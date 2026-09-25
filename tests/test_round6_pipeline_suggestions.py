import importlib
from datetime import datetime
from unittest.mock import patch

import numpy as np
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from fastapi import HTTPException
from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError
from sqlmodel import create_engine, text

from round6_support import DatabaseCase
from app.models import Face, Person
from app.config import settings
from app.processors.faces import FaceProcessor
from app.services import face_matching

face_api = importlib.import_module("app.api.face")


class SuggestionRejectionTests(DatabaseCase):
    def seed_vectors(self):
        person = self.person()
        reference = self.face(person=person)
        orphan = self.face(thumbnail_path="orphan.jpg")
        vector = np.zeros(512, dtype=np.float32)
        vector[0] = 1
        for face in (reference, orphan):
            self.session.exec(text("INSERT INTO face_embeddings(face_id, person_id, embedding) VALUES (:fid, :pid, :v)").bindparams(fid=face.id, pid=face.person_id or -1, v=vector.tobytes()))
        self.session.commit()
        return person, orphan, vector

    def seed_rejection(self, face_id, person_id):
        # Model-independent setup also exercises enforcement before endpoints exist.
        self.session.exec(text("CREATE TABLE IF NOT EXISTS face_suggestion_rejection (face_id INTEGER NOT NULL REFERENCES face(id) ON DELETE CASCADE, person_id INTEGER NOT NULL REFERENCES person(id) ON DELETE CASCADE, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(face_id, person_id))"))
        self.session.exec(text("INSERT INTO face_suggestion_rejection(face_id, person_id, created_at) VALUES (:fid, :pid, CURRENT_TIMESTAMP)").bindparams(fid=face_id, pid=person_id))
        self.session.commit()

    def suggestions(self):
        return face_api.get_orphan_face_suggestions(self.session, cursor=None, limit=48, min_score=0)

    def test_rejected_pair_is_excluded_from_suggestions(self):
        person, orphan, _ = self.seed_vectors()
        self.assertEqual([(item.face.id, item.person_id) for item in self.suggestions().items], [(orphan.id, person.id)])
        self.seed_rejection(orphan.id, person.id)
        self.assertEqual(self.suggestions().items, [])

    def test_rejected_winner_allows_another_person_suggestion(self):
        person, orphan, vector = self.seed_vectors()
        second = self.person()
        reference = self.face(person=second)
        vector[:2] = [0.8, 0.6]
        self.session.exec(text("INSERT INTO face_embeddings(face_id, person_id, embedding) VALUES (:fid, :pid, :v)").bindparams(fid=reference.id, pid=second.id, v=vector.tobytes()))
        self.session.commit()
        self.seed_rejection(orphan.id, person.id)
        self.assertEqual([(item.face.id, item.person_id) for item in self.suggestions().items], [(orphan.id, second.id)])

    def test_matcher_never_assigns_a_rejected_pair(self):
        person, orphan, _ = self.seed_vectors()
        self.seed_rejection(orphan.id, person.id)
        claimed = face_matching.match_faces_to_persons(self.session, [orphan.id], threshold=0.62, min_margin=0)
        self.assertEqual(claimed, {})
        self.session.refresh(orphan)
        self.assertIsNone(orphan.person_id)

    def test_claim_guard_honors_rejection_added_after_scoring(self):
        person, orphan, _ = self.seed_vectors()
        self.seed_rejection(orphan.id, person.id)
        claimed = face_matching._bulk_assign_faces_to_persons(self.session, {orphan.id: person.id})
        self.assertEqual(claimed, {})
        self.assertEqual(self.session.exec(text("SELECT person_id FROM face_embeddings WHERE face_id=:fid").bindparams(fid=orphan.id)).scalar_one(), -1)

    def test_rejection_endpoints_are_idempotent_and_undo_restores_suggestion(self):
        person, orphan, _ = self.seed_vectors()
        reject = getattr(face_api, "reject_face_suggestion", None)
        undo = getattr(face_api, "undo_face_suggestion_rejection", None)
        self.assertTrue(callable(reject), "Missing rejection endpoint")
        self.assertTrue(callable(undo), "Missing undo endpoint")
        body = face_api.FaceSuggestionRejectionRequest(person_id=person.id)
        reject(orphan.id, body, self.session)
        first = self.session.exec(text("SELECT created_at FROM face_suggestion_rejection")).one()
        reject(orphan.id, body, self.session)
        self.assertEqual(self.session.exec(text("SELECT created_at FROM face_suggestion_rejection")).all(), [first])
        self.assertEqual(self.suggestions().items, [])
        undo(orphan.id, body, self.session)
        undo(orphan.id, body, self.session)
        self.assertEqual(len(self.suggestions().items), 1)
        routes = {(route.path, method) for route in face_api.router.routes for method in route.methods}
        self.assertIn(("/{face_id}/reject-suggestion", "POST"), routes)
        self.assertIn(("/{face_id}/reject-suggestion", "DELETE"), routes)

    def test_rejection_endpoints_validate_face_and_person(self):
        reject = getattr(face_api, "reject_face_suggestion", None)
        self.assertTrue(callable(reject), "Missing rejection endpoint")
        person, orphan, _ = self.seed_vectors()
        for face_id, person_id in ((99999, person.id), (orphan.id, 99999)):
            with self.subTest(face_id=face_id, person_id=person_id):
                with self.assertRaises(HTTPException) as exc:
                    reject(face_id, face_api.FaceSuggestionRejectionRequest(person_id=person_id), self.session)
                self.assertEqual(exc.exception.status_code, 404)

    def test_rejection_model_cascades_face_and_person_deletions(self):
        models = importlib.import_module("app.models")
        rejection_model = getattr(models, "FaceSuggestionRejection", None)
        self.assertIsNotNone(rejection_model, "Missing persisted rejection model")
        for delete_model in (Face, Person):
            person, face = self.person(), self.face()
            rejection = rejection_model(face_id=face.id, person_id=person.id)
            self.session.add(rejection)
            self.session.commit()
            self.assertIsInstance(rejection.created_at, datetime)
            self.session.exec(delete(delete_model).where(delete_model.id == (face.id if delete_model is Face else person.id)))
            self.session.commit()
            self.assertEqual(self.session.exec(text("SELECT COUNT(*) FROM face_suggestion_rejection")).scalar_one(), 0)

    def test_rejection_migration_is_the_single_head(self):
        script = ScriptDirectory.from_config(Config("alembic.ini"))
        self.assertEqual(script.get_heads(), ["718293a4b5c6"])
        self.assertEqual(script.get_revision("4e5f60718293").down_revision, "3d4e5f607182")

    def test_rejection_cleanup_when_media_face_processing_is_reset(self):
        person, orphan, _ = self.seed_vectors()
        orphan_id = orphan.id
        self.seed_rejection(orphan_id, person.id)
        from app.models import Media
        FaceProcessor().reset_for_media(self.session.get(Media, orphan.media_id), self.session)
        self.session.commit()
        self.session.expire_all()
        self.assertIsNone(self.session.get(Face, orphan_id))
        self.assertEqual(self.session.exec(text("SELECT COUNT(*) FROM face_suggestion_rejection")).scalar_one(), 0)
        self.assertEqual(self.session.exec(text("PRAGMA foreign_key_check")).all(), [])

    def test_rejection_mutations_respect_presentation_mode(self):
        person, orphan, _ = self.seed_vectors()
        body = face_api.FaceSuggestionRejectionRequest(person_id=person.id)
        with patch.object(settings.general, "presentation_mode", True):
            for endpoint in (face_api.reject_face_suggestion, face_api.undo_face_suggestion_rejection):
                with self.subTest(endpoint=endpoint.__name__):
                    with self.assertRaises(HTTPException) as error:
                        endpoint(orphan.id, body, self.session)
                    self.assertEqual(error.exception.status_code, 403)
        self.assertEqual(self.session.exec(text("SELECT COUNT(*) FROM face_suggestion_rejection")).scalar_one(), 0)

    def test_migration_enforces_unique_pairs_and_cascading_foreign_keys(self):
        module = ScriptDirectory.from_config(Config("alembic.ini")).get_revision("4e5f60718293").module
        engine = create_engine("sqlite://")
        self.addCleanup(engine.dispose)
        with engine.begin() as connection:
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            connection.exec_driver_sql("CREATE TABLE face(id INTEGER PRIMARY KEY)")
            connection.exec_driver_sql("CREATE TABLE person(id INTEGER PRIMARY KEY)")
            with Operations.context(MigrationContext.configure(connection)):
                module.upgrade()
                connection.exec_driver_sql("INSERT INTO face VALUES (1)")
                connection.exec_driver_sql("INSERT INTO person VALUES (1)")
                connection.exec_driver_sql("INSERT INTO face_suggestion_rejection(face_id,person_id) VALUES (1,1)")
                with self.assertRaises(IntegrityError):
                    connection.exec_driver_sql("INSERT INTO face_suggestion_rejection(face_id,person_id) VALUES (1,1)")
                self.assertIsNotNone(connection.exec_driver_sql("SELECT created_at FROM face_suggestion_rejection").scalar_one())
                connection.exec_driver_sql("DELETE FROM person WHERE id=1")
                self.assertEqual(connection.exec_driver_sql("SELECT COUNT(*) FROM face_suggestion_rejection").scalar_one(), 0)
                module.downgrade()
                module.upgrade()
                self.assertEqual(connection.exec_driver_sql("PRAGMA foreign_key_check").all(), [])
