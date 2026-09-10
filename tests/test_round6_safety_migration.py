import contextlib
import importlib.util
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from PIL import Image
from sqlalchemy import text
from sqlmodel import Session, SQLModel, create_engine

from app.config import settings
from app.models import Face, Media


spec = importlib.util.spec_from_file_location('round6_face_migration', Path(__file__).resolve().parents[1] / 'scripts/migrate_face_embeddings.py')
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


class FaceMigrationSafetyTests(unittest.TestCase):
    def _run_migration(self, error=None, thumbnail=True, detected=True, retry=False):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        engine = create_engine('sqlite://')
        self.addCleanup(engine.dispose)
        SQLModel.metadata.create_all(engine)
        thumbnail_dir = root / '.omoide/thumbnails'
        thumbnail_dir.mkdir(parents=True)
        Image.new('RGB', (16, 16)).save(thumbnail_dir / 'face.jpg')
        with Session(engine) as session:
            session.exec(text('CREATE TABLE face_embeddings(face_id INTEGER PRIMARY KEY, person_id INTEGER, embedding BLOB)'))
            session.exec(text('CREATE TABLE person_embeddings(person_id INTEGER PRIMARY KEY, embedding BLOB)'))
            media = Media(path='unused.jpg', filename='unused.jpg', size=1)
            session.add(media)
            session.commit()
            face = Face(media_id=media.id, bbox=[0, 0, 10, 10], thumbnail_path='face.jpg' if thumbnail else None)
            session.add(face)
            session.commit()
            face_id = face.id
            session.exec(text("INSERT INTO face_embeddings VALUES (:face_id, -1, :old)").bindparams(face_id=face_id, old=b'old vector'))
            session.commit()
        calls = []

        def inference(rgb):
            calls.append(rgb.shape)
            if error:
                raise error
            if detected == 'padded-only':
                # The 16x16 fixture crop yields nothing; only the gray-padded retry (larger) detects.
                found = rgb.shape[0] > 16
            else:
                found = bool(detected)
            return [SimpleNamespace(bbox=np.array([0, 0, 10, 10]), embedding=np.ones(512, dtype=np.float32))] if found else []
        self._inference_calls = calls
        client = SimpleNamespace(health=lambda: {'model': 'fake', 'runtime': {'actualCompute': 'cpu'}}, get=inference)
        output = io.StringIO()
        with patch.object(migration, 'engine', engine), patch.object(migration, '_backup_database', return_value=root / 'backup.db'), patch.object(migration, 'AdaFaceSocketAnalysis', return_value=client), patch.object(settings.general, 'data_dir', root), contextlib.redirect_stdout(output):
            migration.migrate(10, None)
            if retry:
                error = None
                migration.migrate(10, None)
        with Session(engine) as session:
            row = session.exec(text('SELECT embedding FROM face_embeddings WHERE face_id=:id').bindparams(id=face_id)).first()
            outcomes = session.exec(text('SELECT outcome FROM face_embedding_migration')).all()
            status = session.exec(text('SELECT status FROM model_fingerprints')).scalar_one()
        return row, [row[0] for row in outcomes], status, output.getvalue()

    def test_tight_crop_is_retried_with_gray_padding(self):
        # Live migration on 2026-09-10 lost ~50% of faces to `no_embedding` because
        # SCRFD finds nothing on tight thumbnail crops; the padded retry recovers them.
        row, outcomes, status, _ = self._run_migration(detected='padded-only')
        self.assertEqual(outcomes, ['migrated'])
        self.assertNotEqual(row[0], b'old vector')
        self.assertEqual(status, 'ready')
        self.assertEqual(len(self._inference_calls), 2)
        self.assertEqual(self._inference_calls[0], (16, 16, 3))
        self.assertGreater(self._inference_calls[1][0], 16)

    def test_drop_unresolved_removes_old_vector_and_marks_ready(self):
        # A face with no detectable content keeps its old vector by default (mixed space);
        # --drop-unresolved deletes it, records 'dropped', and lets the fingerprint become ready.
        row, outcomes, status, _ = self._run_migration(detected=False)
        self.assertEqual(row[0], b'old vector')
        self.assertNotEqual(status, 'ready')
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        engine = create_engine('sqlite://')
        self.addCleanup(engine.dispose)
        SQLModel.metadata.create_all(engine)
        (root / '.omoide/thumbnails').mkdir(parents=True)
        Image.new('RGB', (16, 16)).save(root / '.omoide/thumbnails/face.jpg')
        with Session(engine) as session:
            session.exec(text('CREATE TABLE face_embeddings(face_id INTEGER PRIMARY KEY, person_id INTEGER, embedding BLOB)'))
            session.exec(text('CREATE TABLE person_embeddings(person_id INTEGER PRIMARY KEY, embedding BLOB)'))
            media = Media(path='unused.jpg', filename='unused.jpg', size=1)
            session.add(media)
            session.commit()
            face = Face(media_id=media.id, bbox=[0, 0, 10, 10], thumbnail_path='face.jpg')
            session.add(face)
            session.commit()
            face_id = face.id
            session.exec(text("INSERT INTO face_embeddings VALUES (:face_id, -1, :old)").bindparams(face_id=face_id, old=b'old vector'))
            session.commit()
        client = SimpleNamespace(health=lambda: {'model': 'fake', 'runtime': {'actualCompute': 'cpu'}}, get=lambda rgb: [])
        with patch.object(migration, 'engine', engine), patch.object(migration, '_backup_database', return_value=root / 'backup.db'), patch.object(migration, 'AdaFaceSocketAnalysis', return_value=client), patch.object(settings.general, 'data_dir', root), contextlib.redirect_stdout(io.StringIO()):
            migration.migrate(10, None, drop_unresolved=True)
        with Session(engine) as session:
            self.assertIsNone(session.exec(text('SELECT embedding FROM face_embeddings WHERE face_id=:id').bindparams(id=face_id)).first())
            self.assertEqual(session.exec(text('SELECT outcome FROM face_embedding_migration')).all()[0][0], 'dropped')
            self.assertEqual(session.exec(text('SELECT status FROM model_fingerprints')).scalar_one(), 'ready')
            self.assertIsNotNone(session.get(Face, face_id))

    def test_padding_helper_adds_gray_border(self):
        padded = migration._pad_for_detection(np.zeros((10, 20, 3), dtype=np.uint8), 0.5)
        self.assertEqual(padded.shape, (20, 40, 3))
        self.assertEqual(int(padded[0, 0, 0]), 128)
        self.assertEqual(int(padded[10, 20, 0]), 0)

    def test_service_oserror_is_retryable_and_preserves_existing_vector(self):
        row, outcomes, status, _ = self._run_migration(error=OSError('socket unavailable'))
        self.assertIsNotNone(row)
        self.assertEqual(row[0], b'old vector')
        self.assertNotIn('unreadable_thumbnail', outcomes)
        self.assertNotEqual(status, 'ready')

    def test_service_runtimeerror_is_retryable_and_preserves_existing_vector(self):
        row, outcomes, status, _ = self._run_migration(error=RuntimeError('service busy'))
        self.assertIsNotNone(row)
        self.assertEqual(row[0], b'old vector')
        self.assertNotIn('unreadable_thumbnail', outcomes)
        self.assertNotEqual(status, 'ready')

    def test_no_embedding_does_not_delete_old_vector(self):
        row, _, status, _ = self._run_migration(detected=False)
        self.assertIsNotNone(row)
        self.assertEqual(row[0], b'old vector')
        self.assertNotEqual(status, 'ready')

    def test_missing_thumbnail_is_included_in_completion_check(self):
        row, _, status, output = self._run_migration(thumbnail=False)
        self.assertEqual(row[0], b'old vector')
        self.assertNotEqual(status, 'ready')
        self.assertIn('thumbnail', output.lower())

    def test_valid_inference_replaces_vector_and_completes(self):
        row, outcomes, status, _ = self._run_migration()
        self.assertNotEqual(row[0], b'old vector')
        self.assertEqual(outcomes, ['migrated'])
        self.assertEqual(status, 'ready')

    def test_service_failure_is_retried_successfully_on_the_next_run(self):
        row, outcomes, status, output = self._run_migration(error=OSError('temporarily offline'), retry=True)
        self.assertNotEqual(row[0], b'old vector')
        self.assertEqual(outcomes, ['migrated'])
        self.assertEqual(status, 'ready')
        self.assertIn('inference_error', output)
