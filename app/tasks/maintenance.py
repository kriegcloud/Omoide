from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import delete, func, text, update
from sqlmodel import Session, select

import app.database as db
from app.api.media import delete_record
from app.concurrency import heavy_writer
from app.config import settings
from app.database import safe_commit
from app.logger import logger
from app.models import (
    Face,
    FaceAssignmentSource,
    Media,
    Person,
    PersonMediaLink,
    PersonRelationship,
    PersonSocialLink,
    PersonTagLink,
    ProcessingTask,
    TimelineEvent,
    TrainingDataset,
)
from app.services.face_provenance import face_assignment_values

__all__ = [
    "clean_missing_files",
    "reset_clustering",
    "reset_processing",
]


def _delete_reset_persons(session: Session) -> int:
    """Detach retained datasets and remove person-owned rows in the reset transaction."""
    session.exec(update(TrainingDataset).values(person_id=None))
    session.exec(delete(PersonMediaLink))
    session.exec(delete(PersonSocialLink))
    session.exec(delete(PersonTagLink))
    session.exec(delete(TimelineEvent))
    session.exec(delete(PersonRelationship))
    session.exec(text("DELETE FROM person_embeddings"))
    return session.exec(delete(Person)).rowcount


def reset_processing(session: Session) -> str:
    with heavy_writer(name="reset_processing"):
        face_rows = list(session.exec(select(Face.id, Face.thumbnail_path)))

        try:
            session.exec(update(Media).values(faces_extracted=False, embeddings_created=False))
            session.exec(
                update(Face).where(Face.person_id.is_not(None)).values(
                    **face_assignment_values(None, FaceAssignmentSource.RESET)
                )
            )
            deleted_people = _delete_reset_persons(session)
            session.exec(text("DELETE FROM face_embeddings"))
            session.exec(text("DELETE FROM media_embeddings"))
            session.exec(delete(Face))
            safe_commit(session)
        except Exception:
            session.rollback()
            raise
        logger.info("persons deleted count=%s reason=reset-processing", deleted_people)

        for _, thumb_path in face_rows:
            if not thumb_path:
                continue
            try:
                path_obj = Path(thumb_path)
            except Exception:
                continue
            if path_obj.exists():
                try:
                    path_obj.unlink()
                except Exception:
                    logger.debug(
                        "Failed to remove face thumbnail %s", path_obj
                    )
    return "OK"


def reset_clustering(session: Session) -> str:
    with heavy_writer(name="reset_clustering"):
        try:
            session.exec(
                update(Face).where(Face.person_id.is_not(None)).values(
                    **face_assignment_values(None, FaceAssignmentSource.RESET)
                )
            )
            session.exec(text("UPDATE face_embeddings SET person_id=-1"))
            deleted_people = _delete_reset_persons(session)
            safe_commit(session)
        except Exception:
            session.rollback()
            raise
        logger.info("persons deleted count=%s reason=reset-clustering", deleted_people)
    return "OK"


def _naive_utc(value: datetime) -> datetime:
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _unmounted_roots() -> list[Path]:
    skipped: list[Path] = []
    for root, _read_only in settings.general.resolved_media_dirs():
        try:
            available = root.is_dir() and next(root.iterdir(), None) is not None
        except OSError:
            available = False
        if not available:
            skipped.append(root)
    return skipped


def clean_missing_files(task_id: str) -> None:
    with Session(db.engine) as session:
        task = session.get(ProcessingTask, task_id)
        if not task:
            logger.error("Task %s not found", task_id)
            return

        started = session.exec(
            update(ProcessingTask)
            .where(ProcessingTask.id == task_id, ProcessingTask.status.in_(("pending", "running")))
            .values(
                status="running",
                started_at=datetime.now(timezone.utc),
                total=session.exec(select(func.count(Media.id))).first() or 0,
            )
        ).rowcount
        session.commit()
        if not started:
            return

        processed = 0
        flagged = 0
        recovered = 0
        auto_deleted = 0
        batch_size = 200
        last_id = 0

        def is_cancelled() -> bool:
            session.refresh(task, attribute_names=["status"])
            return task.status != "running"

        grace_hours = max(0, settings.scan.auto_cleanup_grace_hours)
        grace_delta = timedelta(hours=grace_hours)
        auto_cleanup_enabled = settings.scan.auto_cleanup_without_review
        skipped_roots = _unmounted_roots()

        def save_progress() -> None:
            awaiting_review = session.exec(
                select(func.count(Media.id)).where(
                    Media.missing_since.is_not(None),
                    Media.missing_confirmed.is_(False),
                )
            ).first() or 0
            task.processed = processed
            task.result = {
                **(task.result or {}),
                "flagged": flagged,
                "recovered": recovered,
                "removed": auto_deleted,
                "awaiting_review": awaiting_review,
                "skipped_unmounted_roots": [str(root) for root in skipped_roots],
            }
            session.commit()

        with heavy_writer(name="clean_missing_files", cancelled=is_cancelled) as acquired:
            if not acquired:
                return
            while True:
                if is_cancelled():
                    save_progress()
                    return
                media_batch = session.exec(
                    select(Media)
                    .where(Media.id > last_id)
                    .order_by(Media.id)
                    .limit(batch_size)
                ).all()

                if not media_batch:
                    break

                for media in media_batch:
                    if is_cancelled():
                        save_progress()
                        return
                    last_id = media.id
                    processed += 1
                    media_path = Path(media.path)
                    if any(media_path.resolve().is_relative_to(root) for root in skipped_roots):
                        continue
                    current_time = _naive_utc(datetime.now(timezone.utc))

                    if not media_path.exists():
                        if media.missing_since is None:
                            media.missing_since = current_time
                            flagged += 1
                        if auto_cleanup_enabled:
                            cutoff = current_time - grace_delta
                            if grace_delta == timedelta(0) or (
                                media.missing_since
                                and _naive_utc(media.missing_since) <= cutoff
                            ):
                                delete_record(media.id, session)
                                auto_deleted += 1
                                continue
                        media.missing_confirmed = False
                        session.add(media)
                    else:
                        if (
                            media.missing_since is not None
                            or media.missing_confirmed
                        ):
                            media.missing_since = None
                            media.missing_confirmed = False
                            session.add(media)
                            recovered += 1

                save_progress()

        save_progress()
        completed = session.exec(
            update(ProcessingTask)
            .where(ProcessingTask.id == task_id, ProcessingTask.status == "running")
            .values(status="completed", finished_at=datetime.now(timezone.utc))
        ).rowcount
        session.commit()
        if not completed:
            return
        logger.info(
            "Missing files cleanup processed=%d flagged=%d recovered=%d auto_deleted=%d",
            processed,
            flagged,
            recovered,
            auto_deleted,
        )
