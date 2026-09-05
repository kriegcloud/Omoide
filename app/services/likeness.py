from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from sqlalchemy import func
from sqlmodel import Session, select

from app.logger import logger
from app.models import TrainingDataset, TrainingRun, TrainingSample
from app.processors.faces import MAX_DET_DIM, FaceProcessor
from app.services.embeddings import normalize_embedding, person_centroid


class LikenessScorer:
    def __init__(self) -> None:
        processor = FaceProcessor()
        processor.load_model()
        self.detector = processor.model

    def score_image(
        self, path: str | Path, centroid: np.ndarray
    ) -> tuple[float | None, int, list[int] | None]:
        image = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if image is None or image.size == 0:
            raise ValueError(f"Could not read training sample image: {path}")
        source_height, source_width = image.shape[:2]
        scale = min(1.0, MAX_DET_DIM / max(source_width, source_height))
        if scale < 1.0:
            detector_image = cv2.resize(
                image,
                (round(source_width * scale), round(source_height * scale)),
                interpolation=cv2.INTER_AREA,
            )
        else:
            detector_image = image

        # The socket adapter follows FaceProcessor's RGB boundary; InsightFace
        # consumes OpenCV BGR images directly.
        if self.detector.__class__.__name__ == "AdaFaceSocketAnalysis":
            detector_image = cv2.cvtColor(detector_image, cv2.COLOR_BGR2RGB)
        faces = list(self.detector.get(detector_image))
        if not faces:
            return None, 0, None

        def area(face: Any) -> float:
            x1, y1, x2, y2 = np.asarray(face.bbox, dtype=np.float32)[:4]
            return float(max(0.0, x2 - x1) * max(0.0, y2 - y1))

        selected = max(faces, key=area)
        embedding = getattr(selected, "normed_embedding", None)
        if embedding is None:
            embedding = getattr(selected, "embedding", None)
        normalized = normalize_embedding(embedding)
        normalized_centroid = normalize_embedding(centroid)
        if normalized is None or normalized_centroid is None:
            raise ValueError("Scored face did not include a valid embedding")

        x1, y1, x2, y2 = np.asarray(selected.bbox, dtype=np.float32)[:4]
        detector_height, detector_width = detector_image.shape[:2]
        x_scale = source_width / detector_width
        y_scale = source_height / detector_height
        left = max(0, min(source_width, round(float(x1) * x_scale)))
        top = max(0, min(source_height, round(float(y1) * y_scale)))
        right = max(left, min(source_width, round(float(x2) * x_scale)))
        bottom = max(top, min(source_height, round(float(y2) * y_scale)))
        likeness = float(np.clip(np.dot(normalized, normalized_centroid), -1.0, 1.0))
        return likeness, len(faces), [left, top, right - left, bottom - top]


_scorer: LikenessScorer | None = None


def _get_scorer() -> LikenessScorer:
    global _scorer
    if _scorer is None:
        _scorer = LikenessScorer()
    return _scorer


def refresh_run_summary(session: Session, run_id: int) -> None:
    run = session.get(TrainingRun, run_id)
    if run is None:
        return
    samples = session.exec(
        select(TrainingSample).where(
            TrainingSample.run_id == run_id,
            TrainingSample.likeness.is_not(None),
        )
    ).all()
    by_step: dict[int, list[float]] = defaultdict(list)
    for sample in samples:
        if sample.likeness is not None:
            by_step[sample.step].append(float(sample.likeness))
    steps = [
        {
            "step": step,
            "mean": sum(values) / len(values),
            "max": max(values),
            "n": len(values),
        }
        for step, values in sorted(by_step.items())
    ]
    best = max(steps, key=lambda entry: (entry["mean"], -entry["step"]), default=None)
    run.likeness_summary = {"steps": steps}
    run.likeness_best_step = int(best["step"]) if best else None
    run.likeness_best = float(best["mean"]) if best else None
    session.add(run)


def score_pending_samples(session: Session, limit: int = 16) -> int:
    pending = list(
        session.exec(
            select(TrainingSample, TrainingRun, TrainingDataset)
            .join(TrainingRun, TrainingSample.run_id == TrainingRun.id)
            .join(TrainingDataset, TrainingRun.dataset_id == TrainingDataset.id)
            .where(TrainingSample.scored_at.is_(None))
            .order_by(TrainingSample.id)
            .limit(limit)
        ).all()
    )
    affected_runs: set[int] = set()
    centroids: dict[int, np.ndarray | None] = {}
    now = datetime.now()
    for sample, run, dataset in pending:
        affected_runs.add(run.id)
        sample.scored_at = now
        if dataset.person_id is None:
            session.add(sample)
            continue
        if dataset.person_id not in centroids:
            centroids[dataset.person_id] = person_centroid(session, dataset.person_id)
        centroid = centroids[dataset.person_id]
        if centroid is None:
            logger.warning(
                "Could not score training sample %s: person %s has no embedding",
                sample.id,
                dataset.person_id,
            )
            session.add(sample)
            continue
        try:
            sample.likeness, sample.face_count, sample.face_bbox = _get_scorer().score_image(
                sample.path, centroid
            )
        except Exception as exc:
            sample.likeness = None
            sample.face_count = None
            sample.face_bbox = None
            logger.warning("Could not score training sample %s: %s", sample.id, exc)
        session.add(sample)
    session.flush()
    for run_id in affected_runs:
        refresh_run_summary(session, run_id)
    session.commit()
    return len(pending)


def likeness_counts(session: Session, run_id: int) -> tuple[int, int]:
    scored = int(
        session.exec(
            select(func.count(TrainingSample.id)).where(
                TrainingSample.run_id == run_id,
                TrainingSample.scored_at.is_not(None),
            )
        ).one()
    )
    pending = int(
        session.exec(
            select(func.count(TrainingSample.id)).where(
                TrainingSample.run_id == run_id,
                TrainingSample.scored_at.is_(None),
            )
        ).one()
    )
    return scored, pending
