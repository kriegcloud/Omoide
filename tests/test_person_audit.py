import importlib
import os
import tempfile
import unittest
from contextlib import nullcontext
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select


_CONFIG_HOME = tempfile.TemporaryDirectory()
os.environ["XDG_CONFIG_HOME"] = _CONFIG_HOME.name

from app.config import settings  # noqa: E402
from app.database import _attach_engine_listeners, get_session  # noqa: E402
from app.models import Face, Media, Person, PersonMediaLink  # noqa: E402

person_api = importlib.import_module("app.api.person")
face_api = importlib.import_module("app.api.face")
maintenance = importlib.import_module("app.tasks.maintenance")
clustering = importlib.import_module("app.tasks.person_clustering")


class PersonAuditTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.addCleanup(self.engine.dispose)
        _attach_engine_listeners(self.engine)
        SQLModel.metadata.create_all(self.engine)
        with self.engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE VIRTUAL TABLE person_embeddings USING vec0("
                "person_id integer, embedding float[512])"
            )
            connection.exec_driver_sql(
                "CREATE VIRTUAL TABLE face_embeddings USING vec0("
                "face_id integer primary key, person_id integer, embedding float[512])"
            )
            connection.exec_driver_sql(
                "CREATE VIRTUAL TABLE media_embeddings USING vec0("
                "media_id integer primary key, embedding float[512])"
            )
        self.session = Session(self.engine)
        self.addCleanup(self.session.close)
        self.enterContext(patch.object(settings.general, "presentation_mode", False))
        app = FastAPI()
        app.include_router(person_api.router, prefix="/api/person")
        app.include_router(face_api.router, prefix="/api/faces")
        app.dependency_overrides[get_session] = lambda: self.session
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def add_person(self, name="Audit person", appearance_count=7) -> int:
        person = Person(name=name, appearance_count=appearance_count)
        self.session.add(person)
        self.session.commit()
        return person.id

    def add_face(self, person_id) -> tuple[int, int]:
        media = Media(path=f"/fake/audit-{person_id}.jpg", filename="audit.jpg", size=1)
        self.session.add(media)
        self.session.flush()
        face = Face(media_id=media.id, person_id=person_id, bbox=[0, 0, 1, 1])
        self.session.add(face)
        self.session.commit()
        return face.id, media.id

    def assert_deleted(self, person_id):
        self.session.expire_all()
        self.assertIsNone(self.session.get(Person, person_id))

    def test_delete_endpoint_logs_identity_reason_and_appearance_count(self):
        name = "Audit 'name'\nsecond line"
        person_id = self.add_person(name=name)
        with self.assertLogs("app.utils", level="INFO") as logs:
            response = self.client.delete(f"/api/person/{person_id}")
        self.assertEqual(response.status_code, 204, response.text)
        self.assert_deleted(person_id)
        self.assertEqual(len(logs.records), 1)
        message = logs.records[0].getMessage()
        self.assertEqual(message, (
            f"person deleted id={person_id} name={name!r} "
            "reason=delete appearance_count=7"
        ))
        self.assertEqual(len(message.splitlines()), 1)

    def test_bulk_delete_logs_each_deleted_person_once(self):
        first = self.add_person("First")
        second = self.add_person(None)
        with self.assertLogs("app.utils", level="INFO") as logs:
            response = self.client.post(
                "/api/person/bulk-delete",
                json={"person_ids": [first, 9999, second, first]},
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"deleted_ids": [first, second], "skipped_ids": [9999]})
        self.assertEqual([record.getMessage() for record in logs.records], [
            f"person deleted id={first} name='First' reason=bulk-delete appearance_count=7",
            f"person deleted id={second} name=None reason=bulk-delete appearance_count=7",
        ])
        self.assert_deleted(first)
        self.assert_deleted(second)

    def test_merge_logs_source_and_target(self):
        source = self.add_person("Source")
        target = self.add_person("Target")
        with self.assertLogs("app.utils", level="INFO") as logs:
            response = self.client.post(
                "/api/person/merge", json={"source_id": source, "target_id": target}
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assert_deleted(source)
        self.assertEqual([record.getMessage() for record in logs.records], [
            f"person deleted id={source} name='Source' reason=merge-source appearance_count=7 target_id={target}",
        ])

    def test_all_empty_after_endpoints_log_the_operation(self):
        for operation, reason, count in (
            ("assign", "faces-assign", 7),
            ("detach", "faces-detach", 7),
            ("delete", "faces-delete", 7),
            ("create_person", "face-reassign", 7),
            ("reassign_media", "person-media-reassign", 0),
        ):
            with self.subTest(operation=operation):
                # Keep a target row so SQLite does not reuse a deleted source ID.
                target = self.add_person(f"Target {operation}")
                source = self.add_person(f"Source {operation}")
                face_id, media_id = self.add_face(source)
                with self.assertLogs("app.utils", level="INFO") as logs:
                    if operation == "delete":
                        response = self.client.delete("/api/faces/", params={"face_ids": face_id})
                    elif operation == "reassign_media":
                        response = self.client.post(
                            f"/api/person/{source}/media/{media_id}/reassign",
                            json={"target_person_id": target},
                        )
                    else:
                        body = {"face_ids": [face_id]}
                        if operation == "assign":
                            body["person_id"] = target
                        response = self.client.post(f"/api/faces/{operation}", json=body)
                self.assertIn(response.status_code, (200, 201), response.text)
                self.assert_deleted(source)
                self.assertEqual([record.getMessage() for record in logs.records], [
                    f"person deleted id={source} name='Source {operation}' reason=empty-after-{reason} appearance_count={count}",
                ])

    def test_no_audit_entry_when_person_is_retained_or_missing(self):
        source = self.add_person()
        _, media_id = self.add_face(source)
        manual_only = self.add_person("Manual")
        self.session.add(PersonMediaLink(person_id=manual_only, media_id=media_id))
        self.session.commit()
        with self.assertNoLogs("app.utils", level="INFO"):
            self.assertFalse(face_api.old_person_can_be_deleted(self.session, source, reason="test"))
            self.assertFalse(face_api.old_person_can_be_deleted(self.session, manual_only, reason="test"))
            self.assertTrue(face_api.old_person_can_be_deleted(self.session, 9999, reason="test"))
            self.assertTrue(face_api.old_person_can_be_deleted(self.session, None, reason="test"))
            self.assertEqual(self.client.delete("/api/person/9999").status_code, 404)

    def test_failed_delete_commit_does_not_log_a_deletion(self):
        source = self.add_person()
        with self.assertNoLogs("app.utils", level="INFO"):
            with patch("app.utils.safe_commit", side_effect=RuntimeError("commit failed")):
                with self.assertRaisesRegex(RuntimeError, "commit failed"):
                    self.client.delete(f"/api/person/{source}")
        self.session.rollback()
        self.assertIsNotNone(self.session.get(Person, source))

    def test_maintenance_resets_log_the_number_of_deleted_rows(self):
        for reset, reason in (
            (maintenance.reset_clustering, "reset-clustering"),
            (maintenance.reset_processing, "reset-processing"),
        ):
            with self.subTest(reason=reason):
                self.add_person("First")
                self.add_person("Second")
                with patch.object(maintenance, "heavy_writer", return_value=nullcontext()):
                    with self.assertLogs("app", level="INFO") as logs:
                        self.assertEqual(reset(self.session), "OK")
                self.assertEqual(self.session.exec(select(Person)).all(), [])
                self.assertEqual([record.getMessage() for record in logs.records], [
                    f"persons deleted count=2 reason={reason}",
                ])
                self.session.expunge_all()

    def test_clustering_cleanup_and_merge_have_distinct_reasons(self):
        empty = self.add_person("Empty")
        with self.assertLogs("app.utils", level="INFO") as logs:
            clustering.rebuild_person_embedding(self.session, empty)
        self.assert_deleted(empty)
        self.assertIn("reason=empty-after-centroid-rebuild", logs.records[0].getMessage())

        target = self.add_person("Target", appearance_count=2)
        source = self.add_person("Source", appearance_count=1)
        self.add_face(target)
        self.add_face(source)
        with self.assertLogs("app.utils", level="INFO") as logs:
            self.assertEqual(clustering._merge_person_pair(self.session, target, source), (target, source))
        self.assert_deleted(source)
        self.assertEqual([record.getMessage() for record in logs.records], [
            f"person deleted id={source} name='Source' reason=merge-source appearance_count=1 target_id={target}",
        ])


if __name__ == "__main__":
    unittest.main()
