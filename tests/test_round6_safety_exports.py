import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from pydantic import ValidationError
from sqlmodel import Session, SQLModel, create_engine

import app.api  # Initialise the existing task/API import cycle from its supported entry point.
from app.config import settings
from app.models import DatasetExport, DatasetExportLayout, DatasetItem, Media, ProcessingTask, TrainingDataset
from app.schemas.dataset import DatasetCreate, DatasetUpdate
from app.services.datasets import build_export


class ExportPathSafetyTests(unittest.TestCase):
    def test_create_and_update_reject_path_tokens(self):
        for schema in (DatasetCreate, DatasetUpdate):
            for field in ('trigger_word', 'class_token'):
                for value in ('../escaped', r'..\escaped', '.', '..', '/absolute', 'a/../../b', 'bad\0name'):
                    with self.subTest(schema=schema.__name__, field=field, value=value):
                        with self.assertRaises(ValidationError):
                            schema(**{'name': 'subject', field: value})
        self.assertEqual(DatasetCreate(name='subject', trigger_word='顔 #1').trigger_word, '顔 #1')

    def test_legacy_database_path_escape_is_rejected_before_creating_files(self):
        for malicious_field in ('trigger_word', 'class_token', 'slug'):
            with self.subTest(field=malicious_field), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                export_root = root / 'exports'
                engine = create_engine('sqlite://')
                SQLModel.metadata.create_all(engine)
                with Session(engine) as session:
                    fields = dict(name='subject', slug='subject', trigger_word='subject', class_token='person')
                    fields[malicious_field] = '../escaped'
                    dataset = TrainingDataset(**fields)
                    session.add(dataset)
                    session.commit()
                    export = DatasetExport(dataset_id=dataset.id, layout=DatasetExportLayout.KOHYA)
                    task = ProcessingTask(task_type='export_dataset')
                    session.add_all([export, task])
                    session.commit()
                    with patch.object(settings.general, 'datasets_dir', export_root):
                        with self.assertRaises(ValueError):
                            build_export(session, export.id, task.id)
                    self.assertFalse(export_root.exists(), 'invalid stored paths must be rejected before creating output')
                engine.dispose()

    def test_symlinked_dataset_directory_cannot_escape_export_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            export_root, outside = root / 'exports', root / 'outside'
            export_root.mkdir()
            outside.mkdir()
            (export_root / 'subject').symlink_to(outside, target_is_directory=True)
            engine = create_engine('sqlite://')
            SQLModel.metadata.create_all(engine)
            with Session(engine) as session:
                dataset = TrainingDataset(name='subject', slug='subject', trigger_word='subject', class_token='person')
                session.add(dataset)
                session.commit()
                export = DatasetExport(dataset_id=dataset.id, layout=DatasetExportLayout.KOHYA)
                task = ProcessingTask(task_type='export_dataset')
                session.add_all([export, task])
                session.commit()
                with patch.object(settings.general, 'datasets_dir', export_root):
                    with self.assertRaises(ValueError):
                        build_export(session, export.id, task.id)
                self.assertEqual(list(outside.iterdir()), [])
            engine.dispose()

    def _regularization_export(self, cancel_after_first=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source.jpg'
            Image.new('RGB', (8, 8)).save(source)
            engine = create_engine('sqlite://')
            SQLModel.metadata.create_all(engine)
            with Session(engine) as session:
                regularization = TrainingDataset(name='reg', slug='reg', trigger_word='', class_token='person')
                media = Media(path=str(source), filename=source.name, size=source.stat().st_size)
                session.add_all([regularization, media])
                session.commit()
                dataset = TrainingDataset(name='subject', slug='subject', trigger_word='subject', class_token='person', regularization_dataset_id=regularization.id)
                session.add(dataset)
                session.add(DatasetItem(dataset_id=regularization.id, media_id=media.id))
                second_source = root / 'second.jpg'
                Image.new('RGB', (8, 8)).save(second_source)
                second_media = Media(path=str(second_source), filename=second_source.name, size=second_source.stat().st_size)
                session.add(second_media)
                session.commit()
                session.add(DatasetItem(dataset_id=regularization.id, media_id=second_media.id))
                session.commit()
                export = DatasetExport(dataset_id=dataset.id, layout=DatasetExportLayout.KOHYA)
                task = ProcessingTask(task_type='export_dataset', status='running' if cancel_after_first else 'cancelled')
                session.add_all([export, task])
                session.commit()
                def caption(*args):
                    if cancel_after_first:
                        task.status = 'cancelled'
                        session.add(task)
                        session.commit()
                    return None
                with patch.object(settings.general, 'datasets_dir', root / 'exports'), patch('app.services.datasets.resolve_caption', side_effect=caption):
                    manifest = build_export(session, export.id, task.id)
                processed = task.processed
            engine.dispose()
            return manifest, processed

    def test_regularization_loop_honours_already_cancelled_task(self):
        manifest, processed = self._regularization_export()
        self.assertEqual(manifest['regularization_items'], [])
        self.assertEqual(processed, 0)

    def test_regularization_loop_honours_cancellation_between_images(self):
        manifest, processed = self._regularization_export(cancel_after_first=True)
        self.assertEqual(len(manifest['regularization_items']), 1)
        self.assertEqual(processed, 1)
