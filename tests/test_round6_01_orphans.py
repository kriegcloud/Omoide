from unittest.mock import patch

from fastapi import HTTPException
from sqlmodel import select, text

from round6_support import DatabaseCase
from app.api import face as _router
from app.api.face import delete_all_orphans, get_orphan_count
from app.config import settings
from app.models import Face


class DeleteAllOrphansTests(DatabaseCase):
    def test_face_assigned_after_batch_selection_is_preserved(self):
        import importlib
        face_api = importlib.import_module("app.api.face")
        from app.models import FaceAssignmentSource
        from app.services.face_provenance import stamp_face_assignment
        orphan, person = self.face(), self.person()
        orphan_id, person_id = orphan.id, person.id
        original_batch = face_api._delete_face_batch
        def concurrent_assignment(session, ids, **kwargs):
            stamp_face_assignment(orphan, person_id, FaceAssignmentSource.MANUAL)
            session.add(orphan)
            session.commit()
            return original_batch(session, ids, **kwargs)
        with patch.object(face_api, "_delete_face_batch", side_effect=concurrent_assignment):
            self.assertEqual(delete_all_orphans(self.session), {"deleted": 0})
        self.assertEqual(self.session.get(Face, orphan_id).person_id, person_id)

    def test_deletes_multiple_batches_and_thumbnails_preserves_assigned_faces(self):
        media, person = self.media(), self.person()
        assigned = self.face(media=media, person=person)
        self.session.add_all([Face(media_id=media.id, bbox=[0, 0, 1, 1]) for _ in range(501)])
        self.session.commit()
        orphan = self.session.exec(select(Face).where(Face.person_id.is_(None))).first()
        thumb = self.root / "orphan.jpg"
        thumb.touch()
        orphan.thumbnail_path = thumb.name
        self.session.add(orphan)
        for face in (orphan, assigned):
            self.session.exec(text(
                "INSERT INTO face_embeddings(face_id, person_id, embedding) VALUES (:f, :p, zeroblob(2048))"
            ).bindparams(f=face.id, p=face.person_id or -1))
        self.session.commit()
        import app.api.face as face_module
        import importlib
        face_module = importlib.import_module("app.api.face")
        with patch.object(face_module, "_delete_face_batch", wraps=face_module._delete_face_batch) as batch:
            self.assertEqual(delete_all_orphans(self.session), {"deleted": 501})
            self.assertEqual([len(call.args[1]) for call in batch.call_args_list], [500, 1])
        self.assertFalse(thumb.exists())
        self.assertEqual(get_orphan_count(self.session), {"count": 0})
        self.assertIsNotNone(self.session.get(Face, assigned.id))
        self.assertEqual(self.session.exec(text("SELECT face_id FROM face_embeddings")).all(), [(assigned.id,)])

    def test_refuses_above_twenty_thousand_without_deleting(self):
        media = self.media()
        self.session.exec(text("WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM n WHERE x<20001) INSERT INTO face(media_id,bbox) SELECT :m,'[0,0,1,1]' FROM n").bindparams(m=media.id))
        self.session.commit()
        with self.assertRaises(HTTPException) as error:
            delete_all_orphans(self.session)
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(get_orphan_count(self.session)["count"], 20001)

    def test_presentation_mode_refuses_deletion(self):
        self.face()
        with patch.object(settings.general, "presentation_mode", True):
            with self.assertRaises(HTTPException) as error:
                delete_all_orphans(self.session)
        self.assertEqual(error.exception.status_code, 403)
