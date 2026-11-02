import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import tempfile
import unittest

import numpy as np
from PIL import Image
from sqlalchemy import text
from sqlmodel import Session, SQLModel, create_engine
from app.models import Face, Media, Person
from app.config import settings
from app.utils import vector_from_stored


class ReembedTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('reembed', Path(__file__).resolve().parents[1] / 'scripts/reembed_faces.py')
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.engine = create_engine('sqlite://')
        self.addCleanup(self.engine.dispose)
        SQLModel.metadata.create_all(self.engine)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.thumbs = self.root / '.omoide/thumbnails'
        self.thumbs.mkdir(parents=True)
        Image.new('RGB',(32,32)).save(self.thumbs / 'face.jpg')
        with Session(self.engine) as session:
            session.exec(text('CREATE TABLE face_embeddings(face_id INTEGER PRIMARY KEY, person_id INTEGER, embedding BLOB)'))
            session.exec(text('CREATE TABLE person_embeddings(person_id INTEGER PRIMARY KEY, embedding BLOB)'))
            media = Media(path='unused',filename='unused',size=1)
            person = Person(name='Kept', appearance_count=1)
            session.add_all([media, person]); session.commit()
            face = Face(media_id=media.id,person_id=person.id,bbox=[0,0,20,20],thumbnail_path='face.jpg', assignment_source='manual')
            session.add(face); session.commit()
            session.exec(text("INSERT INTO face_embeddings VALUES (1,1,:old)").bindparams(old=np.ones(512,dtype=np.float32).tobytes()))
            session.commit()
        self.client = SimpleNamespace(health=lambda: {'mirrorTta':True,'model':'cvlface_adaface_vit_base_kprpe_webface12m'}, get=lambda rgb: [SimpleNamespace(embedding=np.arange(512,dtype=np.float32),embedding_norm=15.)])
        self.enterContext(patch.object(self.module, 'engine', self.engine))
        self.enterContext(patch.object(self.module, '_backup_database', return_value=self.root / 'backup.db'))
        self.enterContext(patch.object(settings.general, 'data_dir', self.root))

    def test_rewrites_vectors_and_norm_preserving_assignment_and_centroid(self):
        self.module.reembed(self.client)
        with Session(self.engine) as session:
            face = session.get(Face, 1)
            self.assertEqual((face.person_id,face.assignment_source,face.embedding_norm),(1,'manual',15.))
            vector = session.exec(text('SELECT embedding FROM face_embeddings')).scalar_one()
            centroid = session.exec(text('SELECT embedding FROM person_embeddings')).scalar_one()
            np.testing.assert_allclose(vector_from_stored(vector), vector_from_stored(centroid),atol=1e-7)
            self.assertAlmostEqual(float(np.linalg.norm(vector_from_stored(vector))),1.)

    def test_failed_face_aborts_whole_rewrite(self):
        self.client.get = lambda rgb: []
        with self.assertRaisesRegex(RuntimeError, 'unresolved'):
            self.module.reembed(self.client)
        with Session(self.engine) as session:
            self.assertIsNone(session.get(Face,1).embedding_norm)
            self.assertEqual(session.exec(text('SELECT embedding FROM face_embeddings')).scalar_one(), np.ones(512,dtype=np.float32).tobytes())

    def test_requires_mirror_health(self):
        self.client.health = lambda: {'mirrorTta':False}
        with self.assertRaisesRegex(RuntimeError, 'mirror'):
            self.module.reembed(self.client)

    def test_keep_unresolved_retains_old_vector_and_rewrites_the_rest(self):
        with Session(self.engine) as session:
            media = session.exec(text('SELECT id FROM media')).scalar_one()
            session.add(Face(media_id=media, person_id=1, bbox=[0,0,20,20], thumbnail_path='missing.jpg', assignment_source='manual'))
            session.commit()
            session.exec(text("INSERT INTO face_embeddings VALUES (2,1,:old)").bindparams(old=np.full(512, 2, dtype=np.float32).tobytes()))
            session.commit()
        summary = self.module.reembed(self.client, keep_unresolved=True)
        self.assertEqual((summary['rewritten'], summary['kept']), (1, 1))
        with Session(self.engine) as session:
            kept = session.exec(text('SELECT embedding FROM face_embeddings WHERE face_id=2')).scalar_one()
            self.assertEqual(kept, np.full(512, 2, dtype=np.float32).tobytes())
            self.assertIsNone(session.get(Face, 2).embedding_norm)
            self.assertEqual(session.get(Face, 1).embedding_norm, 15.)

    def test_dry_run_writes_nothing_and_saves_staged_vectors(self):
        out = self.root / 'staged.npz'
        summary = self.module.reembed(self.client, dry_run=out)
        self.assertEqual(summary['rewritten'], 0)
        data = np.load(out)
        self.assertEqual(list(data['face_ids']), [1])
        self.assertEqual(data['vectors'].shape, (1, 512))
        with Session(self.engine) as session:
            self.assertIsNone(session.get(Face, 1).embedding_norm)
            self.assertEqual(session.exec(text('SELECT embedding FROM face_embeddings')).scalar_one(), np.ones(512, dtype=np.float32).tobytes())

    def test_min_agreement_keeps_old_vector_when_new_disagrees(self):
        # Thumbnail re-detection can land on a different face region; such a
        # vector is not "the same face plus a mirrored view" and must not replace it.
        with Session(self.engine) as session:
            session.exec(text("UPDATE face_embeddings SET embedding=:v WHERE face_id=1").bindparams(
                v=(-np.arange(512, dtype=np.float32) / np.linalg.norm(np.arange(512, dtype=np.float32))).tobytes()))
            session.commit()
        summary = self.module.reembed(self.client, keep_unresolved=True, min_agreement=0.7)
        self.assertEqual((summary['rewritten'], summary['kept']), (0, 1))
        with Session(self.engine) as session:
            self.assertIsNone(session.get(Face, 1).embedding_norm)
