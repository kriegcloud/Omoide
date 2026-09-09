import importlib
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import inspect
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select


_CONFIG_HOME = tempfile.TemporaryDirectory()
os.environ["XDG_CONFIG_HOME"] = _CONFIG_HOME.name

from app.config import ScanSettings, _apply_env_overrides, settings  # noqa: E402
from app.database import get_session  # noqa: E402
from app.models import ProcessingTask  # noqa: E402
from app.services.task_summary import summarize_task  # noqa: E402
tasks_api = importlib.import_module("app.api.tasks")

from app.tasks.resume import build_resume_callable, is_resumable  # noqa: E402

main = importlib.import_module("app.main")


class TaskResumeTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.addCleanup(self.engine.dispose)
        SQLModel.metadata.create_all(self.engine, tables=[ProcessingTask.__table__])
        self.session = Session(self.engine)
        self.addCleanup(self.session.close)
        for target, attribute, value in (
            (settings.general, "presentation_mode", False),
            (settings.general, "media_dirs", [Path("/tmp/resume-test-media")]),
            (settings.scan, "auto_resume_interrupted_tasks", False),
        ):
            previous = getattr(target, attribute)
            setattr(target, attribute, value)
            self.addCleanup(setattr, target, attribute, previous)
        self.patch("app.database.engine", self.engine)
        self.runner = self.patch("app.tasks.common._run_task_guarded")
        self.thread = self.patch("app.tasks.resume.Thread")
        app = FastAPI()
        app.include_router(tasks_api.router, prefix="/api/tasks")
        app.dependency_overrides[get_session] = lambda: self.session
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def patch(self, target, *args, **kwargs):
        patcher = patch(target, *args, **kwargs)
        mock = patcher.start()
        self.addCleanup(patcher.stop)
        return mock

    def task(self, **changes):
        values = {"task_type": "scan", "status": "interrupted", "params": {}}
        values.update(changes)
        task = ProcessingTask(**values)
        self.session.add(task)
        self.session.commit()
        self.session.refresh(task)
        return task

    def test_task_creators_persist_params(self):
        cases = [
            ("scan", {}, {}),
            ("process_media", {}, {}),
            ("cluster_persons", {}, {}),
            ("find_duplicates", {}, {}),
            ("find_duplicates?threshold=7", {}, {"threshold": 7}),
            ("clean_missing_files", {}, {}),
            ("compute_blur_scores", {}, {}),
            ("build_events", {}, {}),
            ("geocode_places", {}, {}),
            ("backfill_demographics", {}, {}),
            ("backfill_face_timestamps", {}, {}),
            ("backfill_face_quality", {}, {}),
            ("run_processor/embedding_extractor?force=true", {}, {"processor_name": "embedding_extractor", "force": True}),
            ("run_processors_for_media", {"json": {"processor_names": ["faces", "blur"], "media_ids": [3, 8]}}, {"processor_names": ["faces", "blur"], "media_ids": [3, 8]}),
        ]
        for endpoint, request, expected in cases:
            with self.subTest(endpoint=endpoint):
                response = self.client.post(f"/api/tasks/{endpoint}", **request)
                self.assertEqual(response.status_code, 200, response.text)
                with Session(self.engine) as verification:
                    task = verification.get(ProcessingTask, response.json()["id"])
                    self.assertEqual(task.params, expected)
                    self.assertTrue(is_resumable(task))
        self.assertEqual(self.runner.call_count, len(cases))

    def test_cleanup_interrupts_only_resumable_running_tasks_and_deletes_pending(self):
        for cleanup in (main._cleanup_tasks_on_startup, main._cleanup_tasks_on_shutdown):
            with self.subTest(cleanup=cleanup.__name__):
                resumable = self.task(status="running")
                legacy = self.task(status="running", params=None)
                unsupported = self.task(status="running", task_type="export_dataset")
                pending = self.task(status="pending")
                completed = self.task(status="completed")
                ids = [task.id for task in (resumable, legacy, unsupported, pending, completed)]
                cleanup()
                with Session(self.engine) as verification:
                    for task_id, expected in zip(ids[:3], ("interrupted", "cancelled", "cancelled")):
                        actual = verification.get(ProcessingTask, task_id)
                        self.assertEqual(actual.status, expected)
                        self.assertIsNotNone(actual.finished_at)
                    self.assertIsNone(verification.get(ProcessingTask, ids[3]))
                    self.assertEqual(verification.get(ProcessingTask, ids[4]).status, "completed")
                self.thread.assert_not_called()

    def test_resume_endpoint_persists_lineage_and_preserves_original_params_and_result(self):
        params = {"processor_name": "embedding_extractor", "force": True}
        old = self.task(task_type="run_processor", params=params, result={"files": 3})
        old_id = old.id
        response = self.client.post(f"/api/tasks/{old_id}/resume")
        self.assertEqual(response.status_code, 201, response.text)
        new = response.json()
        self.assertNotEqual(new["id"], old_id)
        self.assertEqual(new["task_type"], "run_processor")
        self.assertEqual(new["params"], {**params, "resumed_from": old_id})
        self.assertEqual(new["status"], "pending")
        self.assertEqual(new["processed"], 0)
        self.assertTrue(new["resumable"])
        self.assertIsNone(new["resumed_by"])
        self.session.refresh(old)
        self.assertEqual(old.result, {"files": 3, "resumed_by": new["id"]})
        self.assertEqual(old.params, params)
        self.assertEqual(old.status, "interrupted")
        self.assertIsNone(old.finished_at)
        kwargs = self.thread.call_args.kwargs
        self.assertIs(kwargs["target"], self.runner)
        self.assertEqual(kwargs["args"][1], new["id"])
        self.assertTrue(kwargs["daemon"])
        self.thread.return_value.start.assert_called_once()
        with patch("app.tasks.resume.run_single_processor") as processor:
            kwargs["args"][0](new["id"])
            processor.assert_called_once_with(new["id"], "embedding_extractor", force=False)

    def test_resume_accepts_cancelled_and_failed(self):
        for status, task_type in (("cancelled", "scan"), ("failed", "compute_blur_scores")):
            old = self.task(status=status, task_type=task_type)
            response = self.client.post(f"/api/tasks/{old.id}/resume")
            self.assertEqual(response.status_code, 201, response.text)

    def test_resume_unknown_is_404(self):
        self.assertEqual(self.client.post("/api/tasks/missing/resume").status_code, 404)
        self.thread.assert_not_called()

    def test_resume_rejects_invalid_status_or_contract(self):
        cases = [
            {"status": "completed"}, {"status": "pending"}, {"status": "running"},
            {"params": None}, {"task_type": "unknown"},
            {"task_type": "run_processor", "params": {}},
            {"task_type": "run_processor_for_media", "params": {"processor_names": ["faces"], "media_ids": ["bad"]}},
        ]
        for changes in cases:
            with self.subTest(changes=changes):
                task = self.task(**changes)
                response = self.client.post(f"/api/tasks/{task.id}/resume")
                self.assertEqual(response.status_code, 400, response.text)
        self.thread.assert_not_called()

    def test_resume_conflicts_with_running_or_pending_task_of_same_type(self):
        for status in ("running", "pending"):
            with self.subTest(status=status):
                active = self.task(status=status)
                old = self.task()
                response = self.client.post(f"/api/tasks/{old.id}/resume")
                self.assertEqual(response.status_code, 409, response.text)
                self.session.refresh(old)
                self.assertIsNone(old.result)
                self.session.delete(active)
                self.session.commit()
        self.thread.assert_not_called()

    def test_repeated_resume_does_not_create_another_successor(self):
        old = self.task()
        old_id = old.id
        new = self.client.post(f"/api/tasks/{old_id}/resume").json()
        successor = self.session.get(ProcessingTask, new["id"])
        successor.status = "completed"
        self.session.commit()
        response = self.client.post(f"/api/tasks/{old_id}/resume")
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(len(self.session.exec(select(ProcessingTask)).all()), 2)
        self.assertEqual(self.thread.call_count, 1)

    def test_resume_respects_presentation_mode(self):
        old = self.task()
        settings.general.presentation_mode = True
        self.assertEqual(self.client.post(f"/api/tasks/{old.id}/resume").status_code, 403)
        self.thread.assert_not_called()

    def test_task_reads_include_resume_metadata_and_interrupted_history(self):
        task = self.task(result={"resumed_by": "successor"}, finished_at=datetime.now())
        for endpoint in (f"/{task.id}", "/", "/recent"):
            response = self.client.get(f"/api/tasks{endpoint}")
            self.assertEqual(response.status_code, 200, response.text)
            body = response.json()
            read = body[0] if isinstance(body, list) else body
            self.assertEqual(read["params"], {})
            self.assertTrue(read["resumable"])
            self.assertEqual(read["resumed_by"], "successor")
            self.assertEqual(read["status"], "interrupted")
        self.assertEqual(self.client.get("/api/tasks/active").json(), [])
        active = self.task(status="running", params=None)
        read = self.client.get("/api/tasks/active").json()[0]
        self.assertEqual(read["id"], active.id)
        self.assertFalse(read["resumable"])
        self.assertIsNone(read["resumed_by"])
        self.assertIsNone(read["params"])

    def test_startup_auto_resume_is_opt_in(self):
        self.assertFalse(ScanSettings().auto_resume_interrupted_tasks)
        self.task()
        main._cleanup_tasks_on_startup()
        self.thread.assert_not_called()

    def test_startup_auto_resume_enqueues_only_eligible_interrupted_tasks(self):
        settings.scan.auto_resume_interrupted_tasks = True
        running = self.task(status="running")
        interrupted = self.task(task_type="compute_blur_scores")
        self.task(task_type="build_events", status="cancelled")
        self.task(task_type="geocode_places", status="failed")
        self.task(task_type="process_media", params=None)
        self.task(task_type="backfill_demographics", result={"resumed_by": "already"})
        old_ids = [running.id, interrupted.id]
        main._cleanup_tasks_on_startup()
        self.assertEqual(self.thread.call_count, 2)
        with Session(self.engine) as verification:
            for old_id in old_ids:
                old = verification.get(ProcessingTask, old_id)
                self.assertEqual(old.status, "interrupted")
                new = verification.get(ProcessingTask, old.result["resumed_by"])
                self.assertEqual(new.params, {"resumed_from": old_id})
        self.runner.assert_not_called()

    def test_auto_resume_conflict_does_not_skip_other_types(self):
        settings.scan.auto_resume_interrupted_tasks = True
        self.task()
        self.task()
        self.task(task_type="compute_blur_scores")
        main._cleanup_tasks_on_startup()
        self.assertEqual(self.thread.call_count, 2)

    def test_cleanup_clears_link_to_deleted_pending_successor(self):
        old = self.task(result={"files": 3})
        old_id = old.id
        new = self.client.post(f"/api/tasks/{old_id}/resume").json()
        main._cleanup_tasks_on_shutdown()
        self.session.expire_all()
        self.assertIsNone(self.session.get(ProcessingTask, new["id"]))
        self.assertEqual(self.session.get(ProcessingTask, old_id).result, {"files": 3})
        self.assertEqual(self.client.post(f"/api/tasks/{old_id}/resume").status_code, 201)

    def test_environment_enables_auto_resume_setting(self):
        config = {"scan": ScanSettings().model_dump()}
        with patch.dict(os.environ, {"OMOIDE_SCAN__AUTO_RESUME_INTERRUPTED_TASKS": "true"}, clear=True):
            _apply_env_overrides(config)
        self.assertTrue(ScanSettings.model_validate(config["scan"]).auto_resume_interrupted_tasks)

    def test_selection_resume_replays_original_ids(self):
        task = self.task(task_type="run_processor_for_media", params={"processor_names": ["faces", "blur"], "media_ids": [8, 3]})
        with patch("app.tasks.resume.run_processors_for_media") as processor:
            build_resume_callable(task)("new-id")
            processor.assert_called_once_with("new-id", ["faces", "blur"], [8, 3])

    def test_duplicate_resume_uses_default_or_persisted_threshold(self):
        for params, threshold in (({}, 2), ({"threshold": 7}, 7)):
            task = self.task(task_type="find_duplicates", params=params)
            with patch("app.tasks.resume.run_duplicate_detection") as processor:
                build_resume_callable(task)("new-id")
                processor.assert_called_once_with("new-id", threshold)

    def test_interrupted_summary_preserves_progress(self):
        self.assertEqual(summarize_task(self.task(total=8, processed=3)), "Interrupted at 3/8")
        self.assertEqual(summarize_task(self.task()), "Interrupted")

    def test_failed_thread_start_leaves_a_retryable_successor(self):
        self.thread.return_value.start.side_effect = RuntimeError("executor unavailable")
        old = self.task()
        with self.assertRaisesRegex(RuntimeError, "executor unavailable"):
            self.client.post(f"/api/tasks/{old.id}/resume")
        self.session.refresh(old)
        new = self.session.get(ProcessingTask, old.result["resumed_by"])
        self.assertEqual(new.status, "failed")
        self.assertIsNotNone(new.finished_at)
        self.assertTrue(is_resumable(new))


class TaskResumeMigrationTests(unittest.TestCase):
    def test_migration_preserves_legacy_rows_accepts_interrupted_and_downgrades(self):
        script = ScriptDirectory.from_config(Config("alembic.ini"))
        self.assertEqual(script.get_heads(), ["2c3d4e5f6071"])
        revision = script.get_revision("1b2c3d4e5f60")
        self.assertEqual(revision.down_revision, "0a1b2c3d4e5f")
        engine = create_engine("sqlite://")
        self.addCleanup(engine.dispose)
        with engine.begin() as connection:
            connection.exec_driver_sql("CREATE TABLE processingtask (id TEXT PRIMARY KEY, status TEXT NOT NULL)")
            connection.exec_driver_sql("INSERT INTO processingtask VALUES ('legacy', 'cancelled')")
            with Operations.context(MigrationContext.configure(connection)):
                revision.module.upgrade()
                self.assertEqual(inspect(connection).get_check_constraints("processingtask"), [])
                column = next(col for col in inspect(connection).get_columns("processingtask") if col["name"] == "params")
                self.assertTrue(column["nullable"])
                self.assertEqual(connection.exec_driver_sql("SELECT id, status, params FROM processingtask").one(), ("legacy", "cancelled", None))
                connection.exec_driver_sql("UPDATE processingtask SET status = 'interrupted', params = '{}' WHERE id = 'legacy'")
                revision.module.downgrade()
                self.assertNotIn("params", {col["name"] for col in inspect(connection).get_columns("processingtask")})
                self.assertEqual(connection.exec_driver_sql("SELECT status FROM processingtask").scalar_one(), "cancelled")


if __name__ == "__main__":
    unittest.main()
