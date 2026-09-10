from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

import numpy as np
from sqlalchemy import text, update
from sqlmodel import Session, select

from app.config import settings
from app.database import safe_commit
from app.logger import logger
from app.models import Face, FaceAssignmentSource, FaceSuggestionRejection, Person
from app.services.face_provenance import face_assignment_values
from app.utils import (
    recalculate_person_appearance_counts,
    update_person_embedding,
    vector_from_stored,
)


@dataclass(frozen=True)
class PrototypeIndex:
    person_ids: np.ndarray
    matrix: np.ndarray
    names: dict[int, str]


@dataclass(frozen=True)
class FaceMatch:
    face_id: int
    person_id: int
    score: float
    margin: float
    pose_bin: str


def load_prototype_index(session: Session) -> PrototypeIndex:
    """Build a fresh index for this request or matching pass; never cache globally."""
    person_ids, matrix = _load_person_prototype_matrix(session)
    names = {
        person_id: name or f"Person {person_id}"
        for person_id, name in session.exec(select(Person.id, Person.name)).all()
    }
    return PrototypeIndex(person_ids, matrix, names)


def _pose_bin(frontality: float | None) -> str:
    if frontality is None or not np.isfinite(frontality):
        return "unknown"
    if frontality >= 0.5:
        return "frontal"
    return "quarter" if frontality >= 0.25 else "profile"


def load_suggestion_rejections(
    session: Session, face_ids: list[int]
) -> dict[int, set[int]]:
    """Load explicit review decisions only for the faces being scored."""
    rejected: dict[int, set[int]] = {}
    for chunk in _iter_chunks(sorted(set(face_ids)), 500):
        rows = session.exec(
            select(FaceSuggestionRejection.face_id, FaceSuggestionRejection.person_id)
            .where(FaceSuggestionRejection.face_id.in_(chunk))
        ).all()
        for face_id, person_id in rows:
            rejected.setdefault(face_id, set()).add(person_id)
    return rejected


def score_faces(
    index: PrototypeIndex,
    face_ids: list[int],
    embeddings: np.ndarray,
    frontalities: list[float | None],
    *,
    rejected_persons: dict[int, set[int]] | None = None,
) -> list[FaceMatch]:
    """Score valid faces against normalized prototypes, without database writes.

    The runner-up must belong to a different person, even when the winning
    person has multiple prototypes. With only one person its score is -1.
    """
    if (
        not face_ids
        or index.matrix.size == 0
        or embeddings.ndim != 2
        or index.matrix.ndim != 2
        or embeddings.shape[1] != index.matrix.shape[1]
    ):
        return []
    matches: list[FaceMatch] = []
    for start in range(0, len(face_ids), 512):
        chunk = embeddings[start : start + 512].astype(np.float32, copy=False)
        norms = np.linalg.norm(chunk, axis=1)
        valid = np.flatnonzero(np.isfinite(norms) & (norms > 0.0))
        if not len(valid):
            continue
        similarities = (chunk[valid] / norms[valid, None]) @ index.matrix.T
        similarities = np.clip(similarities, -1.0, 1.0)
        if rejected_persons:
            for row, local_index in enumerate(valid):
                excluded = rejected_persons.get(int(face_ids[start + int(local_index)]))
                if excluded:
                    similarities[row, np.isin(index.person_ids, list(excluded))] = -np.inf
        best_indices = np.argmax(similarities, axis=1)
        best_scores = similarities[np.arange(len(valid)), best_indices]
        best_persons = index.person_ids[best_indices]
        other_scores = np.where(
            index.person_ids[None, :] == best_persons[:, None],
            -np.inf,
            similarities,
        ).max(axis=1)
        other_scores = np.where(np.isfinite(other_scores), other_scores, -1.0)
        for row, local_index in enumerate(valid):
            if not np.isfinite(best_scores[row]):
                continue
            offset = start + int(local_index)
            matches.append(
                FaceMatch(
                    face_id=int(face_ids[offset]),
                    person_id=int(best_persons[row]),
                    score=float(best_scores[row]),
                    margin=float(best_scores[row] - other_scores[row]),
                    pose_bin=_pose_bin(frontalities[offset]),
                )
            )
    return matches


def load_unassigned_face_embeddings(
    session: Session, face_ids: list[int] | None = None
) -> tuple[list[int], np.ndarray, list[float | None]]:
    """Load all or a bounded set of orphan vectors, in stable face-id order."""
    ids: list[int] = []
    vectors: list[np.ndarray] = []
    frontalities: list[float | None] = []
    chunks = [None] if face_ids is None else _iter_chunks(sorted(set(face_ids)), 500)
    for chunk in chunks:
        clause, params = "", {}
        if chunk is not None:
            placeholders, params = _build_in_clause_params(chunk, prefix="f")
            clause = f"AND f.id IN ({placeholders})"
        rows = session.exec(
            text(
                f"""
                SELECT f.id, fe.embedding, f.frontality
                  FROM face AS f
                  JOIN face_embeddings AS fe ON fe.face_id = f.id
                 WHERE f.person_id IS NULL {clause}
                 ORDER BY f.id
                """
            ).bindparams(**params)
        ).all()
        for face_id, raw_embedding, frontality in rows:
            vector = vector_from_stored(raw_embedding)
            if vector is None or vector.size == 0:
                continue
            ids.append(int(face_id))
            vectors.append(vector)
            frontalities.append(frontality)
    matrix = np.vstack(vectors) if vectors else np.empty((0, 0), dtype=np.float32)
    return ids, matrix, frontalities


def matching_thresholds() -> tuple[float, float]:
    threshold = float(settings.face_recognition.existing_person_cosine_threshold)
    if threshold <= 0.0 or threshold > 1.0:
        threshold = (
            float(settings.face_recognition.person_merge_percent_similarity) / 100.0
        )
    return float(np.clip(threshold, 0.0, 1.0)), max(
        0.0, float(settings.face_recognition.existing_person_min_cosine_margin)
    )


def match_faces_to_persons(
    session: Session,
    face_ids: list[int],
    *,
    threshold: float,
    min_margin: float,
    index: PrototypeIndex | None = None,
) -> dict[int, int]:
    """Attach eligible orphans and refresh the affected people's derived data."""
    if not face_ids:
        return {}
    if index is None:
        index = load_prototype_index(session)
    if not index.matrix.size:
        return {}
    ids, embeddings, frontalities = load_unassigned_face_embeddings(session, face_ids)
    assignments = {
        match.face_id: match.person_id
        for match in score_faces(
            index, ids, embeddings, frontalities,
            rejected_persons=load_suggestion_rejections(session, ids),
        )
        if match.score >= threshold and match.margin >= min_margin
    }
    if assignments:
        assignments = _bulk_assign_faces_to_persons(session, assignments)
        person_ids = set(assignments.values())
        recalculate_person_appearance_counts(session, person_ids)
        for person_id in sorted(person_ids):
            update_person_embedding(session, person_id)
        safe_commit(session)
    return assignments


def _quality_seed_filter() -> tuple[str, dict[str, float]]:
    """
    SQL fragment (aliased on ``f``) excluding low-confidence and hard-profile
    faces from seeding new clusters: their embeddings are unreliable and tend
    to agglomerate into junk "people". They stay unassigned and are picked up
    by the leftover matching pass instead. Faces without quality data (NULL,
    extracted before the quality columns existed) are kept.
    """
    return (
        """
           AND (f.det_score IS NULL OR f.det_score >= :min_det_score)
           AND (f.frontality IS NULL OR f.frontality >= :min_frontality)
        """,
        {
            "min_det_score": float(
                settings.face_recognition.cluster_seed_min_det_score
            ),
            "min_frontality": float(
                settings.face_recognition.cluster_seed_min_frontality
            ),
        },
    )


def _iter_chunks(values: list[int], chunk_size: int) -> Iterable[list[int]]:
    size = max(1, int(chunk_size))
    for i in range(0, len(values), size):
        yield values[i : i + size]


def _build_in_clause_params(
    values: list[int], *, prefix: str
) -> tuple[str, dict[str, int]]:
    placeholders: list[str] = []
    params: dict[str, int] = {}
    for idx, value in enumerate(values):
        key = f"{prefix}{idx}"
        placeholders.append(f":{key}")
        params[key] = int(value)
    return ", ".join(placeholders), params


def _compute_person_prototypes(vectors: np.ndarray, k: int) -> np.ndarray:
    """
    Reduce a person's face embeddings (L2-normalized, shape (n, d)) to up to
    ``k`` prototype vectors via farthest-point seeding + a few spherical
    k-means iterations. Multiple prototypes capture pose modes (frontal,
    left/right profile) that a single centroid — dominated by frontal shots —
    washes out.
    """

    def _norm_rows(matrix: np.ndarray) -> np.ndarray:
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms[norms == 0.0] = 1.0
        return matrix / norms

    centroid = _norm_rows(vectors.mean(axis=0, keepdims=True))
    if k <= 1 or vectors.shape[0] <= 1:
        return centroid.astype(np.float32, copy=False)

    centers = [centroid[0]]
    for _ in range(k - 1):
        sims = vectors @ np.vstack(centers).T
        best_sim = sims.max(axis=1)
        centers.append(vectors[int(np.argmin(best_sim))])
    centers_arr = np.vstack(centers)

    for _ in range(4):
        assignment = np.argmax(vectors @ centers_arr.T, axis=1)
        for j in range(centers_arr.shape[0]):
            members = vectors[assignment == j]
            if members.shape[0]:
                centers_arr[j] = members.mean(axis=0)
        centers_arr = _norm_rows(centers_arr)

    return centers_arr.astype(np.float32, copy=False)


def _load_person_prototype_matrix(
    session: Session,
    *,
    per_person_cap: int | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Build the matching matrix from per-person prototypes computed over the
    persons' face embeddings (instead of the single stored centroid). Returns
    (person_ids, matrix) where person_ids has one entry per prototype row and
    may contain the same person several times.
    """
    max_prototypes = max(
        1,
        int(
            getattr(settings.face_recognition, "person_matching_max_prototypes", 3)
        ),
    )
    if per_person_cap is None:
        per_person_cap = int(
            getattr(settings.face_recognition, "person_prototype_sample_cap", 64)
        )
    per_person_cap = max(1, int(per_person_cap))
    pose_prototypes_enabled = bool(
        getattr(settings.face_recognition, "person_pose_prototypes_enabled", True)
    )
    pose_min_faces = max(
        1,
        int(
            getattr(
                settings.face_recognition, "person_pose_prototype_min_faces", 4
            )
        ),
    )
    pose_sample_cap = max(
        1,
        int(
            getattr(
                settings.face_recognition, "person_pose_prototype_sample_cap", 256
            )
        ),
    )
    # Prefer quality faces for the prototypes so junk members (blurry/profile
    # faces mistakenly attached to a person) do not become attachment anchors.
    # Persons with no quality-rated faces fall back to all of their faces.
    quality_filter, quality_params = _quality_seed_filter()
    rows = session.exec(
        text(
            f"""
            SELECT f.person_id, f.frontality, fe.embedding,
                   CASE WHEN 1=1 {quality_filter} THEN 1 ELSE 0 END AS is_quality
              FROM face            AS f
              JOIN face_embeddings AS fe ON fe.face_id = f.id
             WHERE f.person_id IS NOT NULL
             ORDER BY f.id
            """
        ).bindparams(**quality_params)
    ).all()

    quality_grouped: dict[int, list[np.ndarray]] = {}
    fallback_grouped: dict[int, list[np.ndarray]] = {}
    pose_grouped: dict[int, dict[str, list[np.ndarray]]] = {}
    for person_id, frontality, raw_embedding, is_quality in rows:
        if person_id is None:
            continue
        vec = vector_from_stored(raw_embedding)
        if vec is None or vec.size == 0:
            continue
        if not np.all(np.isfinite(vec)) or not np.any(vec):
            continue
        norm = float(np.linalg.norm(vec))
        if not np.isfinite(norm) or norm == 0.0:
            continue
        person_id = int(person_id)
        normalized = (vec / norm).astype(np.float32, copy=False)
        target = quality_grouped if is_quality else fallback_grouped
        target.setdefault(person_id, []).append(normalized)

        if frontality is not None:
            frontality = float(frontality)
            if not np.isfinite(frontality):
                continue
            if frontality >= 0.5:
                pose_bin = "frontal"
            elif frontality >= 0.25:
                pose_bin = "quarter"
            else:
                pose_bin = "profile"
            pose_grouped.setdefault(person_id, {}).setdefault(
                pose_bin, []
            ).append(normalized)

    grouped: dict[int, list[np.ndarray]] = dict(fallback_grouped)
    grouped.update(
        {pid: vecs for pid, vecs in quality_grouped.items() if vecs}
    )

    proto_person_ids: list[int] = []
    proto_vectors: list[np.ndarray] = []
    prototype_counts = {"frontal": 0, "quarter": 0, "profile": 0, "kmeans": 0}
    all_person_ids = sorted(set(grouped) | set(pose_grouped))
    for person_id in all_person_ids:
        vecs = grouped.get(person_id, [])
        rng = np.random.default_rng(person_id)
        if len(vecs) > per_person_cap:
            indices = rng.choice(len(vecs), size=per_person_cap, replace=False)
            sampled = np.vstack([vecs[int(index)] for index in indices])
        else:
            sampled = np.vstack(vecs)

        # only spend extra prototypes on persons with enough faces to
        # actually exhibit distinct pose modes (~4 faces per prototype)
        k = min(max_prototypes, max(1, sampled.shape[0] // 4))
        for proto in _compute_person_prototypes(sampled, k):
            proto_person_ids.append(person_id)
            proto_vectors.append(proto)
            prototype_counts["kmeans"] += 1

        if not pose_prototypes_enabled:
            continue

        for pose_bin in ("frontal", "quarter", "profile"):
            bin_vecs = pose_grouped.get(person_id, {}).get(pose_bin, [])
            if len(bin_vecs) < pose_min_faces:
                continue
            if len(bin_vecs) > pose_sample_cap:
                indices = rng.choice(
                    len(bin_vecs), size=pose_sample_cap, replace=False
                )
                pose_sample = np.vstack(
                    [bin_vecs[int(index)] for index in indices]
                )
            else:
                pose_sample = np.vstack(bin_vecs)
            pose_mean = pose_sample.mean(axis=0)
            pose_norm = float(np.linalg.norm(pose_mean))
            if not np.isfinite(pose_norm) or pose_norm == 0.0:
                continue
            proto_person_ids.append(person_id)
            proto_vectors.append(
                (pose_mean / pose_norm).astype(np.float32, copy=False)
            )
            prototype_counts[pose_bin] += 1

    logger.info(
        "prototypes: %d persons, %d rows "
        "(frontal=%d quarter=%d profile=%d kmeans=%d)",
        len(all_person_ids),
        len(proto_person_ids),
        prototype_counts["frontal"],
        prototype_counts["quarter"],
        prototype_counts["profile"],
        prototype_counts["kmeans"],
    )

    if not proto_person_ids:
        return np.array([], dtype=np.int64), np.empty((0, 0), dtype=np.float32)

    return np.array(proto_person_ids, dtype=np.int64), np.vstack(
        proto_vectors
    ).astype(np.float32, copy=False)


def _bulk_assign_faces_to_persons(
    session: Session,
    assignments: dict[int, int],
    *,
    chunk_size: int = 500,
) -> dict[int, int]:
    if not assignments:
        return {}

    claimed: dict[int, int] = {}
    by_person_id: dict[int, list[int]] = {}
    for face_id, person_id in assignments.items():
        by_person_id.setdefault(int(person_id), []).append(int(face_id))

    for person_id, person_face_ids in by_person_id.items():
        for face_chunk in _iter_chunks(person_face_ids, chunk_size):
            claimed_ids = session.exec(
                update(Face).where(
                    Face.id.in_(face_chunk), Face.person_id.is_(None),
                    ~select(FaceSuggestionRejection.face_id).where(
                        FaceSuggestionRejection.face_id == Face.id,
                        FaceSuggestionRejection.person_id == person_id,
                    ).exists(),
                ).values(**face_assignment_values(person_id, FaceAssignmentSource.AUTO_MATCH))
                .returning(Face.id)
            ).scalars().all()
            if not claimed_ids:
                continue
            claimed.update({int(face_id): person_id for face_id in claimed_ids})
            placeholders, params = _build_in_clause_params(claimed_ids, prefix="f")

            sql_face_embedding = text(
                "UPDATE face_embeddings SET person_id = :pid"
                f" WHERE face_id IN ({placeholders})"
            ).bindparams(pid=person_id, **params)
            session.exec(sql_face_embedding)
    return claimed
