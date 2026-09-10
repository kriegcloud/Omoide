"""Re-enqueue supported tasks from their persisted inputs.

Workers use persisted cursors, completed selection entries and pending face IDs
so resuming preserves forced work without replaying committed items.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from threading import Lock, Thread, current_thread
from time import monotonic

from fastapi import HTTPException
from sqlmodel import Session, select

from app.config import settings
from app.logger import logger
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
    run_media_processing_and_chain,
    run_cleanup_and_chain,
    run_scan_and_chain,
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
    return lambda task_id: run_single_processor(task_id, processor_name, force=bool(params.get("force", False)))


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
    "scan": lambda params: run_scan_and_chain if params.get("chain") else run_scan,
    "process_media": lambda params: run_media_processing_and_chain if params.get("chain") else run_media_processing,
    "cluster_persons": lambda params: run_person_clustering,
    "find_duplicates": _resume_duplicates,
    "clean_missing_files": lambda params: run_cleanup_and_chain if params.get("chain") else clean_missing_files,
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
_workers_lock = Lock()
_worker_threads: set[Thread] = set()
_workers_stopping = False


def accept_resumed_tasks() -> None:
    """Open worker admission when the application starts its lifespan."""
    global _workers_stopping
    with _resume_lock:
        _workers_stopping = False


def stop_resumed_tasks() -> None:
    """Close admission before shutdown marks active database tasks interrupted."""
    global _workers_stopping
    with _resume_lock:
        _workers_stopping = True


def join_resumed_workers(timeout: float = 5.0) -> int:
    """Wait at most one shared deadline for resumed workers, retaining live handles."""
    deadline = monotonic() + max(0.0, timeout)
    with _workers_lock:
        workers = tuple(_worker_threads)
    for worker in workers:
        if worker is not current_thread() and worker.is_alive():
            worker.join(timeout=max(0.0, deadline - monotonic()))
    with _workers_lock:
        _worker_threads.difference_update(
            worker for worker in tuple(_worker_threads) if not worker.is_alive()
        )
        remaining = len(_worker_threads)
    if remaining:
        logger.warning("%s resumed workers still stopping after shutdown timeout", remaining)
    return remaining


def resume_task(session: Session, task: ProcessingTask) -> ProcessingTask:
    """Persist both ends of the resume link before starting the guarded worker."""
    if settings.general.presentation_mode:
        raise HTTPException(status_code=403, detail="Not allowed in presentation_mode mode.")

    with _resume_lock:
        if _workers_stopping:
            raise HTTPException(status_code=503, detail="Application is shutting down")
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

        thread = None
        try:
            thread = Thread(
                target=common._run_task_guarded,
                args=(callable_task, new_task.id),
                name=f"task-resume-{new_task.id}",
                daemon=True,
            )
            with _workers_lock:
                _worker_threads.difference_update(
                    worker for worker in tuple(_worker_threads) if not worker.is_alive()
                )
                _worker_threads.add(thread)
                thread.start()
        except Exception:
            if thread is not None:
                with _workers_lock:
                    _worker_threads.discard(thread)
            # Keep a failed, resumable successor if the executor itself cannot start.
            new_task.status = "failed"
            new_task.finished_at = datetime.now(timezone.utc)
            session.add(new_task)
            session.commit()
            raise
    return new_task
