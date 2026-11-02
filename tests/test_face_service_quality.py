import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from pathlib import Path
import importlib.util

import numpy as np
from sqlalchemy import Float
from app.services.face_inference import AdaFaceSocketAnalysis
from app.processors.faces import FaceProcessor
from app.models import Face


class ServiceQualityTests(unittest.TestCase):
    def payload(self):
        return dict(bbox=[0,0,10,10], keypoints=np.zeros((5,2)).tolist(),
                    detScore=.9, embedding=[1,0], embeddingNorm=12.5,
                    sex='F', age=29, sexScore=.95)

    def test_client_and_processor_keep_quality_and_sex_probability(self):
        client = AdaFaceSocketAnalysis(Path('/tmp/unused'))
        with patch.object(client, '_request', return_value={'faces':[self.payload()]}):
            face = client.get(np.zeros((10,10,3),dtype=np.uint8))[0]
        self.assertEqual(face.embedding_norm, 12.5)
        self.assertEqual(FaceProcessor._demographics(face), ('F',29,.95))
        processor = FaceProcessor()
        processor.demographics_model = Mock()
        processor._apply_cpu_demographics(np.zeros((10,10,3)), [face])
        processor.demographics_model.get.assert_not_called()

    def test_older_payload_uses_cpu_fallback_and_missing_norm_is_nullable(self):
        payload = self.payload()
        for key in ('sex','age','sexScore','embeddingNorm'):
            del payload[key]
        client = AdaFaceSocketAnalysis(Path('/tmp/unused'))
        with patch.object(client, '_request', return_value={'faces':[payload]}):
            face = client.get(np.zeros((10,10,3),dtype=np.uint8))[0]
        self.assertIsNone(face.embedding_norm)
        processor = FaceProcessor()
        processor.model = client
        cpu = Mock()
        cpu.get.return_value = [SimpleNamespace(bbox=face.bbox,gender=1,age=30)]
        with patch.object(processor, '_load_cpu_demographics', return_value=cpu) as load:
            processor._apply_cpu_demographics(np.zeros((10,10,3)),[face])
        load.assert_called_once()
        self.assertEqual(processor._demographics(face),('M',30,None))

    def test_nullable_quality_schema_and_migration(self):
        column = Face.__table__.c.embedding_norm
        self.assertTrue(column.nullable)
        self.assertIsInstance(column.type, Float)
        path = Path(__file__).resolve().parents[1] / 'alembic/versions/a7b8c9d0e1f2_face_embedding_norm.py'
        spec = importlib.util.spec_from_file_location('norm_migration', path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        self.assertEqual(migration.down_revision, 'f1a2b3c4d5e6')
        with patch.object(migration, 'op') as op:
            migration.upgrade()
            self.assertEqual(op.add_column.call_args.args[0], 'face')
            self.assertEqual(op.add_column.call_args.args[1].name, 'embedding_norm')
            migration.downgrade()
            op.drop_column.assert_called_once_with('face','embedding_norm')
