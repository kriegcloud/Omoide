from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock, patch
import numpy as np
from round6_support import DatabaseCase
from app.models import ProcessingTask, Status
from app.processors.faces import FaceProcessor
from app.services.face_inference import AdaFaceSocketAnalysis
from app.tasks import backfill


class ServiceBackfillTests(DatabaseCase):
    def test_service_quality_backfill_without_cpu_model(self):
        task = ProcessingTask(task_type='backfill_face_quality')
        self.session.add(task)
        face = self.face(thumbnail_path='face.jpg')
        (self.root / 'face.jpg').touch()
        client = AdaFaceSocketAnalysis(self.root / 'unused.sock')
        client.get = Mock(return_value=[SimpleNamespace(bbox=np.array([0,0,20,20]), det_score=.93, kps=np.zeros((5,2)))])
        processor = SimpleNamespace(name='faces',model=client,demographics_model=None,load_model=Mock())
        with patch.object(backfill,'processors',[processor]), patch.object(backfill.db,'engine',self.engine), patch.object(backfill,'heavy_writer',return_value=nullcontext(True)), patch.object(backfill.cv2,'imread',return_value=np.zeros((20,20,3),dtype=np.uint8)):
            backfill.run_backfill_face_quality(task.id)
        self.session.refresh(face)
        self.assertAlmostEqual(face.det_score,.93)
        client.get.assert_called_once()

    def test_service_demographics_backfill_without_cpu_model(self):
        task = ProcessingTask(task_type='backfill_demographics')
        self.session.add(task)
        face = self.face()
        client = AdaFaceSocketAnalysis(self.root / 'unused.sock')
        client.get = Mock(return_value=[SimpleNamespace(sex='F',age=32,sex_score=.92)])
        processor = SimpleNamespace(name='faces',model=client,demographics_model=None,load_model=Mock(),_demographics=FaceProcessor._demographics)
        with patch.object(backfill,'processors',[processor]), patch.object(backfill.db,'engine',self.engine), patch.object(backfill,'_load_face_source',return_value=np.zeros((20,20,3),dtype=np.uint8)):
            backfill.run_backfill_demographics(task.id)
        self.session.refresh(face)
        self.session.refresh(task)
        self.assertEqual((face.sex,face.age,face.sex_score),('F',32,.92))
        self.assertEqual(task.status, Status.COMPLETED)
