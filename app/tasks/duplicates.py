from __future__ import annotations

from sqlalchemy import delete, text
from sqlmodel import Session

import app.database as db
from app.concurrency import heavy_writer
from app.logger import logger
from app.models import DuplicateGroup, DuplicateMedia, ProcessingTask
from app.processors.duplicates import DuplicateProcessor
from .hashes import generate_hashes
from .common import _start_task

__all__ = ["run_duplicate_detection"]


def run_duplicate_detection(task_id: str, threshold: int) -> None:
    with Session(db.engine) as session:
        task = session.get(ProcessingTask, task_id)
        if not task or not _start_task(session, task):
            return

    def is_cancelled() -> bool:
        with Session(db.engine) as s:
            t = s.get(ProcessingTask, task_id)
            return not t or t.status in {"cancelled", "interrupted"}

    with heavy_writer(name="find_duplicates", cancelled=is_cancelled) as acquired:
        if not acquired or is_cancelled():
            return
        generate_hashes(task_id)
        if is_cancelled():
            return
        processor = DuplicateProcessor(task_id, threshold)
        processor.process()

    if is_cancelled():
        return
    with Session(db.engine) as session:
        empty_groups = session.exec(
            text(
                """
                SELECT group_id FROM (
                    SELECT group_id, COUNT(*) as cnt
                    FROM duplicatemedia
                    GROUP BY group_id
                ) WHERE cnt < 2
                """
            )
        ).all()
        if empty_groups:
            logger.info(
                "Cleaning up %d empty duplicate groups", len(empty_groups)
            )
            group_ids = [row[0] for row in empty_groups]
            if group_ids:
                session.exec(
                    delete(DuplicateMedia).where(
                        DuplicateMedia.group_id.in_(group_ids)
                    )
                )
                session.exec(
                    delete(DuplicateGroup).where(
                        DuplicateGroup.id.in_(group_ids)
                    )
                )
            session.commit()
