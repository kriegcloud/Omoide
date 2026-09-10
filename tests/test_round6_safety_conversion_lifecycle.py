"""Video conversion must respect task cancellation before replacing originals."""

import importlib
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

_CONFIG_HOME = tempfile.TemporaryDirectory()
os.environ.setdefault("XDG_CONFIG_HOME", _CONFIG_HOME.name)

from sqlmodel import Session, SQLModel, create_engine
from sqlalchemy.sql.dml import Update

from app.config import MediaDirectory
from app.models import Media, ProcessingTask

processors = importlib.import_module("app.api.processors")


class ConversionLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.source = self.root / "video.mp4"
        self.source.write_bytes(b"original")
        self.engine = create_engine("sqlite://")
        self.addCleanup(self.engine.dispose)
        SQLModel.metadata.create_all(self.engine)
        with Session(self.engine) as session:
            media = Media(path=str(self.source), filename=self.source.name, size=8)
            task = ProcessingTask(task_type="convert", status="pending")
            session.add_all([media, task])
            session.commit()
            self.media_id, self.task_id = media.id, task.id
        self.enterContext(patch.object(processors.db, "engine", self.engine))
        self.enterContext(patch.object(processors.settings.general, "media_dirs", [MediaDirectory(path=self.root)]))
        self.enterContext(patch.object(processors, "ensure_ffmpeg_available", return_value=Path("/usr/bin/ffmpeg")))
        self.enterContext(patch.object(processors.ffmpeg, "probe", return_value={"format": {"duration": "10"}}))
        self.enterContext(patch.object(processors, "get_ffmpeg_accel_config", return_value=SimpleNamespace(video_encoder="libx264", hwaccel_args=[])))
        self.enterContext(patch.object(processors.time, "sleep"))
        self.output = None
        self.proc = Mock()
        self.proc.returncode = 0
        self.proc.poll.return_value = 0
        self.proc.communicate.return_value = ("", "")
        self.launch = self.enterContext(patch.object(processors, "popen_silent", side_effect=self.convert))

    def convert(self, args, **kwargs):
        self.output = Path(args[-1])
        self.output.write_bytes(b"converted")
        return self.proc

    def change_status(self, status):
        with Session(self.engine) as session:
            task = session.get(ProcessingTask, self.task_id)
            task.status = status
            session.add(task)
            session.commit()

    def task(self):
        with Session(self.engine) as session:
            return session.get(ProcessingTask, self.task_id)

    def run_conversion(self):
        processors._run_conversion(self.task_id, str(self.source), self.media_id)

    def assert_preserved(self, status):
        self.assertEqual(self.source.read_bytes(), b"original")
        self.assertEqual(self.task().status, status)
        if self.output is not None:
            self.assertFalse(self.output.exists())
        self.assertEqual(list(self.root.glob("*.progress")), [])

    def test_cancelled_task_is_not_restarted_or_converted(self):
        self.change_status("cancelled")
        self.run_conversion()
        self.launch.assert_not_called()
        self.assert_preserved("cancelled")

    def test_interrupted_task_is_not_restarted_or_converted(self):
        self.change_status("interrupted")
        self.run_conversion()
        self.launch.assert_not_called()
        self.assert_preserved("interrupted")

    def test_cancellation_during_ffmpeg_stops_owned_process_and_preserves_original(self):
        polls = 0

        def poll():
            nonlocal polls
            polls += 1
            if polls == 1:
                self.change_status("cancelled")
            # Bound the original broken loop so a failing test cannot hang.
            return 0 if polls >= 3 else None

        self.proc.poll.side_effect = poll
        self.run_conversion()
        self.proc.terminate.assert_called_once()
        self.assert_preserved("cancelled")

    def test_interruption_after_ffmpeg_exit_does_not_replace_original(self):
        def communicate():
            self.change_status("interrupted")
            return "", ""

        self.proc.communicate.side_effect = communicate
        self.run_conversion()
        self.assert_preserved("interrupted")

    def test_ffmpeg_error_after_interruption_does_not_mark_task_failed(self):
        def communicate():
            self.change_status("interrupted")
            raise RuntimeError("conversion pipe closed")

        self.proc.communicate.side_effect = communicate
        self.run_conversion()
        self.assert_preserved("interrupted")

    def test_interruption_between_last_status_read_and_final_claim_preserves_original(self):
        original_exec = Session.exec
        interrupted = False

        def execute(session, statement, *args, **kwargs):
            nonlocal interrupted
            if isinstance(statement, Update) and statement.compile().params.get("processed") == 100:
                interrupted = True
                self.change_status("interrupted")
            return original_exec(session, statement, *args, **kwargs)

        with patch.object(Session, "exec", new=execute):
            self.run_conversion()
        self.assertTrue(interrupted)
        self.assert_preserved("interrupted")

    def test_cancelled_ffmpeg_is_killed_when_termination_times_out(self):
        polls = 0

        def poll():
            nonlocal polls
            polls += 1
            if polls == 1:
                self.change_status("cancelled")
            return 0 if polls >= 3 else None

        self.proc.poll.side_effect = poll
        self.proc.communicate.side_effect = [subprocess.TimeoutExpired("ffmpeg", 2), ("", "")]
        self.run_conversion()
        self.proc.terminate.assert_called_once()
        self.proc.kill.assert_called_once()
        self.assertTrue(all(call.kwargs.get("timeout", 0) <= 5 for call in self.proc.communicate.call_args_list))
        self.assert_preserved("cancelled")

    def test_successful_conversion_records_completed_lifecycle(self):
        self.run_conversion()
        task = self.task()
        self.assertEqual(self.source.read_bytes(), b"converted")
        self.assertEqual(task.status, "completed")
        self.assertEqual(task.processed, 100)
        self.assertIsNotNone(task.started_at)
        self.assertIsNotNone(task.finished_at)
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
