"""Repair boxes stay aligned with the actual downsized bridge input."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from sqlmodel import Session, SQLModel, create_engine

from app.models import ImageRepairJob, ImageRepairStatus, Media
from app.services.comfy_annotation import ComfyAnnotationError
from app.tasks.image_repair import run_repair_job


class RepairCoordinatesAuditTests(unittest.TestCase):
    def test_subject_box_is_scaled_with_transport_without_mutating_saved_source_box(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'source.png'
            Image.new('RGB', (5000, 1000)).save(source)
            engine = create_engine('sqlite://')
            SQLModel.metadata.create_all(engine)
            with Session(engine) as session:
                media = Media(path=str(source), filename=source.name, size=source.stat().st_size, width=5000, height=1000)
                session.add(media)
                session.flush()
                box = {'x': 1000, 'y': 100, 'width': 500, 'height': 400}
                job = ImageRepairJob(media_id=media.id, profile='omoide-remove-people-v1', params={'subject_box': box})
                session.add(job)
                session.commit()
                job_id = job.id
            captured = {}
            def repair(**kwargs):
                captured.update(kwargs)
                raise ComfyAnnotationError('busy', 'test stops before writing output', retryable=True)
            with patch('app.database.engine', engine), patch('app.tasks.image_repair.repair_client') as factory:
                factory.return_value.repair.side_effect = repair
                run_repair_job(job_id)
            self.assertEqual(captured['image'].size, (4096, 819))
            self.assertEqual(captured['params']['subject_box'], {'x': 819, 'y': 82, 'width': 410, 'height': 328})
            with Session(engine) as session:
                job = session.get(ImageRepairJob, job_id)
                self.assertEqual(job.params['subject_box'], box)
                self.assertEqual(job.status, ImageRepairStatus.FAILED)
            engine.dispose()


if __name__ == '__main__':
    unittest.main()
