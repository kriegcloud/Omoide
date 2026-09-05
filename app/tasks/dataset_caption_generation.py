from __future__ import annotations

import time
from datetime import datetime

from sqlmodel import Session, select

import app.database as db
from app.annotation_tasks import (
    AnnotationBusyError,
    create_annotation_attempt,
    run_annotation_attempt,
)
from app.database import safe_commit
from app.logger import logger
from app.models import (
    AnnotationAttempt,
    AnnotationAttemptStatus,
    AnnotationKind,
    AnnotationReviewStatus,
    DatasetItem,
    MediaAnnotation,
    ProcessingTask,
    Status,
)
from app.tasks.state import clear_task_progress, set_task_progress


_BUSY_RETRY_SECONDS = 0.25
_BUSY_RETRIES = 3


def _has_approved_caption(session: Session, media_id: int) -> bool:
    return (
        session.exec(
            select(MediaAnnotation.id).where(
                MediaAnnotation.media_id == media_id,
                MediaAnnotation.kind == AnnotationKind.CAPTION,
                MediaAnnotation.review_status == AnnotationReviewStatus.APPROVED,
            )
        ).first()
        is not None
    )


def generate_dataset_captions(
    task_id: str,
    *,
    dataset_id: int,
    only_missing: bool,
) -> None:
    """Generate dataset captions serially through the annotation lease."""
    generated = 0
    skipped = 0
    failed = 0

    with Session(db.engine) as session:
        task = session.get(ProcessingTask, task_id)
        if task is None:
            logger.error("Dataset caption task %s is missing", task_id)
            return
        items = list(
            session.exec(
                select(DatasetItem)
                .where(DatasetItem.dataset_id == dataset_id)
                .order_by(DatasetItem.position, DatasetItem.id)
            ).all()
        )
        task.status = Status.RUNNING
        task.started_at = datetime.now()
        task.total = len(items)
        task.result = {
            "dataset_id": dataset_id,
            "generated": generated,
            "skipped": skipped,
            "failed": failed,
        }
        session.add(task)
        safe_commit(session)

        for index, item in enumerate(items, start=1):
            session.refresh(task)
            if task.status == Status.CANCELLED:
                break
            set_task_progress(
                task_id,
                current_step="generating_dataset_captions",
                current_item=f"{index} of {len(items)}",
            )
            if item.excluded or (
                only_missing and _has_approved_caption(session, item.media_id)
            ):
                skipped += 1
            else:
                attempt = None
                for retry in range(_BUSY_RETRIES + 1):
                    try:
                        attempt = create_annotation_attempt(
                            session,
                            media_id=item.media_id,
                            kind=AnnotationKind.CAPTION,
                        )
                        break
                    except AnnotationBusyError:
                        session.rollback()
                        if retry == _BUSY_RETRIES:
                            skipped += 1
                            break
                        time.sleep(_BUSY_RETRY_SECONDS)
                    except Exception as exc:
                        session.rollback()
                        failed += 1
                        logger.warning(
                            "Caption generation skipped item %s: %s", item.id, exc
                        )
                        break

                if attempt is not None:
                    try:
                        run_annotation_attempt(attempt.id)
                        session.expire_all()
                        persisted = session.get(AnnotationAttempt, attempt.id)
                        if (
                            persisted is not None
                            and persisted.status != AnnotationAttemptStatus.SUCCEEDED
                        ):
                            failed += 1
                        else:
                            generated += 1
                    except Exception as exc:
                        session.rollback()
                        failed += 1
                        logger.warning(
                            "Caption generation failed for item %s: %s", item.id, exc
                        )

            task = session.get(ProcessingTask, task_id)
            if task is None:
                return
            task.processed = index
            task.result = {
                "dataset_id": dataset_id,
                "generated": generated,
                "skipped": skipped,
                "failed": failed,
            }
            session.add(task)
            safe_commit(session)

        task = session.get(ProcessingTask, task_id)
        if task is not None:
            cancelled = task.status == Status.CANCELLED
            task.status = Status.CANCELLED if cancelled else Status.COMPLETED
            task.finished_at = datetime.now()
            task.result = {
                "generated": generated,
                "skipped": skipped,
                "failed": failed,
            }
            session.add(task)
            safe_commit(session)
        clear_task_progress(task_id)
