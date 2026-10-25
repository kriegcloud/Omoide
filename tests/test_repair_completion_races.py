"""Accepted repair cancellation must win over late thumbnail finalization."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from sqlmodel import Session, SQLModel, create_engine, select
from app.config import settings
from app.models import ImageRepairJob, ImageRepairStatus, Media, ProcessingTask
from app.tasks.image_repair import run_repair_job
from test_image_repairs import FakeRepairClient


class RepairCompletionRaceTests(unittest.TestCase):
    def test_cancellation_during_thumbnail_cannot_publish_success_or_followup(self):
        self._case(False)

    def test_cancellation_with_thumbnail_exception_cleans_provisional_output(self):
        self._case(True)

    def _case(self, raise_after_cancel):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'source.jpg'
            Image.new('RGB',(40,30),'blue').save(source)
            original=source.read_bytes()
            engine=create_engine('sqlite://')
            self.addCleanup(engine.dispose)
            SQLModel.metadata.create_all(engine)
            with engine.begin() as connection:
                connection.exec_driver_sql("CREATE TABLE media_embeddings (media_id INTEGER)")
                connection.exec_driver_sql("CREATE TABLE face_embeddings (face_id INTEGER)")
                connection.exec_driver_sql("CREATE TABLE scene_embeddings (media_id INTEGER)")
            with Session(engine) as session:
                media=Media(path=str(source),filename=source.name,size=len(original),width=40,height=30)
                session.add(media);session.flush()
                job=ImageRepairJob(media_id=media.id,profile='omoide-remove-text-v1')
                session.add(job);session.commit();job_id=job.id
            def cancel(_media):
                with Session(engine) as session:
                    job=session.get(ImageRepairJob,job_id)
                    job.status=ImageRepairStatus.CANCELLED
                    session.add(job);session.commit()
                if raise_after_cancel:
                    raise OSError("thumbnail failed after cancellation")
                return None,None
            with patch('app.database.engine',engine),patch('app.tasks.image_repair.repair_client',return_value=FakeRepairClient()),patch.object(type(settings.general),'resolved_media_dirs',return_value=[(root,False)]),patch('app.tasks.image_repair.generate_perceptual_hash',return_value='hash'),patch('app.tasks.image_repair.generate_thumbnail',side_effect=cancel),patch('app.tasks.media_processing.run_processors_for_media') as process:
                run_repair_job(job_id)
            with Session(engine) as session:
                job=session.get(ImageRepairJob,job_id)
                self.assertEqual(job.status,ImageRepairStatus.CANCELLED)
                self.assertIsNone(job.result_media_id)
                self.assertEqual(len(session.exec(select(Media)).all()),1)
                self.assertEqual(session.exec(select(ProcessingTask)).all(),[])
            process.assert_not_called()
            self.assertEqual(source.read_bytes(),original)
            self.assertEqual(list(root.glob('*_repaired*')),[])
