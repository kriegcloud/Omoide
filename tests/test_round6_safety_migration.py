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
        def inference(rgb):
            if error:
                raise error
            return [SimpleNamespace(bbox=np.array([0, 0, 10, 10]), embedding=np.ones(512, dtype=np.float32))] if detected else []
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
