"""Candidate discovery and selection for mining stills from subject videos."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import cv2
import imagehash
import numpy as np
from PIL import Image
from sqlmodel import Session, select

from app.config import settings
from app.models import DatasetItem, Face, Media, Scene, TrainingDataset
from app.processors.faces import MAX_DET_DIM, FaceProcessor
from app.services.curation import farthest_point_sample, hamming
from app.services.embeddings import normalize_embedding, person_centroid
from app.services.media_files import require_writable_media_file


IDENTITY_THRESHOLD = 0.45
TIMESTAMP_DEDUPE_SECONDS = 0.25


@dataclass
class FrameCandidate:
    timestamp: float
    frame: np.ndarray
    likeness: float
    bbox: list[int]
    yaw: float | None
    pitch: float | None
    sharpness: float
    face_size: float
    phash: str
    novelty: float = 1.0
    score: float = 0.0

    @property
    def timestamp_ms(self) -> int:
        return max(0, round(self.timestamp * 1000))

    def preview_dict(self) -> dict[str, Any]:
        preview = self.frame
        height, width = preview.shape[:2]
        if max(height, width) > 360:
            scale = 360 / max(height, width)
            preview = cv2.resize(
                preview,
                (max(1, round(width * scale)), max(1, round(height * scale))),
                interpolation=cv2.INTER_AREA,
            )
        ok, encoded = cv2.imencode(".jpg", preview, [cv2.IMWRITE_JPEG_QUALITY, 82])
        data_url = None
        if ok:
            data_url = "data:image/jpeg;base64," + base64.b64encode(encoded).decode(
                "ascii"
            )
        return {
            "timestamp": self.timestamp,
            "timestamp_ms": self.timestamp_ms,
            "likeness": self.likeness,
            "bbox": self.bbox,
            "yaw": self.yaw,
            "pitch": self.pitch,
            "sharpness": self.sharpness,
            "face_size": self.face_size,
            "phash": self.phash,
            "novelty": self.novelty,
            "score": self.score,
            "preview_data_url": data_url,
        }


def _dedupe_timestamps(values: Iterable[float], limit: int) -> list[float]:
    result: list[float] = []
    for value in sorted(max(0.0, float(item)) for item in values):
        if result and value - result[-1] < TIMESTAMP_DEDUPE_SECONDS:
            continue
        result.append(value)
        if len(result) >= limit:
            break
    return result


def candidate_timestamps(
    session: Session,
    video: Media,
    person_id: int,
    *,
    fps: float = 2.0,
    max_candidates: int = 48,
) -> list[float]:
    """Merge subject face timestamps with samples from their containing scenes."""
    if fps <= 0:
        raise ValueError("fps must be greater than zero")
    if max_candidates <= 0:
        return []
    duration = max(0.0, float(video.duration or 0.0))
    face_times = [
        float(value)
        for value in session.exec(
            select(Face.timestamp).where(
                Face.media_id == video.id,
                Face.person_id == person_id,
                Face.timestamp.is_not(None),
            )
        ).all()
        if value is not None and float(value) >= 0.0
    ]
    samples: list[float] = list(face_times)
    scenes = session.exec(
        select(Scene).where(Scene.media_id == video.id).order_by(Scene.start_time)
    ).all()
    interval = 1.0 / fps
    for scene in scenes:
        start = max(0.0, float(scene.start_time))
        end = min(duration, float(scene.end_time)) if duration else float(scene.end_time)
        if end < start or not any(start <= value <= end for value in face_times):
            continue
        value = start
        while value <= end + 1e-9:
            samples.append(value)
            value += interval
    if duration:
        samples = [min(value, duration) for value in samples]
    return _dedupe_timestamps(samples, max_candidates)


def _phash(frame: np.ndarray) -> str:
    return str(imagehash.phash(Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))))


def _phash_bits(value: str) -> np.ndarray:
    width = len(value) * 4
    return np.asarray(
        [float(bit) for bit in f"{int(value, 16):0{width}b}"], dtype=np.float32
    )


def _pose_vector(yaw: float | None, pitch: float | None) -> np.ndarray:
    return np.asarray(
        [float(yaw or 0.0) / 90.0, float(pitch or 0.0) / 90.0],
        dtype=np.float32,
    )


def _selection_vector(candidate: FrameCandidate) -> np.ndarray:
    return np.concatenate(
        (_pose_vector(candidate.yaw, candidate.pitch), _phash_bits(candidate.phash))
    )


class FrameMiner:
    """Decode frames and run the same detector model used by likeness scoring."""

    def __init__(self) -> None:
        processor = FaceProcessor()
        processor.load_model()
        self.processor = processor
        self.detector = processor.model

    def _detect(self, frame: np.ndarray) -> tuple[list[Any], float, float]:
        source_height, source_width = frame.shape[:2]
        scale = min(1.0, MAX_DET_DIM / max(source_width, source_height))
        if scale < 1.0:
            detector_frame = cv2.resize(
                frame,
                (round(source_width * scale), round(source_height * scale)),
                interpolation=cv2.INTER_AREA,
            )
        else:
            detector_frame = frame
        inference_frame = detector_frame
        if self.detector.__class__.__name__ == "AdaFaceSocketAnalysis":
            inference_frame = cv2.cvtColor(inference_frame, cv2.COLOR_BGR2RGB)
        faces = list(self.detector.get(inference_frame))
        return (
            faces,
            source_width / detector_frame.shape[1],
            source_height / detector_frame.shape[0],
        )

    def candidate_from_frame(
        self,
        frame: np.ndarray,
        timestamp: float,
        centroid: np.ndarray,
        *,
        min_face_px: int = 160,
    ) -> FrameCandidate | None:
        faces, x_scale, y_scale = self._detect(frame)
        normalized_centroid = normalize_embedding(centroid)
        if normalized_centroid is None:
            raise ValueError("Person has no valid face centroid")
        matches: list[tuple[float, float, Any, np.ndarray]] = []
        for face in faces:
            embedding = getattr(face, "normed_embedding", None)
            if embedding is None:
                embedding = getattr(face, "embedding", None)
            normalized = (
                normalize_embedding(np.asarray(embedding, dtype=np.float32))
                if embedding is not None
                else None
            )
            if normalized is None:
                continue
            likeness = float(
                np.clip(np.dot(normalized, normalized_centroid), -1.0, 1.0)
            )
            if likeness < IDENTITY_THRESHOLD:
                continue
            x1, y1, x2, y2 = np.asarray(face.bbox, dtype=np.float32)[:4]
            width = max(0.0, float(x2 - x1) * x_scale)
            height = max(0.0, float(y2 - y1) * y_scale)
            if min(width, height) < min_face_px:
                continue
            matches.append(
                (
                    width * height,
                    likeness,
                    face,
                    np.asarray([x1, y1, x2, y2]),
                )
            )
        if not matches:
            return None
        _, likeness, selected, bbox = max(matches, key=lambda entry: entry[0])
        source_height, source_width = frame.shape[:2]
        left = max(0, min(source_width, round(float(bbox[0]) * x_scale)))
        top = max(0, min(source_height, round(float(bbox[1]) * y_scale)))
        right = max(left, min(source_width, round(float(bbox[2]) * x_scale)))
        bottom = max(top, min(source_height, round(float(bbox[3]) * y_scale)))
        keypoints = getattr(selected, "kps", None)
        yaw, pitch = self.processor._estimate_pose(keypoints)
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        face_size = float(np.sqrt((right - left) * (bottom - top)))
        return FrameCandidate(
            timestamp=timestamp,
            frame=frame,
            likeness=likeness,
            bbox=[left, top, right - left, bottom - top],
            yaw=yaw,
            pitch=pitch,
            sharpness=sharpness,
            face_size=face_size,
            phash=_phash(frame),
        )

    def decode_candidates(
        self,
        path: Path,
        timestamps: Iterable[float],
        centroid: np.ndarray,
        *,
        min_face_px: int = 160,
    ) -> list[FrameCandidate]:
        capture = cv2.VideoCapture(str(path))
        if not capture.isOpened():
            capture.release()
            raise ValueError(f"Could not open video: {path}")
        candidates: list[FrameCandidate] = []
        try:
            for timestamp in timestamps:
                capture.set(cv2.CAP_PROP_POS_MSEC, float(timestamp) * 1000.0)
                ok, frame = capture.read()
                if not ok or frame is None or frame.size == 0:
                    continue
                candidate = self.candidate_from_frame(
                    frame, float(timestamp), centroid, min_face_px=min_face_px
                )
                if candidate is not None:
                    candidates.append(candidate)
        finally:
            capture.release()
        return candidates


def score_candidates(
    candidates: list[FrameCandidate],
    existing: Iterable[tuple[float | None, float | None, str | None]],
) -> list[FrameCandidate]:
    references = list(existing)
    for candidate in candidates:
        if not references:
            novelty = 1.0
        else:
            pose = _pose_vector(candidate.yaw, candidate.pitch)
            pose_distances = [
                min(1.0, float(np.linalg.norm(pose - _pose_vector(yaw, pitch))) / 2.0)
                for yaw, pitch, _ in references
            ]
            hash_distances = [
                hamming(candidate.phash, value) / (len(candidate.phash) * 4)
                for _, _, value in references
                if value
            ]
            pose_novelty = min(pose_distances, default=1.0)
            hash_novelty = min(hash_distances, default=1.0)
            novelty = max(0.01, (pose_novelty + hash_novelty) / 2.0)
        candidate.novelty = novelty
        frame_height, frame_width = candidate.frame.shape[:2]
        face_size_factor = candidate.face_size / max(frame_width, frame_height)
        candidate.score = candidate.sharpness * face_size_factor * novelty
    return candidates


def select_candidates(
    candidates: list[FrameCandidate], max_per_video: int
) -> list[FrameCandidate]:
    if not candidates or max_per_video <= 0:
        return []
    vectors = np.stack([_selection_vector(candidate) for candidate in candidates])
    quality = np.asarray(
        [candidate.score for candidate in candidates], dtype=np.float32
    )
    indices = farthest_point_sample(vectors, quality, max_per_video)
    return [candidates[index] for index in indices]


def existing_dataset_features(
    session: Session, dataset: TrainingDataset
) -> list[tuple[float | None, float | None, str | None]]:
    result: list[tuple[float | None, float | None, str | None]] = []
    items = session.exec(
        select(DatasetItem).where(DatasetItem.dataset_id == dataset.id)
    ).all()
    for item in items:
        media = session.get(Media, item.media_id)
        if media is None:
            continue
        face = None
        if dataset.person_id is not None:
            faces = session.exec(
                select(Face).where(
                    Face.media_id == media.id, Face.person_id == dataset.person_id
                )
            ).all()
            face = max(
                faces,
                key=lambda value: value.bbox[2] * value.bbox[3],
                default=None,
            )
        result.append(
            (face.yaw if face else None, face.pitch if face else None, media.phash)
        )
    return result


def frame_count_for_video(session: Session, dataset_id: int, video: Media) -> int:
    prefix = f"{Path(video.path).stem}_frame-"
    parent = Path(video.path).parent.resolve(strict=False)
    count = 0
    for item in session.exec(
        select(DatasetItem).where(
            DatasetItem.dataset_id == dataset_id,
            DatasetItem.origin == "frame",
        )
    ).all():
        media = session.get(Media, item.media_id)
        if media is None:
            continue
        path = Path(media.path)
        if (
            path.name.startswith(prefix)
            and path.parent.resolve(strict=False) == parent
        ):
            count += 1
    return count


def dataset_videos(session: Session, dataset: TrainingDataset) -> list[dict[str, Any]]:
    if dataset.person_id is None:
        return []
    videos = list(
        session.exec(
            select(Media)
            .join(Face, Face.media_id == Media.id)
            .where(
                Face.person_id == dataset.person_id,
                Media.duration.is_not(None),
            )
            .distinct()
            .order_by(Media.created_at.desc(), Media.id.desc())
        ).all()
    )
    result: list[dict[str, Any]] = []
    for video in videos:
        detected_face_count = len(
            session.exec(
                select(Face.id).where(
                    Face.media_id == video.id,
                    Face.person_id == dataset.person_id,
                )
            ).all()
        )
        result.append(
            {
                "media_id": video.id,
                "filename": video.filename,
                "duration": video.duration,
                "thumbnail_path": video.thumbnail_path,
                "detected_face_count": detected_face_count,
                "already_mined_count": frame_count_for_video(
                    session, int(dataset.id), video
                ),
            }
        )
    return result


def mine_candidates(
    session: Session,
    dataset: TrainingDataset,
    video: Media,
    *,
    fps: float = 2.0,
    min_face_px: int = 160,
    max_candidates: int = 48,
    timestamps: Iterable[float] | None = None,
    miner: FrameMiner | None = None,
) -> list[FrameCandidate]:
    if dataset.person_id is None:
        raise ValueError("Dataset has no subject person")
    path = require_writable_media_file(
        video.path, settings.general.resolved_media_dirs()
    )
    centroid = person_centroid(session, dataset.person_id)
    if centroid is None:
        raise ValueError("Dataset person has no face centroid")
    values = list(timestamps) if timestamps is not None else candidate_timestamps(
        session,
        video,
        dataset.person_id,
        fps=fps,
        max_candidates=max_candidates,
    )
    values = _dedupe_timestamps(values, max_candidates)
    decoded = (miner or FrameMiner()).decode_candidates(
        path, values, centroid, min_face_px=min_face_px
    )
    return score_candidates(decoded, existing_dataset_features(session, dataset))
