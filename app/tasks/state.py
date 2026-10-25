from __future__ import annotations

from collections import OrderedDict
from typing import Any, MutableMapping
from threading import RLock

from pydantic import BaseModel

__all__ = [
    "TaskFailure",
    "record_task_failure",
    "get_task_failures",
    "get_failure_count",
    "set_task_progress",
    "clear_task_progress",
    "get_task_progress",
]


class TaskFailure(BaseModel):
    path: str
    reason: str


_MAX_TRACKED_FAILURE_TASKS = 5
_MAX_FAILURES_PER_TASK = 500

# The progress dictionaries are intentionally module-level singletons so they
# can be shared across API requests without additional coordination.
_task_progress: dict[str, dict[str, Any]] = {}
_task_failures: "OrderedDict[str, list[TaskFailure]]" = OrderedDict()
_task_failure_counts: dict[str, int] = {}
_failure_lock = RLock()


def get_task_progress() -> MutableMapping[str, dict[str, Any]]:
    return _task_progress


def record_task_failure(
    task_id: str,
    path: str,
    reason: str,
) -> None:
    entry = TaskFailure(path=path, reason=reason)
    with _failure_lock:
        if task_id not in _task_failures:
            _task_failure_counts[task_id] = 0
        failures = _task_failures.setdefault(task_id, [])
        _task_failure_counts[task_id] += 1
        failures.append(entry)
        _task_failures.move_to_end(task_id, last=True)
        # Bound retained examples and task identities, independently of totals.
        while len(_task_failures) > _MAX_TRACKED_FAILURE_TASKS:
            evicted_id, _ = _task_failures.popitem(last=False)
            _task_failure_counts.pop(evicted_id, None)
        if len(failures) > _MAX_FAILURES_PER_TASK:
            failures.pop(0)
    set_task_progress(task_id, current_item=entry.path, current_step="error")


def get_task_failures(task_id: str) -> list[TaskFailure]:
    with _failure_lock:
        return list(_task_failures.get(task_id, []))


def get_failure_count(task_id: str) -> int:
    with _failure_lock:
        return _task_failure_counts.get(task_id, 0) if task_id in _task_failures else 0


def set_task_progress(
    task_id: str,
    *,
    current_item: str | None = None,
    current_step: str | None = None,
    **extras: Any,
) -> None:
    entry = _task_progress.setdefault(
        task_id, {"current_item": None, "current_step": None}
    )
    if current_item is not None:
        entry["current_item"] = current_item
    if current_step is not None:
        entry["current_step"] = current_step
    for key, value in extras.items():
        if key in ("current_item", "current_step"):
            continue
        if value is not None:
            entry[key] = value


def clear_task_progress(task_id: str) -> None:
    _task_progress.pop(task_id, None)
