from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from sqlalchemy import func
from sqlmodel import Session, select

import app.database as db
from app.concurrency import heavy_writer
from app.config import settings
from app.logger import logger
from app.models import Media, ProcessingTask
from app.tasks.common import _finish_task, _start_task

__all__ = ["compute_blur_scores"]

_BATCH_SIZE = 200
_WORKERS = 4


def _score_from_path(path: str) -> float | None:
    from app.sharpness import score_image_path
    try:
        return score_image_path(path)
    except Exception as exc:
        logger.debug("Failed to compute blur score for %s: %s", path, exc)
        return None


def _score_media(thumb_dir: Path, thumb_rel: str | None, original: str,
                 is_video: bool = False) -> float:
    # Still images always use originals; videos retain their thumbnail source.
    path = str(thumb_dir / thumb_rel) if is_video and thumb_rel else original
    score = _score_from_path(path) if path else None
    return score if score is not None else -1.0


def compute_blur_scores(task_id: str) -> None:
    thumb_dir = settings.general.thumb_dir

    with Session(db.engine) as session:
        task = session.get(ProcessingTask, task_id)
        if not task:
            return

        def is_cancelled() -> bool:
            status = session.exec(
                select(ProcessingTask.status).where(ProcessingTask.id == task_id)
            ).first()
            return status not in ("pending", "running")

        processed = 0
        with heavy_writer(name="compute_blur_scores", cancelled=is_cancelled) as acquired:
            if not acquired or not _start_task(session, task):
                return
            task.total = session.exec(
                select(func.count(Media.id)).where(Media.laplacian_score.is_(None))
            ).first() or 0
            session.add(task)
            session.commit()
            while True:
                if is_cancelled():
                    return

                batch: list[Media] = session.exec(
                    select(Media)
                    .where(Media.laplacian_score.is_(None))
                    .limit(_BATCH_SIZE)
                ).all()
                if not batch:
                    break

                # Build lightweight tuples so threads don't touch the session.
                work = [(m.id, m.thumbnail_path, m.path, m.duration is not None) for m in batch]

                scores: dict[int, float] = {}
                with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
                    futures = {
                        pool.submit(_score_media, thumb_dir, thumb_rel, path, is_video): media_id
                        for media_id, thumb_rel, path, is_video in work
                    }
                    for future in as_completed(futures):
                        media_id = futures[future]
                        try:
                            scores[media_id] = future.result()
                        except Exception as exc:
                            logger.warning("Blur score failed for media %s: %s", media_id, exc)
                            scores[media_id] = -1.0

                if is_cancelled():
                    return
                # Write results back via the session (single-threaded).
                for media in batch:
                    media.laplacian_score = scores.get(media.id, -1.0)
                    session.add(media)
                processed += len(batch)
                session.commit()

                if task:
                    remaining = session.exec(
                        select(func.count(Media.id)).where(Media.laplacian_score.is_(None))
                    ).first() or 0
                    task.total = processed + remaining
                    task.processed = processed
                    session.add(task)
                    session.commit()

        _finish_task(session, task, "completed")
