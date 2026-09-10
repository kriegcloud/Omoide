import asyncio
from unittest.mock import patch

import numpy as np
from sqlmodel import select, text

from round6_support import DatabaseCase
from app.api.face import assign_faces, delete_faces
from app.models import Face, Person
from app.schemas.face import FaceAssign
from app.utils import delete_record, update_person_embedding, vector_to_blob


class PersonConsistencyTests(DatabaseCase):
    def add_vector(self, face, vector):
        self.session.exec(text(
            "INSERT INTO face_embeddings(face_id, person_id, embedding) VALUES (:f,:p,:e)"
        ).bindparams(f=face.id, p=face.person_id or -1, e=vector_to_blob(vector)))
        self.session.commit()

    def test_empty_centroid_removes_stale_embedding(self):
        person = self.person()
        self.session.exec(text(
            "INSERT INTO person_embeddings(person_id,embedding) VALUES (:p,:e)"
        ).bindparams(p=person.id, e=vector_to_blob(np.ones(512))))
        self.session.commit()
        update_person_embedding(self.session, person.id)
        self.assertEqual(self.session.exec(text("SELECT person_id FROM person_embeddings")).all(), [])

    def test_reassignment_refreshes_source_and_target_profile_and_centroid(self):
        source, target = self.person(), self.person()
        first = self.face(person=source)
        remaining = self.face(person=source)
        a, b = np.eye(512, dtype=np.float32)[:2]
        self.add_vector(first, a)
        self.add_vector(remaining, b)
        source.profile_face_id = first.id
        self.session.add(source)
        update_person_embedding(self.session, source.id)
        asyncio.run(assign_faces(FaceAssign(face_ids=[first.id], person_id=target.id), self.session))
        self.session.refresh(source)
        self.session.refresh(target)
        self.assertEqual(source.profile_face_id, remaining.id)
        self.assertEqual(target.profile_face_id, first.id)
        actual = self.session.exec(text("SELECT embedding FROM person_embeddings WHERE person_id=:p").bindparams(p=source.id)).one()[0]
        np.testing.assert_allclose(np.frombuffer(actual, dtype=np.float32), b)

    def test_delete_profile_face_clears_fk_before_flush(self):
        person = self.person()
        face = self.face(person=person)
        remaining = self.face(person=person)
        person.profile_face_id = face.id
        self.session.add(person)
        self.session.commit()
        delete_faces([face.id], self.session)
        self.session.refresh(person)
        self.assertEqual(person.profile_face_id, remaining.id)
        self.assertEqual(person.appearance_count, 1)

    def test_failed_face_commit_keeps_thumbnail(self):
        thumb = self.root / "face.jpg"
        thumb.touch()
        face = self.face(thumbnail_path=thumb.name)
        with patch("app.api.face.safe_commit", side_effect=RuntimeError("commit failed")):
            with self.assertRaisesRegex(RuntimeError, "commit failed"):
                delete_faces([face.id], self.session)
        self.session.rollback()
        self.assertTrue(thumb.exists())
        self.assertIsNotNone(self.session.get(Face, face.id))

    def test_failed_last_face_commit_keeps_person_and_face(self):
        person = self.person()
        thumb = self.root / "last-face.jpg"
        thumb.touch()
        face = self.face(person=person, thumbnail_path=thumb.name)
        person_id, face_id = person.id, face.id
        with patch("app.api.face.safe_commit", side_effect=RuntimeError("commit failed")):
            with self.assertRaisesRegex(RuntimeError, "commit failed"):
                delete_faces([face_id], self.session)
        self.session.rollback()
        self.assertIsNotNone(self.session.get(Face, face_id))
        self.assertIsNotNone(self.session.get(Person, person_id))
        self.assertTrue(thumb.exists())

    def test_delete_record_refreshes_person_appearance_count(self):
        person = self.person()
        first = self.face(person=person)
        self.face(person=person)
        person.appearance_count = 2
        self.session.add(person)
        self.session.commit()
        delete_record(first.media_id, self.session)
        self.session.refresh(person)
        self.assertEqual(person.appearance_count, 1)
