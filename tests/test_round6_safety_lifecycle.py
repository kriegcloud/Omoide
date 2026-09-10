"""Regression proofs for round 6 task lifecycle and application safety."""

import asyncio
import importlib
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import ExitStack, nullcontext
from pathlib import Path
from unittest.mock import Mock, patch

_CONFIG_HOME = tempfile.TemporaryDirectory()
os.environ.setdefault("XDG_CONFIG_HOME", _CONFIG_HOME.name)

import httpx
from fastapi import BackgroundTasks, HTTPException
from sqlalchemy.exc import OperationalError
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.config import settings
from app.models import Media, ProcessingTask
tasks_api = importlib.import_module("app.api.tasks")
from app.tasks.common import _finish_task, _start_task, create_and_run_task

blur = importlib.import_module("app.tasks.blur")
resume = importlib.import_module("app.tasks.resume")
main = importlib.import_module("app.main")
ROOT = Path(__file__).resolve().parents[1]


async def request(path):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=main.app), base_url="http://127.0.0.1"
    ) as client:
        return await client.get(path)


class TaskLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        self.addCleanup(self.engine.dispose)
        SQLModel.metadata.create_all(self.engine, tables=[Media.__table__, ProcessingTask.__table__])
        self.session = Session(self.engine)
        self.addCleanup(self.session.close)
        self.enterContext(patch("app.database.engine", self.engine))
        self.enterContext(patch.object(settings.general, "presentation_mode", False))

    def task(self, **kwargs):
        task = ProcessingTask(task_type="compute_blur_scores", params={}, **kwargs)
        self.session.add(task)
        self.session.commit()
        self.session.refresh(task)
        return task

    def media(self):
        media = Media(path="/tmp/round6-blur.jpg", filename="round6-blur.jpg", size=1)
        self.session.add(media)
        self.session.commit()
        self.session.refresh(media)
        return media

    def test_pending_task_is_reused_before_worker_starts(self):
        existing = self.task()
        background = BackgroundTasks()
        actual = create_and_run_task(self.session, background, "compute_blur_scores", Mock())
        self.assertEqual(actual.id, existing.id)
        self.assertEqual(background.tasks, [])

    def test_blur_marks_running_before_scoring_and_records_lifecycle_times(self):
        task = self.task()
        self.media()
        observed = []

        def score(*_):
            with Session(self.engine) as read:
                row = read.get(ProcessingTask, task.id)
                observed.append((row.status, row.started_at))
            return 1.5

        with patch.object(blur, "_score_media", side_effect=score), patch.object(blur, "heavy_writer", return_value=nullcontext(True)):
            blur.compute_blur_scores(task.id)
        self.session.refresh(task)
        self.assertEqual([status for status, _ in observed], ["running"])
        self.assertIsNotNone(observed[0][1])
        self.assertEqual(task.status, "completed")
        self.assertIsNotNone(task.finished_at)
        self.assertEqual(task.processed, 1)

    def test_blur_does_not_run_when_writer_lock_was_not_acquired(self):
        task = self.task()
        media = self.media()
        with patch.object(blur, "_score_media", return_value=1.5) as score, patch.object(blur, "heavy_writer", return_value=nullcontext(False)):
            blur.compute_blur_scores(task.id)
        self.session.refresh(media)
        score.assert_not_called()
        self.assertIsNone(media.laplacian_score)
        self.session.refresh(task)
        self.assertNotEqual(task.status, "completed")

    def test_blur_does_not_complete_an_interrupted_task(self):
        task = self.task()
        media = self.media()

        def score(*_):
            with Session(self.engine) as update:
                row = update.get(ProcessingTask, task.id)
                row.status = "interrupted"
                update.add(row)
                update.commit()
            return 1.5

        with patch.object(blur, "_score_media", side_effect=score), patch.object(blur, "heavy_writer", return_value=nullcontext(True)):
            blur.compute_blur_scores(task.id)
        self.session.refresh(task)
        self.session.refresh(media)
        self.assertEqual(task.status, "interrupted")
        self.assertIsNone(media.laplacian_score)

    def test_shared_start_does_not_revive_terminal_tasks(self):
        for status in ("cancelled", "interrupted", "failed", "completed"):
            with self.subTest(status=status):
                task = self.task(status=status)
                self.assertFalse(_start_task(self.session, task))
                self.session.refresh(task)
                self.assertEqual(task.status, status)
                self.assertIsNone(task.started_at)

    def test_shared_finish_preserves_an_interruption_newer_than_cached_task(self):
        task = self.task(status="running")
        with Session(self.engine) as another:
            row = another.get(ProcessingTask, task.id)
            row.status = "interrupted"
            another.add(row)
            another.commit()
        self.assertEqual(task.status, "running")
        self.assertFalse(_finish_task(self.session, task, "completed"))
        self.session.refresh(task)
        self.assertEqual(task.status, "interrupted")

    def test_shutdown_rejects_new_resumes_without_creating_a_successor(self):
        task = self.task(status="interrupted")
        resume.stop_resumed_tasks()
        self.addCleanup(resume.accept_resumed_tasks)
        with self.assertRaises(HTTPException) as raised:
            resume.resume_task(self.session, task)
        self.assertEqual(raised.exception.status_code, 503)
        self.session.refresh(task)
        self.assertNotIn("resumed_by", task.result or {})

    def test_worker_join_uses_one_shared_deadline(self):
        first, second = Mock(), Mock()
        for worker in (first, second):
            worker.is_alive.return_value = True
        with patch.object(resume, "_worker_threads", {first, second}), patch.object(resume, "monotonic", side_effect=[100.0, 100.0, 105.0]):
            self.assertEqual(resume.join_resumed_workers(timeout=5), 2)
        self.assertEqual(
            sorted(worker.join.call_args.kwargs["timeout"] for worker in (first, second)),
            [0.0, 5.0],
        )

    def test_active_task_read_uses_three_fresh_sessions_then_503(self):
        locked = OperationalError("SELECT", {}, RuntimeError("database is locked"))
        attempts = []

        def failing_session(*_):
            fresh = Mock()
            fresh.__enter__ = Mock(return_value=fresh)
            fresh.__exit__ = Mock(return_value=False)
            fresh.exec.side_effect = locked
            attempts.append(fresh)
            return fresh

        self.session.exec = Mock(side_effect=locked)
        with patch.object(tasks_api, "Session", side_effect=failing_session), patch.object(tasks_api.time, "sleep", side_effect=[None, None, AssertionError("unbounded retry")]):
            with self.assertRaises(HTTPException) as raised:
                tasks_api.list_active_tasks(self.session)
        self.assertEqual(raised.exception.status_code, 503)
        self.assertEqual(len(attempts), 3)
        self.assertTrue(all(s.__exit__.called for s in attempts))
        self.session.exec.assert_not_called()

    def test_active_task_read_recovers_on_fresh_session(self):
        locked = OperationalError("SELECT", {}, RuntimeError("database is locked"))
        first, second = Mock(), Mock()
        first.exec.side_effect = locked
        second.exec.return_value.all.return_value = []
        for session in (first, second):
            session.__enter__ = Mock(return_value=session)
            session.__exit__ = Mock(return_value=False)
        self.session.exec = Mock(side_effect=locked)
        with patch.object(tasks_api, "Session", side_effect=[first, second]) as factory, patch.object(tasks_api.time, "sleep", side_effect=[None, AssertionError("did not retry with fresh session")]):
            actual = tasks_api.list_active_tasks(self.session)
        self.assertEqual(actual, [])
        self.assertEqual(factory.call_count, 2)
        first.__exit__.assert_called_once()
        second.__exit__.assert_called_once()


class ApplicationLifecycleTests(unittest.TestCase):
    def setUp(self):
        resume.accept_resumed_tasks()
        self.addCleanup(resume.accept_resumed_tasks)

    def lifespan_patches(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        for name in (
            "load_processors", "apply_pillow_limits", "_apply_migrations_once",
            "ensure_vec_tables", "ensure_ffmpeg_available", "_cleanup_tasks_on_startup",
            "configure_auto_scan_job", "configure_training_reconcile_job", "_cleanup_tasks_on_shutdown",
        ):
            stack.enter_context(patch.object(main, name))
        stack.enter_context(patch.object(settings.processors, "image_embedding_processor_active", False))
        stack.enter_context(patch("app.annotation_tasks.start_annotation_reconciliation"))
        stack.enter_context(patch("app.annotation_tasks.stop_annotation_reconciliation"))
        stack.enter_context(patch.object(main, "scheduler"))

        async def run_shutdown_join(callback, *args):
            # Thread scheduling is outside this lifecycle contract; the worker
            # doubles joined below do not block and need no executor handoff.
            return callback(*args)

        stack.enter_context(patch.object(main.to_thread, "run_sync", side_effect=run_shutdown_join))
        # Restore state across tests and across other existing lifecycle suites.
        stack.enter_context(patch.object(main.app.state, "ready", False, create=True))
        stack.enter_context(patch.object(main.app.state, "migration_head", "2c3d4e5f6071", create=True))
        return stack

    def test_scheduler_is_stopped_when_lifespan_exits(self):
        self.lifespan_patches()

        async def run():
            async with main.lifespan(main.app):
                pass

        asyncio.run(run())
        main.scheduler.shutdown.assert_called_once_with(wait=False)

    def test_resumed_thread_is_joined_on_shutdown(self):
        self.lifespan_patches()
        engine = create_engine("sqlite://")
        self.addCleanup(engine.dispose)
        SQLModel.metadata.create_all(engine, tables=[ProcessingTask.__table__])
        with Session(engine) as session, patch.object(settings.general, "presentation_mode", False), patch.object(resume, "Thread") as factory:
            factory.return_value.is_alive.return_value = True
            old = ProcessingTask(task_type="scan", status="interrupted", params={})
            session.add(old)
            session.commit()

            async def run():
                async with main.lifespan(main.app):
                    resume.resume_task(session, old)

            asyncio.run(run())
            factory.return_value.join.assert_called_once()
            timeout = factory.return_value.join.call_args.kwargs.get("timeout")
            self.assertIsInstance(timeout, (int, float))
            self.assertGreaterEqual(timeout, 0)
            self.assertLessEqual(timeout, 10)
            # Do not retain fake workers in the process-wide registry.
            factory.return_value.is_alive.return_value = False
            if hasattr(resume, "join_resumed_workers"):
                resume.join_resumed_workers(timeout=0)

    def test_migration_failure_prevents_startup(self):
        self.lifespan_patches()
        main._apply_migrations_once.side_effect = RuntimeError("failed migration")

        async def run():
            async with main.lifespan(main.app):
                self.fail("application served after failed migration")

        with self.assertRaisesRegex(RuntimeError, "failed migration"):
            asyncio.run(run())
        main.configure_auto_scan_job.assert_not_called()
        self.assertFalse(main.app.state.ready)

    def test_health_returns_503_until_migrations_are_ready(self):
        with patch.object(main.app.state, "ready", False, create=True):
            response = asyncio.run(request("/api/health"))
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.headers["content-type"], "application/json")

    def test_health_returns_current_migration_head_after_readiness(self):
        with patch.object(main.app.state, "ready", True, create=True), patch.object(main.app.state, "migration_head", "2c3d4e5f6071", create=True), patch.object(main.db, "get_migration_state", return_value=main.db.MigrationState.APPLIED):
            response = asyncio.run(request("/api/health"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok", "migrations": "2c3d4e5f6071"})

    def test_health_is_not_ready_after_database_engine_changes(self):
        with patch.object(main.app.state, "ready", True, create=True), patch.object(main.app.state, "migration_head", "2c3d4e5f6071", create=True), patch.object(main.db, "get_migration_state", return_value=main.db.MigrationState.NOT_ATTEMPTED):
            response = asyncio.run(request("/api/health"))
        self.assertEqual(response.status_code, 503)

    def test_migration_success_records_the_applied_database_revision(self):
        engine = create_engine("sqlite://")
        self.addCleanup(engine.dispose)
        with engine.begin() as connection:
            connection.exec_driver_sql("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
            connection.exec_driver_sql("INSERT INTO alembic_version VALUES ('2c3d4e5f6071')")
        with patch.object(main, "_migrations_applied", False), patch.object(main.db, "run_migrations"), patch.object(main.db, "engine", engine), patch.object(main.app.state, "migration_head", None, create=True), patch.object(main.app.state, "ready", True, create=True):
            main._apply_migrations_once()
            self.assertEqual(main.app.state.migration_head, "2c3d4e5f6071")
            self.assertTrue(main._migrations_applied)
            self.assertFalse(main.app.state.ready)

    def test_unknown_api_route_returns_json_404_even_with_frontend_installed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "index.html").write_text("<html><head></head><body>app</body></html>")
            with patch.object(settings.general, "static_dir", root):
                response = asyncio.run(request("/api/definitely-not-a-route"))
                frontend = asyncio.run(request("/people"))
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.headers["content-type"], "application/json")
        self.assertEqual(response.json(), {"detail": "Not Found"})
        self.assertEqual(frontend.status_code, 200)

    def test_docker_command_does_not_launch_server_after_migration_failure(self):
        command = next(line.removeprefix("CMD ") for line in (ROOT / "Dockerfile").read_text().splitlines() if line.startswith("CMD "))
        args = json.loads(command)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "alembic").write_text("#!/bin/sh\nexit 7\n")
            (root / "uvicorn").write_text("#!/bin/sh\ntouch \"$LAUNCHED_MARKER\"\n")
            (root / "alembic").chmod(0o755)
            (root / "uvicorn").chmod(0o755)
            marker = root / "launched"
            result = subprocess.run(args, env={**os.environ, "PATH": f"{directory}:/usr/bin:/bin", "LAUNCHED_MARKER": str(marker)}, capture_output=True, timeout=5)
            self.assertFalse(marker.exists(), "uvicorn started after alembic failed")
            self.assertNotEqual(result.returncode, 0)

    def test_workstation_healthcheck_targets_readiness(self):
        import yaml
        services = yaml.safe_load((ROOT / "docker-compose.workstation.yml").read_text())["services"]
        healthcheck = services["omoide"]["healthcheck"]["test"]
        self.assertIn("/api/health", " ".join(healthcheck))
        self.assertIn("status", " ".join(healthcheck))


if __name__ == "__main__":
    unittest.main()
