import os
import tempfile
import unittest
from datetime import datetime, timedelta


_CONFIG_HOME = tempfile.TemporaryDirectory()
os.environ["XDG_CONFIG_HOME"] = _CONFIG_HOME.name

from sqlmodel import Session, SQLModel, create_engine, select  # noqa: E402

from app.api.face import get_orphan_count, router as face_router  # noqa: E402
from app.api.tasks import (  # noqa: E402
    list_active_tasks,
    list_recent_tasks,
    router as tasks_router,
)
from app.models import Face, ProcessingTask  # noqa: E402
from app.processors.duplicates import DuplicateProcessor  # noqa: E402
from app.services.task_summary import (  # noqa: E402
    summarize_task,
    task_duration_seconds,
)


class TaskSummaryTests(unittest.TestCase):
    def task(self, task_type: str, **changes) -> ProcessingTask:
        values = {
            "task_type": task_type,
            "status": "completed",
            "total": 3,
            "processed": 3,
        }
        values.update(changes)
        return ProcessingTask(**values)

    def test_scan_zero_one_and_many(self) -> None:
        self.assertEqual(
            summarize_task(self.task("scan", result={"new_files": 0})),
            "0 new files",
        )
        self.assertEqual(
            summarize_task(self.task("scan", result={"new_files": 1})),
            "1 new file",
        )
        self.assertEqual(
            summarize_task(
                self.task("scan", result={"new_files": 4, "skipped": 2})
            ),
            "4 new files, 2 skipped",
        )

    def test_process_media_nothing_to_do(self) -> None:
        self.assertEqual(
            summarize_task(self.task("process_media", total=0, processed=0)),
            "Nothing to do",
        )

    def test_cluster_persons_uses_nonzero_parts(self) -> None:
        self.assertEqual(
            summarize_task(
                self.task(
                    "cluster_persons",
                    result={"new_persons": 2, "matched": 2, "merged": 0},
                )
            ),
            "2 new people, 2 faces matched",
        )
        self.assertEqual(
            summarize_task(
                self.task(
                    "cluster_persons",
                    result={"new_persons": 0, "matched": 0},
                )
            ),
            "No changes",
        )

    def test_failed_and_cancelled(self) -> None:
        self.assertEqual(
            summarize_task(
                self.task("scan", status="failed", result={"error": "Disk full"})
            ),
            "Disk full",
        )
        self.assertEqual(
            summarize_task(
                self.task("scan", status="cancelled", total=8, processed=3)
            ),
            "Cancelled at 3/8",
        )
        self.assertEqual(
            summarize_task(
                self.task("scan", status="cancelled", total=0, processed=0)
            ),
            "Cancelled",
        )

    def test_duration_handles_naive_datetimes(self) -> None:
        started = datetime(2026, 1, 1, 12, 0, 0)
        task = self.task(
            "scan",
            started_at=started,
            finished_at=started + timedelta(seconds=1.25),
        )
        self.assertEqual(task_duration_seconds(task), 1.25)


class TaskFeedApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
        )
        SQLModel.metadata.create_all(
            self.engine,
            tables=[ProcessingTask.__table__],
        )
    def tearDown(self) -> None:
        self.engine.dispose()

    def add_task(self, **changes) -> ProcessingTask:
        values = {
            "task_type": "scan",
            "status": "completed",
            "created_at": datetime(2026, 1, 1),
            "total": 1,
            "processed": 1,
        }
        values.update(changes)
        task = ProcessingTask(**values)
        with Session(self.engine) as session:
            session.add(task)
            session.commit()
            session.refresh(task)
        return task

    def test_recent_orders_by_effective_finish_and_clamps_limit(self) -> None:
        base = datetime(2026, 1, 1)
        tasks = [
            self.add_task(
                finished_at=base + timedelta(minutes=index),
                created_at=base + timedelta(minutes=index),
            )
            for index in range(55)
        ]

        with Session(self.engine) as session:
            recent_two = list_recent_tasks(2, session=session)
            recent_one = list_recent_tasks(0, session=session)
            recent_fifty = list_recent_tasks(500, session=session)
        self.assertEqual(
            [row.id for row in recent_two],
            [tasks[-1].id, tasks[-2].id],
        )
        self.assertEqual(len(recent_one), 1)
        self.assertEqual(len(recent_fifty), 50)

        paths = [route.path for route in tasks_router.routes]
        self.assertLess(paths.index("/recent"), paths.index("/{task_id}"))

    def test_recent_filters_by_exact_task_type(self) -> None:
        base = datetime(2026, 1, 1)
        older_cluster = self.add_task(
            task_type="cluster_persons",
            finished_at=base + timedelta(minutes=1),
        )
        self.add_task(task_type="scan", finished_at=base + timedelta(minutes=3))
        newer_cluster = self.add_task(
            task_type="cluster_persons",
            finished_at=base + timedelta(minutes=2),
        )

        with Session(self.engine) as session:
            clusters = list_recent_tasks(
                limit=10,
                task_type="cluster_persons",
                session=session,
            )

        self.assertEqual(
            [row.id for row in clusters],
            [newer_cluster.id, older_cluster.id],
        )

    def test_active_includes_pending_after_running(self) -> None:
        base = datetime(2026, 1, 1)
        pending = self.add_task(
            status="pending", created_at=base, finished_at=None
        )
        running = self.add_task(
            status="running",
            created_at=base + timedelta(minutes=1),
            started_at=base + timedelta(minutes=1),
            finished_at=None,
        )
        with Session(self.engine) as session:
            response = list_active_tasks(session)
        self.assertEqual(
            [(row.id, str(row.status)) for row in response],
            [(running.id, "running"), (pending.id, "pending")],
        )


class FaceFeedApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        SQLModel.metadata.create_all(self.engine, tables=[Face.__table__])

    def tearDown(self) -> None:
        self.engine.dispose()

    def test_orphan_count_counts_only_unassigned_faces(self) -> None:
        with Session(self.engine) as session:
            session.add_all(
                [
                    Face(media_id=1, person_id=None, bbox=[0, 0, 1, 1]),
                    Face(media_id=2, person_id=None, bbox=[0, 0, 1, 1]),
                    Face(media_id=3, person_id=9, bbox=[0, 0, 1, 1]),
                ]
            )
            session.commit()
            response = get_orphan_count(session)

        self.assertEqual(response, {"count": 2})
        paths = [route.path for route in face_router.routes]
        self.assertLess(paths.index("/orphans/count"), paths.index("/orphans"))


class DuplicateTaskStatusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        SQLModel.metadata.create_all(
            self.engine,
            tables=[ProcessingTask.__table__],
        )

    def tearDown(self) -> None:
        self.engine.dispose()

    def test_completed_status_preserves_started_at(self) -> None:
        started = datetime(2026, 1, 1, 12, 0, 0)
        with Session(self.engine) as session:
            task = ProcessingTask(
                task_type="find_duplicates",
                status="running",
                started_at=started,
            )
            session.add(task)
            session.commit()
            session.refresh(task)

            DuplicateProcessor(task.id)._update_task_status(session, "completed")

            persisted = session.exec(
                select(ProcessingTask).where(ProcessingTask.id == task.id)
            ).one()
            self.assertEqual(persisted.started_at, started)
            self.assertIsNotNone(persisted.finished_at)


if __name__ == "__main__":
    unittest.main()
