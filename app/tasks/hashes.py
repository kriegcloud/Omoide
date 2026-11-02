from __future__ import annotations

from pathlib import Path
from typing import Literal

from sqlalchemy import func
from sqlmodel import Session, select

import app.database as db
from app.concurrency import heavy_writer
from app.config import settings
from app.logger import logger
from app.models import Media, ProcessingTask
from app.phash import generate_perceptual_hash

__all__ = ["generate_hashes"]


def generate_hashes(task_id: int | None = None) -> None:
    """Populate perceptual hashes for media lacking one."""
    batch_size = 10
    failure_marker = ""

    with Session(db.engine) as session:
        task = session.get(ProcessingTask, task_id) if task_id else None
        logger.info("Got task!")

        if task:
            count_stmt = select(func.count(Media.id)).where(
                Media.phash.is_(None),
            )
            total_count = session.exec(count_stmt).first() or 0
            logger.info("SET count to %s", total_count)
            task.total = total_count
            task.processed = 0
            session.commit()

        def is_cancelled() -> bool:
            if not task:
                return False
            session.refresh(task, attribute_names=["status"])
            return task.status == "cancelled"

        processed_so_far = task.processed if task else 0

        with heavy_writer(name="generate_hashes", cancelled=is_cancelled):
            while True:
                media_batch_stmt = (
                    select(Media)
                    .where(Media.phash.is_(None))
                    .limit(batch_size)
                )
                media_to_hash = session.exec(media_batch_stmt).all()

                if not media_to_hash:
                    logger.info("No more media to hash. Task complete.")
                    break

                successful_hashes = 0
                for media in media_to_hash:
                    if task:
                        session.refresh(task, attribute_names=["status"])
                        if task.status == "cancelled":
                            logger.warning(
                                "Task %s was cancelled. Aborting.", task_id
                            )
                            session.rollback()
                            return

                    try:
                        suffix = (
                            Path(media.path).suffix.lower()
                            if media.path
                            else ""
                        )
                        media_type: Literal["image", "video"] = (
                            "video"
                            if suffix in settings.scan.VIDEO_SUFFIXES
                            else "image"
                        )
                        hash_value = generate_perceptual_hash(
                            media, type=media_type
                        )
                        if hash_value:
                            media.phash = hash_value
                            successful_hashes += 1
                        else:
                            media.phash = failure_marker
                            logger.debug(
                                "No perceptual hash generated for media %s; marking as skipped",
                                media.id,
                            )
                        session.add(media)
                    except Exception as exc:
                        logger.error(
                            "Could not generate hash for media %s: %s",
                            media.id,
                            exc,
                        )
                        media.phash = failure_marker
                        session.add(media)

                session.commit()
                logger.info(
                    "Committed batch of %d hashes.", len(media_to_hash)
                )

                if task:
                    processed_so_far += successful_hashes
                    remaining_stmt = select(func.count(Media.id)).where(
                        Media.phash.is_(None),
                    )
                    remaining = session.exec(remaining_stmt).first() or 0
                    task.total = processed_so_far + remaining
                    task.processed = processed_so_far
                    session.add(task)
                    session.commit()


def rehash_images(session: Session, cancelled=None) -> int:
    """Recompute every still-image hash, committing bounded batches.

    Caller owns writer exclusion. Failures retain the old hash and are logged
    by generate_perceptual_hash. Videos and their hashes are untouched.
    """
    last_id = 0
    updated = 0
    while True:
        if cancelled and cancelled():
            return updated
        batch = session.exec(select(Media).where(
            Media.duration.is_(None), Media.id > last_id
        ).order_by(Media.id).limit(200)).all()
        if not batch:
            return updated
        for media in batch:
            value = generate_perceptual_hash(media, "image")
            if value:
                media.phash = value
                session.add(media)
                updated += 1
        last_id = batch[-1].id
        session.commit()
