"""Missing-file review and maintenance regressions (direct calls, no HTTP thread)."""
from __future__ import annotations

import importlib
import os
import tempfile
import unittest
from contextlib import contextmanager, nullcontext
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import text, update
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

_CONFIG_HOME = tempfile.TemporaryDirectory()
os.environ.setdefault("XDG_CONFIG_HOME", _CONFIG_HOME.name)

from app.config import MediaDirectory, settings  # noqa: E402
from app.database import _attach_engine_listeners  # noqa: E402
from app.models import (  # noqa: E402
    Face, Media, Person, PersonMediaLink, PersonSocialLink, ProcessingTask,
    TrainingDataset,
)
from app.schemas.missing import MissingBulkActionRequest  # noqa: E402
from app.services.task_summary import summarize_task  # noqa: E402

missing_api = importlib.import_module("app.api.missing")
maintenance = importlib.import_module("app.tasks.maintenance")


class MissingMaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "mounted"
        self.root.mkdir()
        (self.root / "mount-marker").write_text("mounted")
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
        )
        self.addCleanup(self.engine.dispose)
        _attach_engine_listeners(self.engine)
        SQLModel.metadata.create_all(self.engine)
        with self.engine.begin() as connection:
            for name, columns in (
                ("face", "face_id integer primary key, person_id integer"),
                ("person", "person_id integer"),
                ("media", "media_id integer primary key"),
            ):
                connection.exec_driver_sql(
                    f"CREATE VIRTUAL TABLE {name}_embeddings USING vec0({columns}, embedding float[512])"
                )
        self.session = Session(self.engine)
        self.addCleanup(self.session.close)
        self.enterContext(patch.object(maintenance.db, "engine", self.engine))
        self.enterContext(patch.object(settings.general, "presentation_mode", False))
        self.enterContext(patch.object(settings.general, "media_dirs", [MediaDirectory(path=self.root)]))
        self.enterContext(patch.object(settings.scan, "auto_cleanup_without_review", False))
        self.enterContext(patch.object(settings.scan, "auto_cleanup_grace_hours", 24))

    def media(self, name="missing.jpg", **values):
        path = Path(name) if Path(name).is_absolute() or name[1:2] == ":" else self.root / name
        row = Media(path=str(path), filename=path.name, size=1, **values)
        self.session.add(row)
        self.session.commit()
        return row.id

    def task(self, **values):
        task = ProcessingTask(task_type="clean_missing_files", **values)
        self.session.add(task)
        self.session.commit()
        return task.id

    def task_row(self, task_id):
        self.session.expire_all()
        return self.session.get(ProcessingTask, task_id)

    def run_task(self, task_id=None):
        task_id = task_id or self.task()
        maintenance.clean_missing_files(task_id)
        return self.task_row(task_id)

    def test_grace_compares_sqlite_naive_timestamp_with_utc_cutoff(self):
        media_id = self.media(missing_since=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=2))
        with patch.object(settings.scan, "auto_cleanup_without_review", True), patch.object(maintenance, "delete_record") as remove:
            task = self.run_task()
        self.assertEqual(task.status, "completed")
        self.assertEqual(task.result["removed"], 1)
        self.assertEqual(remove.call_args.args[0], media_id)

    def test_grace_keeps_recent_missing_record_for_review(self):
        media_id = self.media(missing_since=datetime.now(timezone.utc).replace(tzinfo=None))
        with patch.object(settings.scan, "auto_cleanup_without_review", True), patch.object(maintenance, "delete_record") as remove:
            task = self.run_task()
        remove.assert_not_called()
        self.assertEqual(task.result["awaiting_review"], 1)
        self.assertIsNotNone(self.session.get(Media, media_id).missing_since)

    def test_result_reports_flagged_recovered_removed_and_awaiting_review(self):
        self.media()
        self.media("still-missing.jpg", missing_since=datetime(2026, 1, 1))
        present = self.root / "recovered.jpg"
        present.touch()
        recovered_id = self.media(str(present), missing_since=datetime(2026, 1, 1), missing_confirmed=True)
        task = self.run_task()
        self.assertEqual(task.result, {
            "flagged": 1, "recovered": 1, "removed": 0, "awaiting_review": 2,
            "skipped_unmounted_roots": [],
        })
        self.assertIsNone(self.session.get(Media, recovered_id).missing_since)
        self.assertEqual(task.processed, 3)

    def test_missing_and_empty_roots_are_skipped_without_flagging_or_deleting(self):
        empty = Path(self.temp.name) / "empty"
        empty.mkdir()
        absent = Path(self.temp.name) / "unmounted"
        empty_id = self.media(str(empty / "gone.jpg"))
        absent_id = self.media(str(absent / "gone.jpg"), missing_since=datetime(2026, 1, 1))
        self.media()
        with patch.object(settings.general, "media_dirs", [MediaDirectory(path=p) for p in (self.root, empty, absent)]), patch.object(settings.scan, "auto_cleanup_without_review", True), patch.object(maintenance, "delete_record") as remove:
            task = self.run_task()
        remove.assert_not_called()
        self.assertIsNone(self.session.get(Media, empty_id).missing_since)
        self.assertEqual(self.session.get(Media, absent_id).missing_since, datetime(2026, 1, 1))
        self.assertEqual(task.result["skipped_unmounted_roots"], [str(empty), str(absent)])
        self.assertEqual(task.result["flagged"], 1)

    def test_unavailable_root_guard_respects_path_boundaries(self):
        absent = Path(self.temp.name) / "a"
        sibling = Path(self.temp.name) / "ab"
        sibling.mkdir()
        (sibling / "mount-marker").touch()
        media_id = self.media(str(sibling / "gone.jpg"))
        with patch.object(settings.general, "media_dirs", [MediaDirectory(path=p) for p in (absent, sibling)]):
            self.run_task()
        self.assertIsNotNone(self.session.get(Media, media_id).missing_since)

    def test_cancelled_lock_waiter_never_mutates_media_or_completes(self):
        media_id = self.media()
        task_id = self.task()

        @contextmanager
        def cancelled_writer(**_kwargs):
            with Session(self.engine) as session:
                session.exec(update(ProcessingTask).where(ProcessingTask.id == task_id).values(status="cancelled"))
                session.commit()
            yield False

        with patch.object(maintenance, "heavy_writer", cancelled_writer):
            task = self.run_task(task_id)
        self.assertEqual(task.status, "cancelled")
        self.assertIsNone(self.session.get(Media, media_id).missing_since)
        self.assertEqual(task.processed, 0)

    def test_task_cancelled_before_start_remains_cancelled(self):
        media_id = self.media()
        task = self.run_task(self.task(status="cancelled"))
        self.assertEqual(task.status, "cancelled")
        self.assertIsNone(self.session.get(Media, media_id).missing_since)

    def test_cancellation_is_checked_between_media_items(self):
        first_id, second_id = self.media(), self.media("second.jpg")
        task_id = self.task()
        original_exists = Path.exists

        def exists_then_cancel(path):
            if path == self.root / "missing.jpg":
                with Session(self.engine) as session:
                    session.exec(update(ProcessingTask).where(ProcessingTask.id == task_id).values(status="cancelled"))
                    session.commit()
                return False
            return original_exists(path)

        with patch.object(Path, "exists", exists_then_cancel):
            task = self.run_task(task_id)
        self.assertEqual(task.status, "cancelled")
        self.assertIsNotNone(self.session.get(Media, first_id).missing_since)
        self.assertIsNone(self.session.get(Media, second_id).missing_since)
        self.assertEqual(task.processed, 1)

    def test_cancellation_before_completion_cannot_be_overwritten(self):
        self.media()
        task_id = self.task()

        @contextmanager
        def cancelling_on_release(**_kwargs):
            yield True
            with Session(self.engine) as session:
                session.exec(update(ProcessingTask).where(ProcessingTask.id == task_id).values(status="cancelled"))
                session.commit()

        with patch.object(maintenance, "heavy_writer", cancelling_on_release):
            task = self.run_task(task_id)
        self.assertEqual(task.status, "cancelled")

    def test_completed_summary_points_to_missing_files_review(self):
        task = ProcessingTask(task_type="clean_missing_files", status="completed", result={"removed": 0, "flagged": 4, "awaiting_review": 7})
        self.assertEqual(summarize_task(task), "Flagged 4 missing · 7 awaiting review in Missing Files")

    def reset_with_reference(self, operation, reference):
        media_id = self.media()
        person = Person(name="Reset reference", appearance_count=1)
        self.session.add(person)
        self.session.flush()
        person_id = person.id
        face = Face(media_id=media_id, person_id=person_id, bbox=[0, 0, 1, 1])
        self.session.add(face)
        self.session.flush()
        face_id = face.id
        person.profile_face_id = face_id
        dataset_id = None
        if reference == "media_link":
            self.session.add(PersonMediaLink(person_id=person_id, media_id=media_id))
        elif reference == "social_link":
            self.session.add(PersonSocialLink(person_id=person_id, platform="example", handle="user", url="https://example.invalid/user"))
        else:
            dataset = TrainingDataset(name="Retained", slug="retained", person_id=person_id, trigger_word="subject", class_token="person")
            self.session.add(dataset)
            self.session.flush()
            dataset_id = dataset.id
        self.session.commit()
        with patch.object(maintenance, "heavy_writer", return_value=nullcontext(True)):
            self.assertEqual(operation(self.session), "OK")
        self.session.expire_all()
        self.assertIsNone(self.session.get(Person, person_id))
        self.assertIsNotNone(self.session.get(Media, media_id))
        if dataset_id is not None:
            self.assertIsNone(self.session.get(TrainingDataset, dataset_id).person_id)
        self.assertEqual(self.session.exec(text("PRAGMA foreign_key_check")).all(), [])
        if operation is maintenance.reset_clustering:
            self.assertEqual(self.session.get(Face, face_id).assignment_source, "reset")
        else:
            self.assertIsNone(self.session.get(Face, face_id))

    def test_reset_clustering_clears_person_media_links(self):
        self.reset_with_reference(maintenance.reset_clustering, "media_link")

    def test_reset_clustering_clears_person_social_links(self):
        self.reset_with_reference(maintenance.reset_clustering, "social_link")

    def test_reset_clustering_retains_dataset_without_person(self):
        self.reset_with_reference(maintenance.reset_clustering, "dataset")

    def test_reset_processing_clears_person_media_links(self):
        self.reset_with_reference(maintenance.reset_processing, "media_link")

    def test_reset_processing_clears_person_social_links(self):
        self.reset_with_reference(maintenance.reset_processing, "social_link")

    def test_reset_processing_retains_dataset_without_person(self):
        self.reset_with_reference(maintenance.reset_processing, "dataset")

    def test_reset_failure_rolls_back_all_changes(self):
        media_id = self.media(faces_extracted=True, embeddings_created=True)
        person = Person(name="Retained", appearance_count=1)
        self.session.add(person)
        self.session.flush()
        face = Face(media_id=media_id, person_id=person.id, bbox=[0, 0, 1, 1])
        self.session.add(face)
        self.session.commit()
        person_id, face_id = person.id, face.id
        original_exec = self.session.exec

        def fail_person_delete(statement, *args, **kwargs):
            if getattr(statement, "is_delete", False) and statement.table.name == "person":
                raise RuntimeError("injected reset failure")
            return original_exec(statement, *args, **kwargs)

        for operation in (maintenance.reset_clustering, maintenance.reset_processing):
            with self.subTest(operation=operation.__name__), patch.object(self.session, "exec", side_effect=fail_person_delete), patch.object(maintenance, "heavy_writer", return_value=nullcontext(True)):
                with self.assertRaisesRegex(RuntimeError, "injected"):
                    operation(self.session)
                self.session.expire_all()
                self.assertEqual(self.session.get(Face, face_id).person_id, person_id)
                self.assertTrue(self.session.get(Media, media_id).faces_extracted)

    def prefix_case(self, prefix, expected_path, excluded_path, action):
        expected_id = self.media(expected_path, missing_since=datetime(2026, 1, 1))
        excluded_id = self.media(excluded_path, missing_since=datetime(2026, 1, 1))
        page = missing_api.list_missing_media(session=self.session, limit=100, cursor=None, path_prefix=prefix, include_confirmed=False)
        self.assertEqual([item.id for item in page.items], [expected_id])
        self.assertEqual(page.total, 1)
        payload = MissingBulkActionRequest(select_all=True, path_prefix=prefix)
        self.assertEqual(missing_api._resolve_target_ids(self.session, payload), [expected_id])
        if action == "reset":
            self.assertEqual(missing_api.reset_missing_flags(payload, self.session).cleared, 1)
        else:
            with patch.object(missing_api, "delete_record") as remove:
                self.assertEqual(missing_api.confirm_missing(payload, self.session).deleted, 1)
            self.assertEqual([call.args[0] for call in remove.call_args_list], [expected_id])
        self.assertIsNotNone(self.session.get(Media, excluded_id).missing_since)

    def test_missing_prefix_uses_separator_boundary_for_list_count_and_reset_all(self):
        self.prefix_case("/photos/a/", "/photos/a/one.jpg", "/photos/ab/two.jpg", "reset")

    def test_missing_prefix_escapes_wildcards_for_list_count_and_confirm_all(self):
        self.prefix_case("/photos/50%_done/", "/photos/50%_done/one.jpg", "/photos/50wildXdone/two.jpg", "confirm")

    def test_missing_prefix_escapes_backslashes(self):
        self.prefix_case("C:\\photos\\a\\", "C:\\photos\\a\\one.jpg", "C:\\photos\\ab\\two.jpg", "reset")


if __name__ == "__main__":
    unittest.main()
