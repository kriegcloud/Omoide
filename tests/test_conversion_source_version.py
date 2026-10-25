"""Replacement encodes must remain bound to the source version they read."""

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

_CONFIG_HOME = tempfile.TemporaryDirectory()
os.environ.setdefault("XDG_CONFIG_HOME", _CONFIG_HOME.name)

from sqlmodel import Session, SQLModel, create_engine

from app import conversion
from app.config import MediaDirectory, settings
from app.models import Media, ProcessingTask
from app.tasks import conversion as worker


class ConversionSourceVersionTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.engine = create_engine("sqlite://")
        self.addCleanup(self.engine.dispose)
        SQLModel.metadata.create_all(self.engine)
        self.enterContext(patch.object(worker.db, "engine", self.engine))
        self.enterContext(patch.object(settings.general, "media_dirs", [MediaDirectory(path=self.root)]))
        self.enterContext(patch.object(conversion, "converted_copy_path", return_value=self.root / "playback-copy.mp4"))

    def run_case(self, route, suffix, change):
        directory = self.root / f"{route}-{suffix[1:]}-{change}"
        directory.mkdir()
        source = directory / f"video{suffix}"
        original = b"source before encoding"
        encoded = b"encoded source before encoding"
        newer = b"newer source edited during encoding"
        source.write_bytes(original)
        with Session(self.engine) as session:
            media = Media(path=str(source), filename=source.name, size=len(original))
            task = ProcessingTask(task_type="convert", status="pending")
            session.add_all([media, task])
            session.commit()
            media_id, task_id = media.id, task.id

            def encode(target):
                self.assertEqual(source.read_bytes(), original)
                target.write_bytes(encoded)
                if change == "inplace":
                    source.write_bytes(newer)
                elif change == "atomic":
                    replacement = directory / "external-edit"
                    replacement.write_bytes(newer)
                    replacement.replace(source)

            error = None
            if route == "shared":
                with patch.object(conversion, "convert_file", side_effect=lambda _source, target, **_: encode(target)):
                    try:
                        conversion.convert_media(session, media, "replace_original")
                    except conversion.ConversionError as exc:
                        error = exc
            else:
                process = Mock()
                process.poll.return_value = 0
                process.returncode = 0
                process.communicate.return_value = ("", "")

                def launch(command, **_):
                    encode(Path(command[-1]))
                    return process

                with patch.object(worker, "popen_silent", side_effect=launch), \
                     patch.object(worker, "ensure_ffmpeg_available", return_value=Path("/usr/bin/ffmpeg")), \
                     patch.object(worker.ffmpeg, "probe", return_value={}), \
                     patch.object(worker, "get_ffmpeg_accel_config", return_value=SimpleNamespace(video_encoder="libx264", hwaccel_args=[])):
                    worker._run_conversion(task_id, str(source), media_id)

        with Session(self.engine) as session:
            media = session.get(Media, media_id)
            task = session.get(ProcessingTask, task_id)
            if change == "unchanged":
                self.assertEqual(Path(media.path).read_bytes(), encoded)
                if route == "single":
                    self.assertEqual(task.status, "completed")
                self.assertEqual(len(list(directory.iterdir())), 1)
                return

            self.assertTrue(source.exists(), "conversion must preserve the newer source path")
            self.assertEqual(source.read_bytes(), newer, "conversion must preserve the newer source")
            self.assertEqual((media.path, media.filename, media.size), (str(source), source.name, len(original)))
            recovery_files = [path for path in directory.iterdir() if path != source and path.read_bytes() == encoded]
            self.assertTrue(recovery_files, "retain the encoded version for recovery")
            if route == "shared":
                self.assertIsNotNone(error, "source conflict must not report success")
                self.assertIn(str(recovery_files[0]), str(error))
            else:
                self.assertEqual(task.status, "failed")
                self.assertIn("Source identity changed", task.result["error"])
                self.assertEqual(Path(task.result["recovery_path"]).read_bytes(), encoded)

    def test_changes_during_encoding_preserve_newer_source_and_encoded_recovery(self):
        for route in ("shared", "single"):
            for suffix in (".avi", ".mp4"):
                for change in ("inplace", "atomic"):
                    with self.subTest(route=route, suffix=suffix, change=change):
                        self.run_case(route, suffix, change)

    def test_unchanged_source_still_converts_and_cleans_recovery(self):
        for route in ("shared", "single"):
            for suffix in (".avi", ".mp4"):
                with self.subTest(route=route, suffix=suffix):
                    self.run_case(route, suffix, "unchanged")


if __name__ == "__main__":
    unittest.main()
