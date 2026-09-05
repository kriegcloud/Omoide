from __future__ import annotations

import random
import re
import unicodedata
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Iterable

import numpy as np
from PIL import Image
from sqlalchemy import func, text, true
from sqlalchemy.exc import OperationalError
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlmodel import Session, select

from app.config import settings
from app.models import (
    DatasetCaptionSource,
    DatasetItem,
    Face,
    Media,
    MediaCurationStats,
    MediaTagLink,
    Person,
    PersonMediaLink,
    Tag,
    TrainingDataset,
    TrainingDatasetKind,
)
from app.services.embeddings import (
    cosine_distance,
    decode_embedding as _decode_embedding,
    person_centroid as _person_centroid,
)


DEFAULT_COMPOSITION_TARGETS = {
    "framing": {"close": 0.35, "half": 0.35, "full": 0.30},
    "yaw": {
        "left_profile": 0.10,
        "left_three_quarter": 0.20,
        "frontal": 0.40,
        "right_three_quarter": 0.20,
        "right_profile": 0.10,
    },
}

YAW_BANDS = [
    "left_profile",
    "left_three_quarter",
    "frontal",
    "right_three_quarter",
    "right_profile",
]
ANGLE_LABELS = ["< -45", "-45..-15", "-15..15", "15..45", "> 45"]


def _slugify(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", ascii_value.lower())).strip("-")


def framing_for_ratio(face_ratio: float | None) -> str:
    if face_ratio is None:
        return "none"
    if face_ratio >= 0.12:
        return "closeup"
    if face_ratio >= 0.04:
        return "portrait"
    if face_ratio >= 0.012:
        return "half_body"
    return "full_body"


def aspect_class(width: int | None, height: int | None) -> str:
    if not width or not height:
        return "square"
    ratio = width / height
    if ratio < 0.9:
        return "portrait"
    if ratio > 1.1:
        return "landscape"
    return "square"


def hamming(phash_a: str, phash_b: str) -> int:
    return (int(phash_a, 16) ^ int(phash_b, 16)).bit_count()


def farthest_point_sample(
    vectors: np.ndarray, quality: np.ndarray, target_count: int
) -> list[int]:
    """Return indices selected by deterministic, quality-seeded FPS."""
    points = np.asarray(vectors, dtype=np.float32)
    scores = np.asarray(quality, dtype=np.float32)
    if target_count <= 0 or len(points) == 0:
        return []
    count = min(target_count, len(points))
    selected = [int(np.argmax(scores))]
    minimum = np.linalg.norm(points - points[selected[0]], axis=1)
    minimum[selected[0]] = -1
    while len(selected) < count:
        index = int(np.argmax(minimum))
        selected.append(index)
        minimum = np.minimum(minimum, np.linalg.norm(points - points[index], axis=1))
        minimum[selected] = -1
    return selected


def _face_embedding(session: Session, face_id: int) -> np.ndarray | None:
    try:
        row = session.exec(
            text("SELECT embedding FROM face_embeddings WHERE face_id = :face_id").bindparams(
                face_id=face_id
            )
        ).first()
    except OperationalError:
        return None
    return _decode_embedding(row[0]) if row else None


def _face_area(face: Face) -> int:
    if len(face.bbox) < 4:
        return 0
    return max(0, int(face.bbox[2])) * max(0, int(face.bbox[3]))


def _subject_face(faces: Iterable[Face], person_id: int | None) -> Face | None:
    rows = list(faces)
    assigned = [face for face in rows if person_id is not None and face.person_id == person_id]
    candidates = assigned or rows
    return max(candidates, key=_face_area, default=None)


def _brightness(session: Session, media: Media) -> tuple[float | None, float | None]:
    cached = session.get(MediaCurationStats, media.id)
    if cached:
        return cached.brightness_mean, cached.contrast_std
    if not media.thumbnail_path:
        return None, None
    path = Path(settings.general.thumb_dir) / media.thumbnail_path
    try:
        with Image.open(path) as opened:
            pixels = np.asarray(opened.convert("L"), dtype=np.float32)
    except (OSError, ValueError):
        return None, None
    mean, std = float(pixels.mean()), float(pixels.std())
    # Concurrent analyses (two tabs, a refetch racing the first request) can
    # compute the same media at once; an idempotent insert keeps the second
    # writer from failing the whole request on the primary key.
    session.execute(
        sqlite_insert(MediaCurationStats)
        .values(
            media_id=media.id,
            brightness_mean=mean,
            contrast_std=std,
            computed_at=datetime.now(),
        )
        .on_conflict_do_nothing(index_elements=["media_id"])
    )
    return mean, std


def _quality(metric: dict) -> float:
    return float(metric.get("sharpness") or 0) * float(metric.get("det_score") or 0.5) * float(
        metric.get("resolution") or 0
    )


def kmeans(
    vectors: np.ndarray, cluster_count: int, *, seed: int = 16, max_iterations: int = 100
) -> tuple[np.ndarray, np.ndarray]:
    """Cluster vectors with deterministic NumPy-only k-means."""
    points = np.asarray(vectors, dtype=np.float32)
    if points.ndim != 2 or len(points) == 0:
        return np.empty(0, dtype=np.int64), np.empty((0, 0), dtype=np.float32)
    count = min(max(1, int(cluster_count)), len(points))
    generator = np.random.default_rng(seed)
    centers = points[generator.choice(len(points), size=count, replace=False)].copy()
    labels = np.full(len(points), -1, dtype=np.int64)
    for _ in range(max_iterations):
        distances = np.linalg.norm(points[:, None, :] - centers[None, :, :], axis=2)
        next_labels = np.argmin(distances, axis=1)
        if np.array_equal(next_labels, labels):
            break
        labels = next_labels
        for index in range(count):
            members = points[labels == index]
            if len(members):
                centers[index] = members.mean(axis=0)
    return labels, centers


def _image_embedding(session: Session, media_id: int) -> np.ndarray | None:
    try:
        row = session.exec(
            text(
                "SELECT embedding FROM media_embeddings WHERE media_id = :media_id"
            ).bindparams(media_id=media_id)
        ).first()
    except OperationalError:
        return None
    return _decode_embedding(row[0]) if row else None


def _angle_band(value: float | None, *, semantic: bool = False) -> str | None:
    if value is None:
        return None
    index = int(np.searchsorted([-45, -15, 15, 45], value, side="right"))
    return (YAW_BANDS if semantic else ANGLE_LABELS)[index]


def _composition_framing(framing: str) -> str | None:
    return {
        "closeup": "close",
        "portrait": "close",
        "half_body": "half",
        "full_body": "full",
    }.get(framing)


def _resolution_band(media: Media, buckets: list[int]) -> str:
    longest = max(media.width or 0, media.height or 0)
    ordered = sorted(set(buckets)) or [512, 768, 1024]
    eligible = [bucket for bucket in ordered if bucket <= longest]
    return str(eligible[-1]) if eligible else f"< {ordered[0]}"


def _metric_for_media(
    session: Session, dataset: TrainingDataset, media: Media
) -> dict:
    faces = list(session.exec(select(Face).where(Face.media_id == media.id)).all())
    subject = _subject_face(faces, dataset.person_id)
    image_area = (media.width or 0) * (media.height or 0)
    face_ratio = _face_area(subject) / image_area if subject and image_area else None
    brightness, contrast = _brightness(session, media)
    return {
        "media_id": media.id,
        "face_ratio": face_ratio,
        "framing": framing_for_ratio(face_ratio),
        "other_people": sum(face.person_id != dataset.person_id for face in faces),
        "frontality": subject.frontality if subject else None,
        "yaw": subject.yaw if subject else None,
        "pitch": subject.pitch if subject else None,
        "det_score": subject.det_score if subject else None,
        "sharpness": media.laplacian_score,
        "brightness_mean": brightness,
        "contrast_std": contrast,
        "aspect": aspect_class(media.width, media.height),
        "resolution_bucket": _resolution_band(media, dataset.buckets),
        "resolution": image_area,
        "subject_face": subject,
    }


def _composition_histograms(metrics: list[dict]) -> dict[str, dict[str, int]]:
    dimensions: dict[str, list[str | None]] = {
        "framing": [_composition_framing(metric["framing"]) for metric in metrics],
        "yaw": [_angle_band(metric["yaw"], semantic=True) for metric in metrics],
        "pitch": [_angle_band(metric["pitch"]) for metric in metrics],
        "brightness": [
            _histogram([metric["brightness_mean"]], [64, 128, 192], ["0–64", "64–128", "128–192", "192–255"])
            for metric in metrics
        ],
        "aspect": [metric["aspect"] for metric in metrics],
        "resolution": [metric["resolution_bucket"] for metric in metrics],
    }
    result: dict[str, dict[str, int]] = {}
    for dimension, values in dimensions.items():
        if dimension == "brightness":
            counter: Counter = Counter()
            for histogram in values:
                counter.update({key: count for key, count in histogram.items() if count})
            result[dimension] = dict(counter)
        else:
            result[dimension] = dict(Counter(value for value in values if value is not None))
    for dimension, labels in {
        "framing": ["close", "half", "full"],
        "yaw": YAW_BANDS,
        "pitch": ANGLE_LABELS,
        "brightness": ["0–64", "64–128", "128–192", "192–255"],
        "aspect": ["portrait", "square", "landscape"],
    }.items():
        result[dimension] = {
            label: result.get(dimension, {}).get(label, 0) for label in labels
        }
    return result


def _dataset_clusters(session: Session, metrics: list[dict]) -> list[dict]:
    embedded = [
        (metric, _image_embedding(session, metric["media_id"])) for metric in metrics
    ]
    embedded = [(metric, vector) for metric, vector in embedded if vector is not None]
    if not embedded:
        return []
    points = np.asarray([vector for _, vector in embedded], dtype=np.float32)
    count = min(len(points), max(3, min(12, round(len(metrics) / 40))))
    labels, centers = kmeans(points, count)
    clusters: list[dict] = []
    for index, center in enumerate(centers):
        member_indices = np.flatnonzero(labels == index)
        ordered = sorted(
            member_indices,
            key=lambda member: float(np.linalg.norm(points[member] - center)),
        )
        media_ids = [embedded[member][0]["media_id"] for member in ordered]
        tag_names = session.exec(
            select(Tag.name)
            .join(MediaTagLink, MediaTagLink.tag_id == Tag.id)
            .where(MediaTagLink.media_id.in_(media_ids))
        ).all()
        clusters.append(
            {
                "count": len(member_indices),
                "representative_media_ids": media_ids[:4],
                "top_tags": [name for name, _ in Counter(tag_names).most_common(5)],
            }
        )
    return clusters


def _composition_gaps(
    dataset: TrainingDataset, composition: dict, total: int
) -> list[dict]:
    targets = dataset.composition_targets or DEFAULT_COMPOSITION_TARGETS
    gaps: list[dict] = []
    for dimension, bands in targets.items():
        histogram = composition.get(dimension, {})
        for band, fraction in bands.items():
            have = int(histogram.get(band, 0))
            want = int(round(total * float(fraction)))
            gaps.append(
                {
                    "dimension": dimension,
                    "band": band,
                    "have": have,
                    "want": want,
                    "deficit": max(0, want - have),
                }
            )
    return gaps


def _histogram(values: list[float | int | None], edges: list[float], labels: list[str]) -> dict[str, int]:
    result = dict.fromkeys(labels, 0)
    for value in values:
        if value is None:
            continue
        index = int(np.searchsorted(edges, float(value), side="right"))
        result[labels[index]] += 1
    return result


def _duplicate_groups(metrics: list[dict]) -> list[dict]:
    parent = list(range(len(metrics)))

    def find(value: int) -> int:
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = parent[value]
        return value

    def union(left: int, right: int) -> None:
        a, b = find(left), find(right)
        if a != b:
            parent[b] = a

    for left in range(len(metrics)):
        for right in range(left + 1, len(metrics)):
            a, b = metrics[left], metrics[right]
            if not a.get("phash") or not b.get("phash"):
                continue
            if hamming(a["phash"], b["phash"]) > 6:
                continue
            embeddings = a.get("_embedding"), b.get("_embedding")
            if all(vector is not None for vector in embeddings) and cosine_distance(*embeddings) >= 0.25:
                continue
            union(left, right)
    groups: dict[int, list[dict]] = {}
    for index, metric in enumerate(metrics):
        groups.setdefault(find(index), []).append(metric)
    return [
        {
            "item_ids": [metric["item_id"] for metric in group],
            "best_item_id": max(group, key=_quality)["item_id"],
        }
        for group in groups.values()
        if len(group) > 1
    ]


def compute_item_metrics(
    session: Session,
    dataset: TrainingDataset,
    items: Iterable[DatasetItem],
) -> list[dict]:
    """Compute curation metrics for only the supplied dataset items."""
    centroid = _person_centroid(session, dataset.person_id)
    metrics: list[dict] = []
    for item in items:
        media = session.get(Media, item.media_id)
        if media is None:
            continue
        base = _metric_for_media(session, dataset, media)
        faces = list(session.exec(select(Face).where(Face.media_id == media.id)).all())
        subject = base.pop("subject_face")
        embedding = _face_embedding(session, subject.id) if subject and subject.id else None
        metric = {
            "item_id": item.id,
            **base,
            "identity_distance": (
                cosine_distance(embedding, centroid)
                if embedding is not None and centroid is not None
                else None
            ),
            "duplicate_group": None,
            "phash": media.phash,
            "_embedding": embedding,
        }
        metrics.append(metric)
    session.commit()
    return metrics


def compute_dataset_analysis(session: Session, dataset: TrainingDataset) -> dict:
    items = list(
        session.exec(
            select(DatasetItem)
            .where(DatasetItem.dataset_id == dataset.id)
            .order_by(DatasetItem.position, DatasetItem.id)
        ).all()
    )
    metrics = compute_item_metrics(session, dataset, items)
    duplicates = _duplicate_groups(metrics)
    for group_index, group in enumerate(duplicates, start=1):
        for metric in metrics:
            if metric["item_id"] in group["item_ids"]:
                metric["duplicate_group"] = group_index
    public = [{key: value for key, value in metric.items() if not key.startswith("_") and key not in {"phash", "resolution"}} for metric in metrics]
    summary = {
        "framing": dict(Counter(metric["framing"] for metric in metrics)),
        "aspect": dict(Counter(metric["aspect"] for metric in metrics)),
        "sharpness_hist": _histogram([m["sharpness"] for m in metrics], [25, 75, 150], ["0–25", "25–75", "75–150", "150+"]),
        "frontality_hist": _histogram([m["frontality"] for m in metrics], [0.4, 0.7, 0.9], ["0–0.4", "0.4–0.7", "0.7–0.9", "0.9–1"]),
        "brightness_hist": _histogram([m["brightness_mean"] for m in metrics], [64, 128, 192], ["0–64", "64–128", "128–192", "192–255"]),
        "other_people_hist": dict(Counter(str(metric["other_people"]) for metric in metrics)),
    }
    composition = _composition_histograms(metrics)
    return {
        "items": public,
        "summary": summary,
        "outliers": [metric["item_id"] for metric in metrics if (metric["identity_distance"] or 0) > 0.55],
        "duplicates": duplicates,
        "composition": composition,
        "clusters": _dataset_clusters(session, metrics),
        "gaps": _composition_gaps(dataset, composition, len(metrics)),
    }


def dataset_gaps(session: Session, dataset: TrainingDataset) -> list[dict]:
    analysis = compute_dataset_analysis(session, dataset)
    if dataset.person_id is None:
        return [{**gap, "candidates": []} for gap in analysis["gaps"]]
    existing = set(
        session.exec(
            select(DatasetItem.media_id).where(DatasetItem.dataset_id == dataset.id)
        ).all()
    )
    detected = set(
        session.exec(select(Face.media_id).where(Face.person_id == dataset.person_id)).all()
    )
    linked = set(
        session.exec(
            select(PersonMediaLink.media_id).where(
                PersonMediaLink.person_id == dataset.person_id
            )
        ).all()
    )
    candidates: list[tuple[Media, dict]] = []
    for media_id in detected | linked:
        if media_id in existing:
            continue
        media = session.get(Media, media_id)
        if media is None or media.duration is not None:
            continue
        metric = _metric_for_media(session, dataset, media)
        metric.pop("subject_face", None)
        candidates.append((media, metric))

    result: list[dict] = []
    for gap in analysis["gaps"]:
        dimension, band = gap["dimension"], gap["band"]
        matching = []
        for media, metric in candidates:
            value = metric.get(dimension)
            if dimension == "framing":
                value = _composition_framing(metric["framing"])
            elif dimension == "yaw":
                value = _angle_band(metric["yaw"], semantic=True)
            elif dimension == "pitch":
                value = _angle_band(metric["pitch"])
            elif dimension == "brightness":
                histogram = _histogram(
                    [metric["brightness_mean"]],
                    [64, 128, 192],
                    ["0–64", "64–128", "128–192", "192–255"],
                )
                value = next((label for label, count in histogram.items() if count), None)
            if value == band:
                # Gap quality intentionally ignores frontality: profile gaps
                # would otherwise rank their best candidates last.
                quality = float(metric["sharpness"] or 0) * float(
                    metric["face_ratio"] or 0
                )
                matching.append((quality, int(media.id)))
        matching.sort(key=lambda entry: (-entry[0], entry[1]))
        result.append({**gap, "candidates": [media_id for _, media_id in matching[:24]]})
    session.commit()
    return result


def fill_dataset_gaps(
    session: Session,
    dataset: TrainingDataset,
    *,
    max_add: int,
    dimensions: Iterable[str] | None = None,
) -> list[int]:
    allowed = set(dimensions) if dimensions is not None else None
    gaps = [
        gap
        for gap in dataset_gaps(session, dataset)
        if gap["deficit"] > 0 and (allowed is None or gap["dimension"] in allowed)
    ]
    max_position = session.exec(
        select(func.max(DatasetItem.position)).where(DatasetItem.dataset_id == dataset.id)
    ).one()
    position = int(max_position if max_position is not None else -1) + 1
    added: list[int] = []
    used: set[int] = set()
    offsets = [0] * len(gaps)
    remaining = [int(gap["deficit"]) for gap in gaps]
    while len(added) < max_add and any(value > 0 for value in remaining):
        progressed = False
        for index, gap in enumerate(gaps):
            if len(added) >= max_add or remaining[index] <= 0:
                continue
            candidates = gap["candidates"]
            while offsets[index] < len(candidates) and candidates[offsets[index]] in used:
                offsets[index] += 1
            if offsets[index] >= len(candidates):
                remaining[index] = 0
                continue
            media_id = candidates[offsets[index]]
            offsets[index] += 1
            used.add(media_id)
            added.append(media_id)
            remaining[index] -= 1
            session.add(
                DatasetItem(
                    dataset_id=dataset.id,
                    media_id=media_id,
                    position=position,
                )
            )
            position += 1
            progressed = True
        if not progressed:
            break
    session.commit()
    return added


def auto_select_dataset(
    session: Session,
    dataset: TrainingDataset,
    *,
    target_count: int,
    min_frontality: float | None = None,
    min_sharpness: float | None = None,
    max_other_people: int | None = None,
    drop_duplicates: bool = True,
    dry_run: bool = False,
) -> dict:
    analysis = compute_dataset_analysis(session, dataset)
    by_id = {metric["item_id"]: metric for metric in analysis["items"]}
    allowed = list(by_id)
    if min_frontality is not None:
        allowed = [item_id for item_id in allowed if (by_id[item_id]["frontality"] or 0) >= min_frontality]
    if min_sharpness is not None:
        allowed = [item_id for item_id in allowed if (by_id[item_id]["sharpness"] or 0) >= min_sharpness]
    if max_other_people is not None:
        allowed = [item_id for item_id in allowed if by_id[item_id]["other_people"] <= max_other_people]
    if drop_duplicates:
        losers = {item_id for group in analysis["duplicates"] for item_id in group["item_ids"] if item_id != group["best_item_id"]}
        allowed = [item_id for item_id in allowed if item_id not in losers]

    framing_values = ["closeup", "portrait", "half_body", "full_body", "none"]
    aspect_values = ["portrait", "square", "landscape"]
    embeddings = compute_dataset_embeddings(session, dataset, allowed)
    vectors, quality = [], []
    for item_id in allowed:
        metric = by_id[item_id]
        face = embeddings.get(item_id)
        if face is None:
            face = np.zeros(512, dtype=np.float32)
        norm = float(np.linalg.norm(face))
        if norm:
            face = face / norm
        framing = np.array([0.5 if metric["framing"] == value else 0 for value in framing_values])
        aspect = np.array([0.3 if metric["aspect"] == value else 0 for value in aspect_values])
        brightness = metric["brightness_mean"]
        bucket = min(3, max(0, int((brightness or 0) // 64)))
        bright = np.array([0.2 if index == bucket else 0 for index in range(4)])
        vectors.append(np.concatenate((face, framing, aspect, bright)))
        item = session.get(DatasetItem, item_id)
        media = session.get(Media, item.media_id) if item else None
        resolution = (media.width or 0) * (media.height or 0) if media else 0
        quality.append(
            (metric["sharpness"] or 0)
            * (metric["det_score"] or 0.5)
            * resolution
        )
    chosen_indices = farthest_point_sample(np.asarray(vectors), np.asarray(quality), target_count)
    selected = [allowed[index] for index in chosen_indices]
    excluded = [item_id for item_id in by_id if item_id not in selected]
    if not dry_run:
        for item in session.exec(select(DatasetItem).where(DatasetItem.dataset_id == dataset.id)).all():
            item.excluded = item.id in excluded
            session.add(item)
        session.commit()
    return {"selected_item_ids": selected, "excluded_item_ids": excluded}


def compute_dataset_embeddings(
    session: Session, dataset: TrainingDataset, item_ids: Iterable[int]
) -> dict[int, np.ndarray]:
    wanted = set(item_ids)
    result: dict[int, np.ndarray] = {}
    for item in session.exec(select(DatasetItem).where(DatasetItem.dataset_id == dataset.id)).all():
        if item.id not in wanted:
            continue
        faces = session.exec(select(Face).where(Face.media_id == item.media_id)).all()
        face = _subject_face(faces, dataset.person_id)
        embedding = _face_embedding(session, face.id) if face and face.id else None
        if embedding is not None:
            result[item.id] = embedding
    return result


def build_regularization_dataset(
    session: Session,
    dataset: TrainingDataset,
    *,
    target_count: int,
    gender: str | None = None,
    exclude_person_ids: Iterable[int] = (),
) -> TrainingDataset:
    subject = session.get(Person, dataset.person_id) if dataset.person_id else None
    requested_gender = (gender or (subject.gender if subject else None) or "person").lower()
    aliases = {"woman": "female", "man": "male", "f": "female", "m": "male"}
    normalized = aliases.get(requested_gender, requested_gender)
    excluded = set(exclude_person_ids)
    if dataset.person_id is not None:
        excluded.add(dataset.person_id)
    # One grouped query: media with exactly one detected face, joined to a
    # visible person of the requested gender. Avoids a query per media row.
    single_face = (
        select(Face.media_id, func.min(Face.person_id).label("person_id"))
        .group_by(Face.media_id)
        .having(func.count(Face.id) == 1)
        .subquery()
    )
    rows = session.exec(
        select(Media, Person)
        .join(single_face, single_face.c.media_id == Media.id)
        .join(Person, Person.id == single_face.c.person_id)
        .where(
            Media.duration.is_(None),
            Person.hidden_at.is_(None),
            Person.id.not_in(excluded) if excluded else true(),
        )
    ).all()
    candidates: list[tuple[Media, Person]] = []
    for media, person in rows:
        person_gender = aliases.get((person.gender or "").lower(), (person.gender or "").lower())
        if person_gender != normalized:
            continue
        candidates.append((media, person))
    # Shuffle first, then dedupe only as far as needed for the target count.
    random.Random(dataset.id).shuffle(candidates)
    deduped: list[tuple[Media, Person]] = []
    for candidate in candidates:
        if len(deduped) >= target_count:
            break
        if candidate[0].phash and any(
            existing[0].phash and hamming(candidate[0].phash, existing[0].phash) <= 6
            for existing in deduped
        ):
            continue
        deduped.append(candidate)
    chosen = deduped[:target_count]
    subject_name = subject.name if subject and subject.name else dataset.name
    name = f"{subject_name} — regularization"
    base_slug = _slugify(name) or "regularization"
    slug, suffix = base_slug, 2
    while session.exec(select(TrainingDataset.id).where(TrainingDataset.slug == slug)).first() is not None:
        slug = f"{base_slug}-{suffix}"
        suffix += 1
    class_token = {"female": "woman", "male": "man"}.get(normalized, normalized)
    regularization = TrainingDataset(
        name=name,
        slug=slug,
        kind=TrainingDatasetKind.REGULARIZATION,
        trigger_word="",
        class_token=class_token,
        caption_source=DatasetCaptionSource.TEMPLATE,
        caption_template="{class}",
        target_resolution=dataset.target_resolution,
        buckets=list(dataset.buckets),
        repeats=dataset.repeats,
        export_layout=dataset.export_layout,
    )
    session.add(regularization)
    session.flush()
    for position, (media, _) in enumerate(chosen):
        session.add(DatasetItem(dataset_id=regularization.id, media_id=media.id, position=position))
    dataset.regularization_dataset_id = regularization.id
    session.add(dataset)
    session.commit()
    session.refresh(regularization)
    return regularization
