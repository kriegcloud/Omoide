import errno
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sqlmodel import Session, SQLModel, create_engine

from app.config import settings, MediaDirectory
from app.models import Media, ProcessingTask
from app.services.media_files import MediaFileCollisionError, _replace_with_fallback


class ExclusiveMoveTests(unittest.TestCase):
    def test_existing_destination_is_not_overwritten_at_final_move(self):
        with tempfile.TemporaryDirectory() as directory:
            source, target = Path(directory) / 'source', Path(directory) / 'target'
            source.write_bytes(b'source')
            target.write_bytes(b'unrelated')
            with self.assertRaises(MediaFileCollisionError):
                _replace_with_fallback(source, target)
            self.assertEqual(target.read_bytes(), b'unrelated')
            self.assertEqual(source.read_bytes(), b'source')

    def test_cross_device_copy_does_not_overwrite_or_remove_existing_target(self):
        with tempfile.TemporaryDirectory() as directory:
            source, target = Path(directory) / 'source', Path(directory) / 'target'
            source.write_bytes(b'source')
            target.write_bytes(b'unrelated')
            with patch('app.services.media_files.os.link', side_effect=OSError(errno.EXDEV, 'cross device')), patch('app.services.media_files.os.replace', side_effect=OSError(errno.EXDEV, 'cross device')):
                with self.assertRaises(MediaFileCollisionError):
                    _replace_with_fallback(source, target)
            self.assertEqual(target.read_bytes(), b'unrelated')
            self.assertEqual(source.read_bytes(), b'source')

    def test_cross_device_success_and_failed_verification_preserve_source(self):
        with tempfile.TemporaryDirectory() as directory:
            source, target = Path(directory) / 'source', Path(directory) / 'target'
            source.write_bytes(b'source')
            with patch('app.services.media_files.os.link', side_effect=OSError(errno.EXDEV, 'cross device')), patch('app.services.media_files.os.replace', side_effect=OSError(errno.EXDEV, 'cross device')), patch('app.services.media_files._verify_copy', side_effect=OSError('bad copy')):
                with self.assertRaisesRegex(OSError, 'bad copy'):
                    _replace_with_fallback(source, target)
            self.assertFalse(target.exists())
            self.assertEqual(source.read_bytes(), b'source')
            with patch('app.services.media_files.os.link', side_effect=OSError(errno.EXDEV, 'cross device')):
                _replace_with_fallback(source, target)
            self.assertEqual(target.read_bytes(), b'source')
            self.assertFalse(source.exists())

    def test_filesystems_without_hardlinks_use_exclusive_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            source, target = Path(directory) / 'source', Path(directory) / 'target'
            source.write_bytes(b'source')
            with patch('app.services.media_files.os.link', side_effect=OSError(errno.EPERM, 'hard links unsupported')):
                _replace_with_fallback(source, target)
            self.assertEqual(target.read_bytes(), b'source')
            self.assertFalse(source.exists())

    def test_failed_database_commit_compensates_move_rename_and_bulk(self):
        import importlib
        api = importlib.import_module('app.api.media')
        from app.schemas.media import MediaBulkMoveRequest, MediaMoveRequest, MediaRenameRequest
        for action in ('move', 'rename', 'bulk'):
            with self.subTest(action=action), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                destination = root / 'destination'
                destination.mkdir()
                source = root / 'source.jpg'
                source.write_bytes(b'original')
                engine = create_engine('sqlite://')
                SQLModel.metadata.create_all(engine)
                with Session(engine) as session:
                    media = Media(path=str(source), filename=source.name, size=8)
                    session.add(media)
                    session.commit()
                    media_id = media.id
                    with patch.object(api.settings.general, 'media_dirs', [MediaDirectory(path=root)]), patch.object(api.settings.general, 'presentation_mode', False), patch.object(api, 'safe_commit', side_effect=RuntimeError('commit failed')):
                        with self.assertRaisesRegex(RuntimeError, 'commit failed'):
                            if action == 'move':
                                api.move_media(media_id, MediaMoveRequest(destination_dir=str(destination)), session)
                            elif action == 'rename':
                                api.rename_media(media_id, MediaRenameRequest(filename='renamed.jpg'), session)
                            else:
                                api.bulk_move_media(MediaBulkMoveRequest(media_ids=[media_id], destination_dir=str(destination)), session)
                    self.assertTrue(source.exists(), 'failed commit must restore the original path')
                    self.assertEqual(source.read_bytes(), b'original')
                    session.expire_all()
                    self.assertEqual(session.get(Media, media_id).path, str(source))
                engine.dispose()


class WritableRootTests(unittest.TestCase):
    def test_most_specific_read_only_root_wins_in_either_order(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entries = [MediaDirectory(path=root), MediaDirectory(path=root / 'private', read_only=True)]
            for roots in (entries, list(reversed(entries))):
                general = settings.general.model_copy(update={'media_dirs': roots})
                with self.assertRaises(PermissionError):
                    general.ensure_media_path_writable(root / 'private' / 'secret.jpg')
                general.ensure_media_path_writable(root / 'public.jpg')


class ConversionSafetyTests(unittest.TestCase):
    def test_replace_failure_keeps_original_output_and_unrelated_temp(self):
        from app.api import processors
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'video.mp4'
            source.write_bytes(b'original')
            unrelated = root / 'video_temp.mp4'
            unrelated.write_bytes(b'unrelated')
            engine = create_engine('sqlite://')
            SQLModel.metadata.create_all(engine)
            with Session(engine) as session:
                media = Media(path=str(source), filename=source.name, size=8)
                task = ProcessingTask(task_type='convert')
                session.add_all([media, task])
                session.commit()
                media_id, task_id = media.id, task.id
            outputs = []
            def convert(command, **kwargs):
                output = Path(command[-1])
                outputs.append(output)
                output.write_bytes(b'converted')
                return SimpleNamespace(poll=lambda: 0, returncode=0, communicate=lambda: ('', ''))
            with patch.object(processors.db, 'engine', engine), patch.object(processors.settings.general, 'media_dirs', [MediaDirectory(path=root)]), patch.object(processors, 'ensure_ffmpeg_available', return_value=Path('/usr/bin/ffmpeg')), patch.object(processors.ffmpeg, 'probe', return_value={}), patch.object(processors, 'get_ffmpeg_accel_config', return_value=SimpleNamespace(video_encoder='libx264', hwaccel_args=[])), patch.object(processors, 'popen_silent', side_effect=convert), patch.object(Path, 'rename', side_effect=OSError('replace failed')), patch.object(Path, 'replace', side_effect=OSError('replace failed')):
                processors._run_conversion(task_id, str(source), media_id)
            self.assertTrue(source.exists(), 'the source must survive a failed atomic replacement')
            self.assertEqual(source.read_bytes(), b'original')
            self.assertEqual(unrelated.read_bytes(), b'unrelated')
            self.assertNotEqual(outputs[0], unrelated)
            self.assertEqual(outputs[0].read_bytes(), b'converted')
            with Session(engine) as session:
                self.assertEqual(session.get(ProcessingTask, task_id).status, 'failed')
            engine.dispose()
