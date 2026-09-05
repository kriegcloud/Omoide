"""Durable task for materialising selected video frames as dataset media."""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path

import cv2
from sqlalchemy import func
from sqlmodel import Session, select

import app.database as db
from app.database import safe_commit
from app.logger import logger
from app.models import DatasetItem, Media, ProcessingTask, Status, TrainingDataset
from app.services.frame_mining import (
    dataset_videos,
    mine_candidates,
    select_candidates,
)
from app.services.media_files import MediaFileError
from app.tasks.state import clear_task_progress, record_task_failure, set_task_progress
from app.utils import generate_perceptual_hash, generate_thumbnail


def _write_frame(path: Path, frame, quality: int = 95) -> None:
    if path.exists():
        raise FileExistsError(f"Frame output already exists: {path}")
    temporary = path.with_name(f".{path.stem}.tmp.jpg")
    try:
        if not cv2.imwrite(
            str(temporary), frame, [cv2.IMWRITE_JPEG_QUALITY, quality]
        ):
            raise OSError(f"Could not write frame: {path}")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _register_frame(
    session: Session,
    dataset: TrainingDataset,
    video: Media,
    candidate,
) -> Media:
    target = Path(video.path).with_name(
        f"{Path(video.path).stem}_frame-{candidate.timestamp_ms}.jpg"
    )
    _write_frame(target, candidate.frame)
    try:
        height, width = candidate.frame.shape[:2]
        media = Media(
            path=os.fspath(target),
            filename=target.name,
            size=target.stat().st_size,
            width=width,
            height=height,
            created_at=video.created_at + timedelta(milliseconds=candidate.timestamp_ms),
            laplacian_score=candidate.sharpness,
        )
        session.add(media)
        session.flush()
        media.phash = generate_perceptual_hash(media, type="image")
        media.thumbnail_path, thumbnail_error = generate_thumbnail(media)
        if thumbnail_error:
            logger.warning(
                "Frame media %s thumbnail failed: %s", media.id, thumbnail_error
            )
        max_position = session.exec(
            select(func.max(DatasetItem.position)).where(
                DatasetItem.dataset_id == dataset.id
            )
        ).one()
        session.add(
            DatasetItem(
                dataset_id=int(dataset.id),
                media_id=int(media.id),
                position=int(max_position if max_position is not None else -1) + 1,
                origin="frame",
            )
        )
        session.add(media)
        safe_commit(session)
        session.refresh(media)
        return media
    except Exception:
        session.rollback()
        target.unlink(missing_ok=True)
        raise


def mine_dataset_frames(
    task_id: str,
    *,
    dataset_id: int,
    video_media_ids: list[int] | None = None,
    max_per_video: int = 12,
    min_face_px: int = 160,
    fps: float = 2.0,
    selected_timestamps_ms: dict[int, list[int]] | None = None,
) -> None:
    """Mine, register, and enqueue processing for selected subject frames."""
    created_ids: list[int] = []
    skipped = 0
    failed = 0
    selected_timestamps_ms = selected_timestamps_ms or {}
    with Session(db.engine) as session:
        task = session.get(ProcessingTask, task_id)
        dataset = session.get(TrainingDataset, dataset_id)
        if task is None:
            logger.error("Dataset frame mining task %s is missing", task_id)
            return
        if dataset is None:
            task.status = Status.FAILED
            task.finished_at = datetime.now()
            task.result = {"dataset_id": dataset_id, "error": "Dataset not found"}
            session.add(task)
            safe_commit(session)
            return

        summaries = dataset_videos(session, dataset)
        if video_media_ids is None:
            video_ids = [
                int(summary["media_id"])
                for summary in summaries
                if summary["already_mined_count"] == 0
            ]
        else:
            video_ids = list(dict.fromkeys(video_media_ids))
        task.status = Status.RUNNING
        task.started_at = datetime.now()
        task.total = len(video_ids)
        task.result = {"dataset_id": dataset_id, "created_media_ids": []}
        session.add(task)
        safe_commit(session)

        for index, video_id in enumerate(video_ids, start=1):
            session.refresh(task)
            if task.status == Status.CANCELLED:
                break
            video = session.get(Media, video_id)
            set_task_progress(
                task_id,
                current_step="mining_video_frames",
                current_item=video.path if video else str(video_id),
            )
            try:
                if video is None or video.duration is None:
                    raise ValueError("Video media record was not found")
                requested = selected_timestamps_ms.get(video_id)
                timestamps = (
                    [value / 1000.0 for value in requested]
                    if requested is not None
                    else None
                )
                candidates = mine_candidates(
                    session,
                    dataset,
                    video,
                    fps=fps,
                    min_face_px=min_face_px,
                    max_candidates=max(48, max_per_video * 12),
                    timestamps=timestamps,
                )
                chosen = select_candidates(candidates, max_per_video)
                if not chosen:
                    skipped += 1
                for candidate in chosen:
                    media = _register_frame(session, dataset, video, candidate)
                    created_ids.append(int(media.id))
            except (MediaFileError, FileExistsError, OSError, ValueError) as exc:
                session.rollback()
                failed += 1
                record_task_failure(
                    task_id, video.path if video else str(video_id), str(exc)
                )
                logger.warning("Could not mine frames from media %s: %s", video_id, exc)
            except Exception as exc:  # noqa: BLE001 - durable task boundary
                session.rollback()
                failed += 1
                record_task_failure(
                    task_id, video.path if video else str(video_id), str(exc)
                )
                logger.exception("Frame mining failed for media %s", video_id)

            task = session.get(ProcessingTask, task_id)
            if task is None:
                return
            task.processed = index
            task.result = {
                "dataset_id": dataset_id,
                "created_media_ids": created_ids,
                "skipped_videos": skipped,
                "failed_videos": failed,
            }
            session.add(task)
            safe_commit(session)

        task = session.get(ProcessingTask, task_id)
        if task is None:
            return
        cancelled = task.status == Status.CANCELLED
        task.status = Status.CANCELLED if cancelled else Status.COMPLETED
        task.finished_at = datetime.now()
        task.result = {
            "dataset_id": dataset_id,
            "created_media_ids": created_ids,
            "skipped_videos": skipped,
            "failed_videos": failed,
        }
        session.add(task)

        processor_task_id: str | None = None
        if created_ids and not cancelled:
            processor_task = ProcessingTask(
                task_type="run_processor_for_media", total=len(created_ids), processed=0
            )
            session.add(processor_task)
            safe_commit(session)
            session.refresh(processor_task)
            processor_task_id = processor_task.id
        else:
            safe_commit(session)

    clear_task_progress(task_id)
    if processor_task_id is not None:
        from app.tasks.media_processing import (
            edit_processor_names,
            run_processors_for_media,
        )

        try:
            run_processors_for_media(
                processor_task_id, edit_processor_names(), created_ids
            )
        except Exception:  # noqa: BLE001 - follow-up must not undo mined frames
            logger.exception(
                "Post-frame-mining processing failed for media %s", created_ids
            )
