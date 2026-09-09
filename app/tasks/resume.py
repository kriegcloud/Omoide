"""Re-enqueue supported tasks from their persisted inputs.

Resume starts a new task and relies on the existing worker's incremental work
selection. In particular, a library-wide run_processor always uses force=False,
even if its original params say True, so already-processed media are skipped.
Selection runs intentionally reprocess the same media IDs.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from threading import Lock, Thread

from fastapi import HTTPException
from sqlmodel import Session, select

from app.config import settings
from app.models import ProcessingTask
from app.tasks import (
    clean_missing_files,
    compute_blur_scores,
    run_backfill_demographics,
    run_backfill_face_quality,
    run_backfill_face_timestamps,
    run_build_events,
    run_duplicate_detection,
    run_geocode_places,
    run_media_processing,
    run_person_clustering,
    run_processors_for_media,
    run_scan,
    run_single_processor,
)
from app.tasks import common


def _resume_processor(params: dict) -> Callable[[str], None]:
    processor_name = params.get("processor_name")
    if not isinstance(processor_name, str) or not processor_name:
        raise ValueError("Missing processor_name")
    return lambda task_id: run_single_processor(task_id, processor_name, force=False)


def _resume_selection(params: dict) -> Callable[[str], None]:
    names = params.get("processor_names")
    ids = params.get("media_ids")
    if not isinstance(names, list) or not all(isinstance(name, str) for name in names):
        raise ValueError("Missing processor_names")
    if not isinstance(ids, list) or not all(type(media_id) is int for media_id in ids):
        raise ValueError("Missing media_ids")
    names, ids = list(names), list(ids)
    return lambda task_id: run_processors_for_media(task_id, names, ids)


def _resume_duplicates(params: dict) -> Callable[[str], None]:
    threshold = params.get("threshold", 2)
    if type(threshold) is not int:
        raise ValueError("Invalid duplicate threshold")
    return lambda task_id: run_duplicate_detection(task_id, threshold)


RESUMABLE_TASK_TYPES: dict[str, Callable[[dict], Callable[[str], None]]] = {
    "scan": lambda params: run_scan,
    "process_media": lambda params: run_media_processing,
    "cluster_persons": lambda params: run_person_clustering,
    "find_duplicates": _resume_duplicates,
    "clean_missing_files": lambda params: clean_missing_files,
    "compute_blur_scores": lambda params: compute_blur_scores,
    "build_events": lambda params: run_build_events,
    "geocode_places": lambda params: run_geocode_places,
    "run_processor": _resume_processor,
    "run_processor_for_media": _resume_selection,
    "backfill_demographics": lambda params: run_backfill_demographics,
    "backfill_face_timestamps": lambda params: run_backfill_face_timestamps,
    "backfill_face_quality": lambda params: run_backfill_face_quality,
}


def build_resume_callable(task: ProcessingTask) -> Callable[[str], None]:
    factory = RESUMABLE_TASK_TYPES.get(task.task_type)
    if factory is None or not isinstance(task.params, dict):
        raise ValueError("Task has no supported resume parameters")
    return factory(task.params)


def is_resumable(task: ProcessingTask) -> bool:
    """Whether the task type and saved inputs support resume, regardless of status."""
    try:
        build_resume_callable(task)
    except ValueError:
        return False
    return True


# Serialize resume requests in this server, including the pending-to-running gap.
_resume_lock = Lock()


def resume_task(session: Session, task: ProcessingTask) -> ProcessingTask:
    """Persist both ends of the resume link before starting the guarded worker."""
    if settings.general.presentation_mode:
        raise HTTPException(status_code=403, detail="Not allowed in presentation_mode mode.")

    with _resume_lock:
        session.refresh(task)
        if task.status not in ("interrupted", "cancelled", "failed") or not is_resumable(task):
            raise HTTPException(status_code=400, detail="Task cannot be resumed")
        if (task.result or {}).get("resumed_by"):
            raise HTTPException(status_code=409, detail="Task has already been resumed")
        existing = session.exec(
            select(ProcessingTask).where(
                ProcessingTask.task_type == task.task_type,
                ProcessingTask.status.in_(("pending", "running")),
            )
        ).first()
        if existing:
            raise HTTPException(status_code=409, detail="A task of this type is already active")

        callable_task = build_resume_callable(task)
        new_task = ProcessingTask(
            task_type=task.task_type,
            params={**task.params, "resumed_from": task.id},
        )
        task.result = {**(task.result or {}), "resumed_by": new_task.id}
        session.add_all([new_task, task])
        session.commit()
        session.refresh(new_task)

    try:
        Thread(
            target=common._run_task_guarded,
            args=(callable_task, new_task.id),
            name=f"task-resume-{new_task.id}",
            daemon=True,
        ).start()
    except Exception:
        # Keep a failed, resumable successor if the executor itself cannot start.
        new_task.status = "failed"
        new_task.finished_at = datetime.now(timezone.utc)
        session.add(new_task)
        session.commit()
        raise
    return new_task
