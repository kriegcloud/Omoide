import importlib
import os
import tempfile
import unittest
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import numpy as np
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import inspect
from sqlalchemy.pool import StaticPool
from sqlalchemy.sql.dml import Delete
from sqlmodel import Session, SQLModel, create_engine, select, text


_CONFIG_HOME = tempfile.TemporaryDirectory()
os.environ["XDG_CONFIG_HOME"] = _CONFIG_HOME.name

from app.config import settings  # noqa: E402
from app.database import _attach_engine_listeners, get_session  # noqa: E402
from app.models import Face, FaceAssignmentSource, Media, Person  # noqa: E402
from app.services.face_matching import (  # noqa: E402
    _bulk_assign_faces_to_persons,
    match_faces_to_persons,
    matching_thresholds,
)
from app.services.face_provenance import stamp_face_assignment  # noqa: E402
from app.utils import vector_to_blob  # noqa: E402

face_api = importlib.import_module("app.api.face")
person_api = importlib.import_module("app.api.person")
clustering = importlib.import_module("app.tasks.person_clustering")
maintenance = importlib.import_module("app.tasks.maintenance")


class FaceAssignmentProvenanceTests(unittest.TestCase):
    def setUp(self):
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
        self.enterContext(patch.object(settings.general, "presentation_mode", False))
        self.app = FastAPI()
        self.app.include_router(face_api.router, prefix="/api/faces")
        self.app.include_router(person_api.router, prefix="/api/person")
        self.app.dependency_overrides[get_session] = lambda: self.session
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        self.vector = np.zeros(512, dtype=np.float32)
        self.vector[0] = 1.0
        self.counter = 0
        self.before = datetime.now(timezone.utc).replace(tzinfo=None)

    def person(self, name="Provenance person", **values):
        values.setdefault("appearance_count", 0)
        person = Person(name=name, **values)
        self.session.add(person)
        self.session.commit()
        return person.id

    def add_face(self, person_id=None, **values):
        self.counter += 1
        media = Media(
            path=f"/synthetic/provenance-{self.counter}.jpg",
            filename=f"provenance-{self.counter}.jpg", size=1,
        )
        self.session.add(media)
        self.session.flush()
        face = Face(
            media_id=media.id, person_id=person_id, bbox=[0, 0, 10, 10],
            thumbnail_path="provenance-test.jpg", frontality=0.9, det_score=0.99,
            **values,
        )
        self.session.add(face)
        self.session.flush()
        self.session.exec(text(
            "INSERT INTO face_embeddings(face_id, person_id, embedding) VALUES (:id, :pid, :vec)"
        ).bindparams(id=face.id, pid=person_id if person_id is not None else -1,
                     vec=vector_to_blob(self.vector)))
        self.session.commit()
        return face.id

    def face(self, face_id):
        self.session.expire_all()
        return self.session.get(Face, face_id)

    def assert_assignment(self, face_id, person_id, source):
        # Read through another session so an unflushed ORM stamp cannot pass.
        with Session(self.engine) as verification:
            face = verification.get(Face, face_id)
            self.assertEqual(face.person_id, person_id)
            self.assertEqual(face.assignment_source, source)
            self.assertIsInstance(face.assigned_at, datetime)
            self.assertIsNone(face.assigned_at.tzinfo)
            self.assertGreaterEqual(face.assigned_at, self.before)
            self.assertLessEqual(face.assigned_at, datetime.now(timezone.utc).replace(tzinfo=None))
            return face.assigned_at

    def post(self, path, body, status=200):
        response = self.client.post(path, json=body)
        self.assertEqual(response.status_code, status, response.text)
        return response.json()

    def assign(self, face_ids, person_id, **values):
        return self.post("/api/faces/assign", {"face_ids": face_ids, "person_id": person_id, **values})

    def recent(self, **params):
        response = self.client.get("/api/faces/assignments/recent", params=params)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_manual_assign_defaults_and_bulk_reassignment(self):
        source, target = self.person("Source"), self.person("Target")
        first, second = self.add_face(), self.add_face(source)
        self.assign([first, second], target)
        for face_id in (first, second):
            self.assert_assignment(face_id, target, "manual")

    def test_assign_passes_through_suggestion_source(self):
        target, face_id = self.person(), self.add_face()
        self.assign([face_id], target, source="suggestion")
        self.assert_assignment(face_id, target, "suggestion")

    def test_undo_reassigns_with_undo_source(self):
        target, face_id = self.person(), self.add_face()
        self.assign([face_id], target, source="undo")
        self.assert_assignment(face_id, target, "undo")

    def test_assign_rejects_server_sources_and_invalid_values_with_422(self):
        target, face_id = self.person(), self.add_face()
        for source in ("cluster", "auto_match", "merge", "detach", "reset", "unknown", None):
            with self.subTest(source=source):
                response = self.client.post("/api/faces/assign", json={
                    "face_ids": [face_id], "person_id": target, "source": source,
                })
                self.assertEqual(response.status_code, 422, response.text)
                self.assertIsNone(self.face(face_id).assigned_at)
                self.assertIsNone(self.face(face_id).person_id)

    def test_suggestion_accept_endpoint_stamps_default_and_explicit_source(self):
        target = self.person()
        for values in ({}, {"source": "suggestion"}):
            with self.subTest(values=values):
                first, second = self.add_face(), self.add_face()
                result = self.post("/api/faces/assign-suggested", {
                    "assignments": [{"face_id": fid, "person_id": target} for fid in (first, second)],
                    **values,
                })
                self.assertEqual(result["assigned"], 2)
                for face_id in (first, second):
                    self.assert_assignment(face_id, target, "suggestion")

    def test_suggestion_retry_and_skipped_faces_preserve_provenance(self):
        target, other = self.person(), self.person("Other")
        face_id = self.add_face()
        self.assign([face_id], target)
        before = self.face(face_id).assigned_at
        result = self.post("/api/faces/assign-suggested", {
            "assignments": [{"face_id": face_id, "person_id": other}],
        })
        self.assertEqual(result["assigned"], 0)
        self.assertEqual(self.face(face_id).assigned_at, before)
        self.assertEqual(self.face(face_id).assignment_source, "manual")

    def test_suggestion_endpoint_rejects_forged_server_source(self):
        self.post("/api/faces/assign-suggested", {"assignments": [], "source": "cluster"}, status=422)

    def test_same_person_assignment_preserves_last_change(self):
        target, face_id = self.person(), self.add_face()
        self.assign([face_id], target)
        before = self.face(face_id).assigned_at
        self.assign([face_id, face_id], target, source="undo")
        self.assertEqual(self.face(face_id).assigned_at, before)
        self.assertEqual(self.face(face_id).assignment_source, "manual")

    def test_create_person_from_faces_is_manual(self):
        face_ids = [self.add_face(), self.add_face()]
        result = self.post("/api/faces/create_person", {"face_ids": face_ids, "name": "Created"}, status=201)
        for face_id in face_ids:
            self.assert_assignment(face_id, result["id"], "manual")

    def test_auto_match_service_stamps_matched_row(self):
        target = self.person()
        self.add_face(target)
        face_id = self.add_face()
        threshold, margin = matching_thresholds()
        result = match_faces_to_persons(self.session, [face_id], threshold=threshold, min_margin=margin)
        self.assertEqual(result, {face_id: target})
        self.assert_assignment(face_id, target, "auto_match")

    def test_auto_match_bulk_chunks_stamp_changed_faces_only(self):
        target, other = self.person(), self.person("Other")
        unchanged = self.add_face(target)
        changed = [self.add_face(), self.add_face(), self.add_face()]
        _bulk_assign_faces_to_persons(
            self.session, {unchanged: target, changed[0]: target, changed[1]: target, changed[2]: other},
            chunk_size=1,
        )
        self.session.commit()
        for face_id, person_id in ((changed[0], target), (changed[1], target), (changed[2], other)):
            self.assert_assignment(face_id, person_id, "auto_match")
        self.assertIsNone(self.face(unchanged).assigned_at)

    def test_cluster_creation_stamps_every_member(self):
        count = max(settings.face_recognition.person_min_face_count, settings.face_recognition.person_min_media_count)
        face_ids = [self.add_face() for _ in range(count)]
        with patch.object(clustering.db, "engine", self.engine):
            created, people = clustering._assign_faces_to_clusters(
                {0: (face_ids, [self.vector.copy() for _ in face_ids])}, "provenance-task", update_progress=False,
            )
        self.assertEqual(created, 1)
        for face_id in face_ids:
            self.assert_assignment(face_id, people[0], "cluster")

    def test_cluster_drop_keep_merge_uses_cluster_source(self):
        keep, drop = self.person("Keep", appearance_count=2), self.person("Drop", appearance_count=1)
        unchanged, moved = self.add_face(keep), self.add_face(drop)
        self.assertEqual(clustering._merge_person_pair(self.session, keep, drop), (keep, drop))
        self.assert_assignment(moved, keep, "cluster")
        self.assertIsNone(self.face(unchanged).assigned_at)

    def test_person_merge_stamps_moved_faces_only(self):
        target, source = self.person("Target"), self.person("Source")
        unchanged, moved = self.add_face(target), self.add_face(source)
        self.post("/api/person/merge", {"source_id": source, "target_id": target})
        self.assert_assignment(moved, target, "merge")
        self.assertIsNone(self.face(unchanged).assigned_at)

    def test_multiple_person_merge_stamps_all_sources(self):
        target = self.person("Target")
        sources = [self.person("First"), self.person("Second")]
        faces = [self.add_face(source) for source in sources]
        self.post(f"/api/person/{target}/merge-multiple", {"source_ids": sources})
        for face_id in faces:
            self.assert_assignment(face_id, target, "merge")

    def test_media_reassignment_uses_merge_source(self):
        source, target = self.person("Source"), self.person("Target")
        face_id = self.add_face(source)
        media_id = self.face(face_id).media_id
        self.post(f"/api/person/{source}/media/{media_id}/reassign", {"target_person_id": target})
        self.assert_assignment(face_id, target, "merge")

    def test_single_face_detach_stamps_cleared_assignment(self):
        source = self.person()
        face_id = self.add_face(source)
        self.post("/api/faces/detach", {"face_ids": [face_id]})
        self.assert_assignment(face_id, None, "detach")

    def test_bulk_face_detach_stamps_every_cleared_assignment(self):
        source = self.person()
        face_ids = [self.add_face(source), self.add_face(source)]
        self.post("/api/faces/detach", {"face_ids": face_ids})
        for face_id in face_ids:
            self.assert_assignment(face_id, None, "detach")

    def test_single_media_detach_stamps_cleared_assignment(self):
        source = self.person()
        face_id = self.add_face(source)
        media_id = self.face(face_id).media_id
        self.post(f"/api/person/{source}/media/{media_id}/detach", {})
        self.assert_assignment(face_id, None, "detach")

    def test_bulk_media_detach_stamps_every_cleared_assignment(self):
        source = self.person()
        face_ids = [self.add_face(source), self.add_face(source)]
        media_ids = [self.face(fid).media_id for fid in face_ids]
        self.post(f"/api/person/{source}/media/bulk-detach", {"media_ids": media_ids})
        for face_id in face_ids:
            self.assert_assignment(face_id, None, "detach")

    def test_person_delete_detaches_surviving_faces_with_provenance(self):
        source = self.person()
        face_id = self.add_face(source)
        response = self.client.delete(f"/api/person/{source}")
        self.assertEqual(response.status_code, 204, response.text)
        self.assert_assignment(face_id, None, "detach")

    def test_bulk_person_delete_detaches_surviving_faces_with_provenance(self):
        sources = [self.person("First"), self.person("Second")]
        face_ids = [self.add_face(source) for source in sources]
        self.post("/api/person/bulk-delete", {"person_ids": sources})
        for face_id in face_ids:
            self.assert_assignment(face_id, None, "detach")

    def test_detach_of_orphan_preserves_previous_provenance(self):
        before = datetime(2020, 1, 1)
        face_id = self.add_face(assigned_at=before, assignment_source="reset")
        self.post("/api/faces/detach", {"face_ids": [face_id]})
        self.assertEqual(self.face(face_id).assigned_at, before)
        self.assertEqual(self.face(face_id).assignment_source, "reset")

    def test_reset_clustering_stamps_assigned_faces_and_preserves_orphans(self):
        source = self.person()
        face_id, orphan = self.add_face(source), self.add_face()
        with patch.object(maintenance, "heavy_writer", return_value=nullcontext()):
            self.assertEqual(maintenance.reset_clustering(self.session), "OK")
        self.assert_assignment(face_id, None, "reset")
        self.assertIsNone(self.face(orphan).assigned_at)

    def test_reset_processing_stamps_before_intentionally_deleting_faces(self):
        source = self.person()
        face_id = self.add_face(source)
        original_exec = self.session.exec
        observed = []

        def observe(statement, *args, **kwargs):
            if isinstance(statement, Delete) and statement.table.name == "face":
                row = original_exec(select(Face).where(Face.id == face_id)).one()
                observed.append((row.person_id, row.assignment_source, row.assigned_at))
            return original_exec(statement, *args, **kwargs)

        with (
            patch.object(maintenance, "heavy_writer", return_value=nullcontext()),
            patch.object(self.session, "exec", side_effect=observe),
        ):
            self.assertEqual(maintenance.reset_processing(self.session), "OK")
        self.assertEqual(len(observed), 1)
        self.assertEqual(observed[0][:2], (None, "reset"))
        self.assertGreaterEqual(observed[0][2], self.before)
        self.assertIsNone(self.face(face_id))

    def test_assignment_and_provenance_roll_back_together(self):
        target, face_id = self.person(), self.add_face()
        stamp_face_assignment(self.face(face_id), target, FaceAssignmentSource.UNDO)
        self.session.flush()
        self.session.rollback()
        face = self.face(face_id)
        self.assertIsNone(face.person_id)
        self.assertIsNone(face.assigned_at)
        self.assertIsNone(face.assignment_source)

    def test_recent_orders_by_last_change_excludes_nulls_and_includes_detached_faces(self):
        target = self.person("Named person")
        base = datetime(2024, 1, 1)
        newest = self.add_face(assigned_at=base + timedelta(days=2), assignment_source="detach")
        oldest = self.add_face(target, assigned_at=base, assignment_source="manual")
        middle = self.add_face(target, assigned_at=base + timedelta(days=1), assignment_source="suggestion")
        self.add_face(target)
        rows = self.recent()
        self.assertEqual([row["id"] for row in rows], [newest, middle, oldest])
        self.assertIsNone(rows[0]["person_id"])
        self.assertIsNone(rows[0]["person_name"])
        self.assertEqual(rows[1]["person_name"], "Named person")
        self.assertEqual(set(rows[0]), {
            "id", "media_id", "person_id", "person_name", "thumbnail_path", "assigned_at", "assignment_source",
        })
        self.assertEqual(rows[0]["assigned_at"], "2024-01-03T00:00:00")

    def test_recent_source_and_person_filters_combine(self):
        first, second = self.person("First"), self.person("Second")
        stamp = datetime(2024, 1, 1)
        a = self.add_face(first, assigned_at=stamp, assignment_source="suggestion")
        b = self.add_face(second, assigned_at=stamp, assignment_source="suggestion")
        c = self.add_face(first, assigned_at=stamp, assignment_source="manual")
        self.assertEqual([row["id"] for row in self.recent(source="suggestion")], [b, a])
        self.assertEqual([row["id"] for row in self.recent(person_id=first)], [c, a])
        self.assertEqual([row["id"] for row in self.recent(source="suggestion", person_id=first)], [a])
        self.assertEqual(self.recent(person_id=0), [])
        self.assertEqual(self.recent(person_id=99999), [])

    def test_recent_accepts_every_enum_source_and_rejects_unknown(self):
        for source in FaceAssignmentSource:
            self.assertEqual(self.recent(source=source.value), [])
        self.assertEqual(self.client.get("/api/faces/assignments/recent?source=unknown").status_code, 422)

    def test_recent_limit_defaults_and_clamps_both_bounds(self):
        first = self.add_face(assigned_at=datetime(2024, 1, 1), assignment_source="manual")
        media_id = self.face(first).media_id
        self.session.add_all([
            Face(media_id=media_id, bbox=[0, 0, 1, 1], assigned_at=datetime(2024, 1, 1),
                 assignment_source="manual")
            for _ in range(500)
        ])
        self.session.commit()
        self.assertEqual(len(self.recent()), 50)
        for limit, count in ((-10, 1), (0, 1), (1, 1), (3, 3), (500, 500), (501, 500)):
            with self.subTest(limit=limit):
                self.assertEqual(len(self.recent(limit=limit)), count)

    def test_face_listing_and_profile_reads_include_provenance(self):
        target, face_id = self.person(), self.add_face()
        self.assign([face_id], target, source="suggestion")
        person = self.session.get(Person, target)
        person.profile_face_id = face_id
        self.session.commit()
        for url in (f"/api/person/{target}/faces", f"/api/person/{target}"):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200, response.text)
            row = response.json()["items"][0] if url.endswith("/faces") else response.json()["profile_face"]
            self.assertEqual(row["assignment_source"], "suggestion")
            self.assertIsNotNone(row["assigned_at"])
        self.post(f"/api/person/{target}/media/{self.face(face_id).media_id}/detach", {})
        response = self.client.get("/api/faces/orphans")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["items"][0]["assignment_source"], "detach")
        self.assertIsNotNone(response.json()["items"][0]["assigned_at"])

    def test_recent_route_is_registered_before_any_face_path_parameter(self):
        paths = [route.path for route in face_api.router.routes]
        recent_index = paths.index("/assignments/recent")
        self.assertTrue(all(recent_index < index for index, path in enumerate(paths) if "{" in path))


class FaceAssignmentMigrationTests(unittest.TestCase):
    def test_upgrade_preserves_legacy_unknown_assignments_and_downgrade_removes_columns(self):
        script = ScriptDirectory.from_config(Config("alembic.ini"))
        self.assertEqual(script.get_heads(), ["2c3d4e5f6071"])
        revision = script.get_revision("2c3d4e5f6071")
        self.assertEqual(revision.down_revision, "1b2c3d4e5f60")
        engine = create_engine("sqlite://")
        self.addCleanup(engine.dispose)
        with engine.begin() as connection:
            connection.exec_driver_sql("CREATE TABLE face (id INTEGER PRIMARY KEY, person_id INTEGER)")
            connection.exec_driver_sql("INSERT INTO face VALUES (1, 460), (2, NULL)")
            with Operations.context(MigrationContext.configure(connection)):
                revision.module.upgrade()
                columns = {column["name"]: column for column in inspect(connection).get_columns("face")}
                for name in ("assigned_at", "assignment_source"):
                    self.assertTrue(columns[name]["nullable"])
                self.assertEqual(connection.exec_driver_sql(
                    "SELECT id, person_id, assigned_at, assignment_source FROM face ORDER BY id"
                ).all(), [(1, 460, None, None), (2, None, None, None)])
                connection.exec_driver_sql(
                    "UPDATE face SET assigned_at = '2024-01-01 00:00:00', assignment_source = 'manual' WHERE id = 1"
                )
                revision.module.downgrade()
                self.assertEqual({column["name"] for column in inspect(connection).get_columns("face")}, {"id", "person_id"})
                self.assertEqual(connection.exec_driver_sql("SELECT id, person_id FROM face ORDER BY id").all(), [(1, 460), (2, None)])


if __name__ == "__main__":
    unittest.main()
