from __future__ import annotations

from datetime import datetime
from typing import Any

from app.models import ProcessingTask


def _count(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, (list, tuple, set, dict)):
        return len(value)
    return None


def _plural(count: int, singular: str, plural: str | None = None) -> str:
    return f"{count} {singular if count == 1 else plural or singular + 's'}"


def _items(task: ProcessingTask) -> str:
    return f"{task.processed}/{task.total} items"


def task_duration_seconds(task: ProcessingTask) -> float | None:
    if task.started_at is None or task.finished_at is None:
        return None

    def naive_utc(value: datetime) -> datetime:
        return value.replace(tzinfo=None) if value.tzinfo is not None else value

    return (
        naive_utc(task.finished_at) - naive_utc(task.started_at)
    ).total_seconds()


def summarize_task(task: ProcessingTask) -> str:
    result = task.result or {}
    status = str(task.status)

    if status == "failed":
        error = result.get("error")
        return str(error) if error is not None else "Failed"
    if status == "cancelled":
        if task.total == 0:
            return "Cancelled"
        return f"Cancelled at {task.processed}/{task.total}"
    if status in ("running", "pending"):
        return ""
    if status != "completed":
        return ""

    task_type = task.task_type
    if task_type == "scan":
        new_files = _count(result.get("new_files"))
        if new_files is not None:
            summary = _plural(new_files, "new file")
            skipped = _count(result.get("skipped")) or 0
            if skipped > 0:
                summary += f", {skipped} skipped"
            return summary
        return _plural(task.processed, "new file")

    if task_type in ("process_media", "run_processor", "run_processor_for_media"):
        return "Nothing to do" if task.total == 0 else _plural(task.processed, "file") + " processed"

    if task_type == "cluster_persons":
        keys = ("new_persons", "matched", "merged")
        if any(key in result for key in keys):
            parts: list[str] = []
            new_persons = _count(result.get("new_persons")) or 0
            matched = _count(result.get("matched")) or 0
            merged = _count(result.get("merged")) or 0
            if new_persons:
                parts.append(_plural(new_persons, "new person", "new people"))
            if matched:
                parts.append(_plural(matched, "face") + " matched")
            if merged:
                parts.append(_plural(merged, "person", "people") + " merged")
            return ", ".join(parts) if parts else "No changes"
        return _plural(task.processed, "face") + " clustered"

    if task_type == "clean_missing_files":
        removed = _count(result.get("removed"))
        if removed is not None:
            return "No missing records" if removed == 0 else _plural(removed, "record") + " removed"
        return "Checked " + _plural(task.processed, "record")

    if task_type == "find_duplicates":
        groups = _count(result.get("groups"))
        if groups is not None:
            return _plural(groups, "duplicate group")
        return _plural(task.processed, "file") + " compared"

    face_backfills = {"backfill_demographics", "backfill_face_quality"}
    if task_type in face_backfills:
        return "Nothing to do" if task.total == 0 else _plural(task.processed, "face") + " updated"
    if task_type == "backfill_face_timestamps":
        return "Nothing to do" if task.total == 0 else _plural(task.processed, "video") + " updated"
    if task_type == "pose_backfill":
        return "Nothing to do" if task.total == 0 else _plural(task.processed, "image") + " updated"

    if task_type == "dataset_frame_mining":
        created = _count(result.get("created_media_ids"))
        return _plural(created, "frame") + " added" if created is not None else _items(task)

    if task_type == "dataset_caption_generation":
        generated = _count(result.get("generated"))
        if generated is None:
            generated = _count(result.get("caption_ids"))
        return _plural(generated, "caption") + " generated" if generated is not None else _items(task)

    if task_type == "batch_edit_media":
        edited = _count(result.get("edited"))
        skipped = _count(result.get("skipped"))
        if edited is not None or skipped is not None:
            return f"{edited or 0} edited, {skipped or 0} skipped"
        return _items(task)

    if task_type == "export_dataset":
        exported = next(
            (
                count
                for key in ("exported", "item_count", "items", "exported_ids")
                if (count := _count(result.get(key))) is not None
            ),
            None,
        )
        return _plural(exported, "item") + " exported" if exported is not None else _items(task)

    return "Done" if task.total == 0 else _items(task)
