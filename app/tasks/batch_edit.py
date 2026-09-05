from __future__ import annotations

from datetime import datetime

from sqlmodel import Session

import app.database as db
from app.config import settings
from app.database import safe_commit
from app.logger import logger
from app.models import Media, ProcessingTask, Status
from app.schemas.media import MediaBatchEditRequest
from app.services.image_edits import edit_media_record
from app.tasks.state import clear_task_progress, set_task_progress


_EDIT_PROCESSORS = ["faces", "embedding_extractor", "auto_tagger", "blur", "exif"]


def batch_edit_media(task_id: str, raw_request: dict) -> None:
    """Apply one Phase 6 operation list to many media records."""
    request = MediaBatchEditRequest.model_validate(raw_request)
    edited: list[dict[str, int]] = []
    skipped: list[dict[str, int | str]] = []
    output_ids: list[int] = []

    with Session(db.engine) as session:
        task = session.get(ProcessingTask, task_id)
        if task is None:
            logger.error("Batch edit task %s is missing", task_id)
            return
        task.status = Status.RUNNING
        task.started_at = datetime.now()
        unique_ids = list(dict.fromkeys(request.media_ids))
        task.total = len(unique_ids)
        session.add(task)
        safe_commit(session)

        try:
            for index, media_id in enumerate(unique_ids, start=1):
                session.refresh(task)
                if task.status == Status.CANCELLED:
                    break
                set_task_progress(
                    task_id,
                    current_step="batch_editing_media",
                    current_item=f"{index} of {len(unique_ids)}",
                )
                media = session.get(Media, media_id)
                if media is None:
                    skipped.append({"media_id": media_id, "reason": "Media not found"})
                elif media.duration is not None:
                    skipped.append({"media_id": media_id, "reason": "Video"})
                else:
                    try:
                        result = edit_media_record(
                            session,
                            media,
                            request.ops,
                            request.mode,
                        )
                        edited.append(
                            {"media_id": media_id, "result_media_id": int(result.id)}
                        )
                        output_ids.append(int(result.id))
                    except Exception as exc:
                        session.rollback()
                        logger.warning("Batch edit skipped media %s: %s", media_id, exc)
                        skipped.append({"media_id": media_id, "reason": str(exc)})
                task = session.get(ProcessingTask, task_id)
                if task is None:
                    return
                task.processed = index
                task.result = {"edited": edited, "skipped": skipped}
                session.add(task)
                safe_commit(session)

            session.refresh(task)
            cancelled = task.status == Status.CANCELLED
            task.status = Status.CANCELLED if cancelled else Status.COMPLETED
            task.finished_at = datetime.now()
            task.result = {"edited": edited, "skipped": skipped}
            session.add(task)
            safe_commit(session)
        except Exception as exc:
            logger.exception("Batch edit task %s failed", task_id)
            session.rollback()
            task = session.get(ProcessingTask, task_id)
            if task is not None:
                task.status = Status.FAILED
                task.finished_at = datetime.now()
                task.result = {"edited": edited, "skipped": skipped, "error": str(exc)}
                session.add(task)
                safe_commit(session)
            return
        finally:
            clear_task_progress(task_id)

        if not output_ids or cancelled:
            return
        processor_task = ProcessingTask(
            task_type="run_processor_for_media", total=len(output_ids), processed=0
        )
        session.add(processor_task)
        safe_commit(session)
        session.refresh(processor_task)
        processor_task_id = processor_task.id

    # Follow the single-edit path by processing edited outputs after the batch
    # summary itself has reached a terminal state.
    from app.tasks.media_processing import run_processors_for_media

    run_processors_for_media(processor_task_id, _EDIT_PROCESSORS, output_ids)
