from datetime import datetime

from sqlmodel import Session

import app.database as db
from app.database import safe_commit
from app.logger import logger
from app.models import (
    DatasetExport,
    DatasetExportStatus,
    ProcessingTask,
    Status,
)
from app.services.datasets import build_export
from app.tasks.state import clear_task_progress


def export_dataset(task_id: str, export_id: int) -> None:
    """Run a dataset export and mirror task state onto its export row."""
    with Session(db.engine) as session:
        task = session.get(ProcessingTask, task_id)
        export = session.get(DatasetExport, export_id)
        if task is None or export is None:
            logger.error("Dataset export task %s or export %s is missing", task_id, export_id)
            return
        task.status = Status.RUNNING
        task.started_at = datetime.now()
        export.status = DatasetExportStatus.RUNNING
        session.add(task)
        session.add(export)
        safe_commit(session)

        try:
            build_export(session, export_id, task_id)
            session.refresh(task)
            session.refresh(export)
            cancelled = task.status == Status.CANCELLED
            export.status = (
                DatasetExportStatus.CANCELLED
                if cancelled
                else DatasetExportStatus.COMPLETED
            )
            task.status = Status.CANCELLED if cancelled else Status.COMPLETED
        except Exception as exc:
            logger.exception("Dataset export %s failed", export_id)
            task.status = Status.FAILED
            export.status = DatasetExportStatus.FAILED
            export.error = str(exc)
        finally:
            finished = datetime.now()
            task.finished_at = finished
            export.finished_at = finished
            session.add(task)
            session.add(export)
            safe_commit(session)
            clear_task_progress(task_id)
