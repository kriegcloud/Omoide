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
from app.tasks.common import _start_task, _finish_task
from app.tasks.state import clear_task_progress


def export_dataset(task_id: str, export_id: int) -> None:
    """Run a dataset export and mirror task state onto its export row."""
    with Session(db.engine) as session:
        task = session.get(ProcessingTask, task_id)
        export = session.get(DatasetExport, export_id)
        if task is None or export is None:
            logger.error("Dataset export task %s or export %s is missing", task_id, export_id)
            return
        if not _start_task(session, task):
            return
        export.status = DatasetExportStatus.RUNNING
        session.add(task)
        session.add(export)
        safe_commit(session)

        failure = None
        try:
            build_export(session, export_id, task_id)
            _finish_task(session, task, "completed")
        except Exception as exc:
            session.rollback()
            logger.exception("Dataset export %s failed", export_id)
            _finish_task(session, task, "failed")
            failure = str(exc)
        finally:
            session.refresh(task)
            session.refresh(export)
            if failure is not None:
                export.error = failure
            export.status = (
                DatasetExportStatus.COMPLETED if task.status == Status.COMPLETED
                else DatasetExportStatus.FAILED if task.status == Status.FAILED
                else DatasetExportStatus.CANCELLED
            )
            export.finished_at = task.finished_at or datetime.now()
            session.add(export)
            safe_commit(session)
            clear_task_progress(task_id)
