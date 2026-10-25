from __future__ import annotations

import math
import time
from collections import deque
from collections.abc import Iterable
import hdbscan
import networkx as nx
from sklearn.decomposition import PCA
from sklearn.neighbors import NearestNeighbors
from tqdm import tqdm
from datetime import UTC, datetime

import numpy as np
from sqlalchemy import func, text, update
from sqlmodel import Session, select

import app.database as db
from app.concurrency import heavy_writer
from app.config import settings
from app.database import safe_commit
from app.persons import set_face_embedding_person
from app.tasks.common import _finish_task, _start_task
from app.logger import logger
from app.models import (
    Face,
    FaceAssignmentSource,
    TimelineEvent,
    Person,
    ProcessingTask,
)
# Keep the private helper names available for existing callers and tests.
from app.services.face_matching import (
    PrototypeIndex,
    _bulk_assign_faces_to_persons,
    _compute_person_prototypes,
    _load_person_prototype_matrix,
    _quality_seed_filter,
    matching_thresholds,
    score_faces,
)
from app.services.face_provenance import face_assignment_values
from app.utils import (
    _distance_to_similarity,
    recalculate_person_appearance_counts,
    refresh_persons,
    remove_person,
    vector_from_stored,
    vector_to_blob,
)
from app.tasks.common import complete_task

from .clustering_algorithms import group_faces_by_cluster, run_clustering
from .clustering_queries import fetch_faces_and_embeddings, get_face_total
from .face_matching import match_remaining_single_faces as upstream_match_remaining_single_faces
from .person_merging import merge_similar_persons as upstream_merge_similar_persons
from .relationships import rebuild_person_relationships
from .state import clear_task_progress, set_task_progress

__all__ = [
    "merge_person_with_similar",
    "merge_similar_persons",
    "rebuild_person_embedding",
    "run_person_clustering",
]


from .clustering_queries import fetch_faces_and_embeddings as _fetch_faces_and_embeddings


from .clustering_algorithms import _cluster_embeddings


from .clustering_algorithms import _trim_clusters_by_centroid_similarity


from .clustering_algorithms import _cluster_embeddings_chinese_whispers


from .clustering_algorithms import _cluster_embeddings_hdbscan


from .clustering_algorithms import group_faces_by_cluster as _group_faces_by_cluster


from .clustering_algorithms import _summarize_labels


from .clustering_algorithms import run_clustering as _run_clustering


def rebuild_person_embedding(session: Session, person_id: int) -> None:
    rows = session.exec(
        text("SELECT embedding FROM face_embeddings WHERE person_id = :pid").bindparams(
            pid=person_id
        )
    ).all()
    vectors: list[np.ndarray] = []
    for (stored,) in rows:
        vec = vector_from_stored(stored)
        if vec is None or vec.size == 0:
            continue
        norm = float(np.linalg.norm(vec))
        if not np.isfinite(norm) or norm == 0.0:
            continue
        vectors.append((vec / norm).astype(np.float32, copy=False))

    # In case the person we try to recalculate the embedding for has no faces,
    # we can safely remove that person, otherwise only remove the person embeddings
    # so it can be recalculated later, when face embeddings are calculated
    if not vectors:
        faces_count_row = session.exec(
            select(func.count(Face.id)).where(Face.person_id == person_id)
        ).one()
        faces_count = (
            int(faces_count_row[0])
            if isinstance(faces_count_row, tuple)
            else int(faces_count_row)
        )
        if faces_count == 0:
            remove_person(person_id, session, reason="empty-after-centroid-rebuild")
        else:
            logger.debug(
                "Skipping centroid rebuild for person %s; no embeddings available yet",
                person_id,
            )
        return

    session.exec(
        text("DELETE FROM person_embeddings WHERE person_id = :pid").bindparams(
            pid=person_id
        )
    )

    centroid = np.vstack(vectors).mean(axis=0)
    norm = float(np.linalg.norm(centroid))
    if norm > 0.0 and np.isfinite(norm):
        centroid = (centroid / norm).astype(np.float32, copy=False)
    else:
        centroid = centroid.astype(np.float32, copy=False)

    blob = vector_to_blob(centroid)
    if blob is not None:
        session.exec(
            text(
                """
                INSERT INTO person_embeddings(person_id, embedding)
                VALUES (:pid, :emb)
                """
            ).bindparams(pid=person_id, emb=blob)
        )


def _merge_person_pair(session: Session, id_a: int, id_b: int) -> tuple[int, int] | None:
    if id_a == id_b:
        return None
    person_a, person_b = session.get(Person, id_a), session.get(Person, id_b)
    if not person_a or not person_b:
        return None
    keep, drop = (person_b, person_a) if int(person_b.appearance_count or 0) > int(person_a.appearance_count or 0) else (person_a, person_b)
    keep_id, drop_id = int(keep.id), int(drop.id)
    from app.persons import merge_person_into
    merge_person_into(session, drop_id, keep_id, assignment_source=FaceAssignmentSource.CLUSTER)
    return keep_id, drop_id


def merge_person_with_similar(
    session: Session,
    person_id: int,
    percent_threshold: float | None = None,
    k: int = 20,
) -> list[int]:
    """
    Merge the specified person with all similar persons whose similarity
    exceeds the configured threshold. Returns the list of person IDs that were
    merged into the surviving record.
    """
    threshold = float(
        percent_threshold
        if percent_threshold is not None
        else getattr(
            settings.face_recognition,
            "person_merge_percent_similarity",
            75.0,
        )
    )

    row = session.exec(
        text(
            "SELECT embedding FROM person_embeddings WHERE person_id = :pid"
        ).bindparams(pid=person_id)
    ).first()
    if not row:
        return []

    vec = vector_from_stored(row[0])
    if vec is None or vec.size == 0:
        return []

    norm = float(np.linalg.norm(vec))
    if not np.isfinite(norm) or norm == 0.0:
        return []

    normed_vec = (vec / norm).astype(np.float32, copy=False)
    blob = vector_to_blob(normed_vec)
    if blob is None:
        return []

    candidates = session.exec(
        text(
            """
            SELECT person_id, distance
              FROM person_embeddings
             WHERE person_id != :pid
               AND embedding MATCH :vec
          ORDER BY distance ASC
          LIMIT :limit_val
            """
        ).bindparams(
            pid=person_id,
            vec=blob,
            limit_val=max(1, int(k)),
        )
    ).all()

    merged_ids: list[int] = []
    current_id = person_id
    for candidate_id, distance in candidates:
        if candidate_id is None:
            continue
        similarity = _distance_to_similarity(distance)
        if float(similarity) < threshold:
            continue
        merge_result = _merge_person_pair(session, current_id, int(candidate_id))
        if merge_result is None:
            continue
        keep_id, dropped_id = merge_result
        merged_ids.append(dropped_id)
        current_id = keep_id

    return merged_ids


def merge_similar_persons(task_id, candidate_person_ids=None):
    from .person_merging import merge_similar_persons as merge
    return merge(task_id, candidate_person_ids)


def _reduce_candidates_by_threshold(percent_threshold, rows):
    candidate_pairs: list[tuple[int, float]] = []
    for candidate_id, distance in rows:
        if candidate_id is None:
            continue
        similarity = _distance_to_similarity(distance)
        if similarity < percent_threshold:
            continue
        candidate_pairs.append((int(candidate_id), similarity))
    return candidate_pairs


def _assign_faces_to_clusters(
    clusters: dict[int, tuple[list[int], list[np.ndarray]]],
    task_id: str,
    *,
    update_progress: bool = True,
) -> tuple[int, list[int]]:
    created = 0
    new_person_ids: list[int] = []
    min_face_count = int(settings.face_recognition.person_min_face_count)
    max_radius = float(settings.face_recognition.person_cluster_max_l2_radius)
    for face_ids, embeddings in clusters.values():
        if len(face_ids) < min_face_count:
            continue

        # Trim outliers instead of discarding the whole cluster: one bad face
        # used to poison an otherwise-good cluster of dozens, sending all of
        # them back to manual assignment. Iterate because dropping outliers
        # shifts the centroid. Trimmed faces stay unassigned and get another
        # chance in the leftover matching pass. Note this check cannot catch
        # junk-hub clusters (they are compact); those are handled by the
        # quality gate at seeding time.
        face_ids_arr = np.asarray(face_ids)
        embeddings_arr = np.array(embeddings)
        trimmed = 0
        for _ in range(5):
            centroid = embeddings_arr.mean(axis=0)
            l2_radii = np.linalg.norm(embeddings_arr - centroid, axis=1)
            keep = l2_radii <= max_radius
            if keep.all():
                break
            trimmed += int(np.sum(~keep))
            face_ids_arr = face_ids_arr[keep]
            embeddings_arr = embeddings_arr[keep]
            if len(face_ids_arr) < min_face_count:
                break
        if len(face_ids_arr) < min_face_count:
            logger.info(
                "Skipping loose cluster (%d of %d faces left after radius trim)",
                len(face_ids_arr),
                len(face_ids),
            )
            continue
        if trimmed:
            logger.info(
                "Trimmed %d outlier faces (radius > %.3f) from cluster of %d",
                trimmed,
                max_radius,
                len(face_ids),
            )
        face_ids = [int(fid) for fid in face_ids_arr]

        with Session(db.engine) as session:
            task = session.get(ProcessingTask, task_id)
            if task and task.status in {"cancelled", "interrupted"}:
                break
            new_person = Person(name=None, appearance_count=0)
            session.add(new_person)
            session.flush()

            # Inference ran outside this transaction. Only claim faces still
            # unassigned at the UPDATE, so a newer human assignment wins.
            claimed_ids = session.exec(
                update(Face)
                .where(Face.id.in_(face_ids), Face.person_id.is_(None))
                .values(**face_assignment_values(new_person.id, FaceAssignmentSource.CLUSTER))
                .returning(Face.id)
            ).scalars().all()
            media_count = int(session.exec(
                select(func.count(func.distinct(Face.media_id)))
                .where(Face.id.in_(claimed_ids))
            ).one() or 0)
            if (len(claimed_ids) < min_face_count
                    or media_count < settings.face_recognition.person_min_media_count):
                session.rollback()
                continue

            claimed_set = set(claimed_ids)
            claimed_embeddings = np.asarray([
                embedding for fid, embedding in zip(face_ids, embeddings_arr)
                if fid in claimed_set
            ])
            face_ids = [fid for fid in face_ids if fid in claimed_set]
            centroid = claimed_embeddings.mean(axis=0)
            similarities = claimed_embeddings @ centroid
            new_person.profile_face_id = face_ids[int(np.argmax(similarities))]
            new_person.appearance_count = media_count
            session.add(new_person)
            for face_id in claimed_ids:
                session.exec(text(
                    "UPDATE face_embeddings SET person_id = :p_id WHERE face_id = :f_id"
                ).bindparams(p_id=new_person.id, f_id=face_id))

            session.exec(
                text(
                    """
                    DELETE FROM person_embeddings WHERE person_id=:p_id
                    """
                ).bindparams(p_id=new_person.id)
            )
            centroid_blob = vector_to_blob(centroid)
            if centroid_blob is None:
                logger.warning(
                    "Unable to serialize centroid for new person %s; skipping embedding"
                    " write",
                    new_person.id,
                )
            else:
                sql_person_emb = text(
                    """
                    INSERT INTO person_embeddings(person_id, embedding)
                    VALUES (:p_id, :emb)
                    """
                ).bindparams(p_id=new_person.id, emb=centroid_blob)
                session.exec(sql_person_emb)

            if update_progress and task is not None:
                task.processed += len(face_ids)
                session.add(task)
            safe_commit(session)
            created += 1
            new_person_ids.append(new_person.id)
    return created, new_person_ids


from .clustering_queries import get_face_total as _get_face_total


def _is_task_cancelled(session: Session, task_id: str) -> bool:
    row = session.exec(
        text(
            """
            SELECT status
              FROM processingtask
             WHERE id = :task_id
            """
        ).bindparams(task_id=task_id)
    ).first()
    if not row:
        return False
    return row[0] in {"cancelled", "interrupted"}


def _match_unassigned_to_existing(session, face_ids, embeddings, task_id, prototypes=None):
    from .face_matching import _match_unassigned_to_existing as match
    return match(session, face_ids, embeddings, task_id, prototypes=prototypes, _hooks=globals())


def _match_remaining_single_faces(task_id, *, batch_size):
    from .face_matching import _match_remaining_single_faces as match
    return match(task_id, batch_size=batch_size, _hooks=globals())


def run_person_clustering(task_id: str) -> None:
    cluster_chunk_size = settings.face_recognition.cluster_batch_size
    total_new_persons = 0
    with Session(db.engine) as session:
        task = session.get(ProcessingTask, task_id)
        if not task:
            logger.error("Task %s not found.", task_id)
            return

        if not _start_task(session, task):
            return
        # count only faces eligible to seed clusters so the progress bar
        # matches what the batch loop actually processes; the quality-excluded
        # remainder is accounted for separately in the matching phase
        task.total = _get_face_total(session, quality_seed_only=True)
        logger.info(
            "Got %s faces to cluster! batch_size=%d",
            task.total,
            settings.face_recognition.cluster_batch_size,
        )
        safe_commit(session)

    def is_cancelled() -> bool:
        with Session(db.engine) as s:
            t = s.get(ProcessingTask, task_id)
            return not t or t.status in {"cancelled", "interrupted"}

    def finalize_cancelled() -> None:
        with Session(db.engine) as session:
            task = session.get(ProcessingTask, task_id)
            if task and task.status == "cancelled" and task.finished_at is None:
                task.finished_at = datetime.now(UTC)
                session.add(task)
                safe_commit(session)
        clear_task_progress(task_id)

    with heavy_writer(name="cluster_persons", cancelled=is_cancelled) as acquired:
        if not acquired:
            finalize_cancelled()
            return

        set_task_progress(
            task_id, current_step="clustering_batches", current_item="starting"
        )
        last_id = 0
        batch_index = 0
        new_person_candidates: list[int] = []
        while True:
            if is_cancelled():
                finalize_cancelled()
                return
            logger.info("--- Starting new Clustering Batch ---")
            with Session(db.engine) as session:
                logger.debug("Continuing from id: %s", last_id)
                batch_face_ids, batch_embeddings = _fetch_faces_and_embeddings(
                    session,
                    last_id=last_id,
                    limit=settings.face_recognition.cluster_batch_size,
                    # only quality faces may seed new persons; low-quality
                    # faces are attached in the leftover matching pass below
                    quality_seed_only=True,
                )
                if not batch_face_ids:
                    break

            if batch_face_ids:
                last_id = batch_face_ids[-1]

            logger.info("Processing batch of %d faces...", len(batch_face_ids))
            batch_index += 1
            batch_started = time.monotonic()
            set_task_progress(
                task_id,
                current_step="clustering_batch",
                current_item=(
                    f"batch {batch_index} ({len(batch_face_ids)} faces, last_id"
                    f" {last_id})"
                ),
            )

            min_faces_required = max(
                2, int(settings.face_recognition.person_min_face_count)
            )
            if len(batch_embeddings) >= min_faces_required:
                if (
                    cluster_chunk_size > 0
                    and len(batch_embeddings) > cluster_chunk_size
                ):
                    total_chunks = math.ceil(len(batch_embeddings) / cluster_chunk_size)
                    logger.info(
                        "Clustering %d unassigned faces in %d chunks (max %d per"
                        " chunk)",
                        len(batch_embeddings),
                        total_chunks,
                        cluster_chunk_size,
                    )
                    total_created = 0
                    total_clusters = 0
                    for chunk_index, start in enumerate(
                        range(0, len(batch_embeddings), cluster_chunk_size), start=1
                    ):
                        if is_cancelled():
                            logger.info("Clustering cancelled mid-chunk.")
                            break
                        end = start + cluster_chunk_size
                        chunk_faces = batch_face_ids[start:end]
                        chunk_embs = batch_embeddings[start:end]
                        set_task_progress(
                            task_id,
                            current_step="clustering_unassigned",
                            current_item=(
                                f"batch {batch_index} chunk {chunk_index}/"
                                f"{total_chunks} ({len(chunk_faces)} faces)"
                            ),
                        )
                        logger.info(
                            "Batch %d chunk %d/%d: running on %d faces",
                            batch_index,
                            chunk_index,
                            total_chunks,
                            len(chunk_faces),
                        )
                        labels, cluster_count, noise_ratio, chunk_elapsed = (
                            _run_clustering(
                                embeddings=chunk_embs,
                                face_ids=chunk_faces,
                            )
                        )
                        logger.info(
                            "Batch %d chunk %d/%d: done in %.1fs (clusters=%d"
                            " noise=%.2f)",
                            batch_index,
                            chunk_index,
                            total_chunks,
                            chunk_elapsed,
                            cluster_count,
                            noise_ratio,
                        )
                        clusters = _group_faces_by_cluster(
                            labels, chunk_faces, chunk_embs
                        )
                        created_count, created_person_ids = _assign_faces_to_clusters(
                            clusters,
                            task_id,
                            update_progress=False,
                        )
                        total_created += created_count
                        total_new_persons += created_count
                        total_clusters += len(clusters)
                        if created_person_ids:
                            new_person_candidates.extend(created_person_ids)
                        set_task_progress(
                            task_id,
                            current_step="clustering_unassigned",
                            current_item=(
                                f"batch {batch_index} chunk {chunk_index}/"
                                f"{total_chunks} created {total_created} persons"
                            ),
                        )
                        with Session(db.engine) as progress_session:
                            task = progress_session.get(ProcessingTask, task_id)
                            if task and task.status == "running":
                                task.processed += len(chunk_faces)
                                progress_session.add(task)
                                safe_commit(progress_session)
                    logger.info(
                        "Cluster batch produced %d new persons out of %d candidate"
                        " clusters",
                        total_created,
                        total_clusters,
                    )
                else:
                    logger.info(
                        "Batch %d: running on %d unassigned faces",
                        batch_index,
                        len(batch_embeddings),
                    )
                    labels, cluster_count, noise_ratio, cluster_elapsed = (
                        _run_clustering(
                            embeddings=batch_embeddings,
                            face_ids=batch_face_ids,
                        )
                    )
                    logger.info(
                        "Batch %d: done in %.1fs (clusters=%d noise=%.2f)",
                        batch_index,
                        cluster_elapsed,
                        cluster_count,
                        noise_ratio,
                    )
                    clusters = _group_faces_by_cluster(
                        labels, batch_face_ids, batch_embeddings
                    )
                    created_count, created_person_ids = _assign_faces_to_clusters(
                        clusters, task_id, update_progress=False
                    )
                    if created_person_ids:
                        new_person_candidates.extend(created_person_ids)
                    total_new_persons += created_count
                    logger.info(
                        "Cluster batch produced %d new persons out of %d candidate"
                        " clusters",
                        created_count,
                        len(clusters),
                    )
                    set_task_progress(
                        task_id,
                        current_step="clustering_unassigned",
                        current_item=(
                            f"batch {batch_index} created {created_count} persons"
                        ),
                    )
            else:
                logger.info(
                    "Batch %d: only %d unassigned faces (< min %d). Skipping"
                    " clustering.",
                    batch_index,
                    len(batch_embeddings),
                    settings.face_recognition.person_min_face_count,
                )
                set_task_progress(
                    task_id,
                    current_step="clustering_unassigned",
                    current_item=(
                        f"batch {batch_index} skipped (only"
                        f" {len(batch_embeddings)} faces)"
                    ),
                )
            if batch_face_ids:
                with Session(db.engine) as session:
                    task = session.get(ProcessingTask, task_id)
                    if task and task.status == "running":
                        task.processed += len(batch_face_ids)
                        session.add(task)
                        safe_commit(session)
            logger.info(
                "Finished batch %d in %.1fs",
                batch_index,
                time.monotonic() - batch_started,
            )
            if len(batch_face_ids) < settings.face_recognition.cluster_batch_size:
                break
            set_task_progress(
                task_id,
                current_step="clustering_batches",
                current_item=f"waiting for next batch after id {last_id}",
            )
        logger.info(
            "Starting merge of similar persons (%d candidates)",
            len(new_person_candidates),
        )
        merged_persons = merge_similar_persons(task_id, new_person_candidates)
        if is_cancelled():
            logger.info("Clustering task %s cancelled before leftover matching.", task_id)
            finalize_cancelled()
            return
        logger.info("Starting leftover single-face match pass after clustering")
        (
            matched_leftovers,
            scanned_leftovers,
            cancelled_during_match,
        ) = (
            _match_remaining_single_faces(
                task_id,
                batch_size=settings.face_recognition.cluster_batch_size,
            )
        )
        logger.info(
            "Leftover single-face pass finished: scanned=%d matched=%d",
            scanned_leftovers,
            matched_leftovers,
        )
        if cancelled_during_match or is_cancelled():
            logger.info("Clustering task %s cancelled during matching phase.", task_id)
            finalize_cancelled()
            return

        with Session(db.engine) as session:
            task = session.get(ProcessingTask, task_id)
            logger.info("FINISHED CLUSTERING!")
            task.result = {
                **(task.result or {}),
                "new_persons": total_new_persons,
                "matched": matched_leftovers,
                "merged": merged_persons,
            }
            session.add(task)
            if not _finish_task(session, task, "completed"):
                clear_task_progress(task_id)
                return
    clear_task_progress(task_id)
    rebuild_person_relationships()
