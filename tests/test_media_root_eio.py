"""An unreadable media root (EIO from a stale mount) must not crash or mislead tasks.

Reproduces the 2026-09-23 incident: the T7 drives were re-attached while the
container held the old superblock, every stat under /app/media/T7 raised
``OSError(EIO)``, and "Detect Missing Files", "Process Unindexed Media" and
"Scan for New Files" either crashed with a bare "Failed" or reported success.
"""
from __future__ import annotations

import errno
import importlib
import os
import stat
from contextlib import nullcontext
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PIL import Image

from round6_support import DatabaseCase
from app.config import MediaDirectory, settings
from app.models import Media, ProcessingTask
from app.services.task_summary import summarize_task
from app.tasks import common, state
from app.tasks import media_processing as processing

maintenance = importlib.import_module("app.tasks.maintenance")
scan = importlib.import_module("app.tasks.scan")


def _eio_for(marker: str):
    """Patch ``Path.exists`` so paths containing ``marker`` raise EIO like a dead mount."""
    original = Path.exists

    def fake(self, *args, **kwargs):
        if marker in str(self):
            raise OSError(errno.EIO, os.strerror(errno.EIO), str(self))
        return original(self, *args, **kwargs)

    return patch.object(Path, "exists", fake)


class UnreadableMediaTests(DatabaseCase):
    def setUp(self):
        super().setUp()
        (self.root / "mount-marker").write_text("mounted")
        self.enterContext(patch.object(settings.general, "media_dirs", [MediaDirectory(path=self.root)]))
        self.enterContext(patch.object(settings.scan, "auto_cleanup_without_review", True))
        self.enterContext(patch.object(settings.scan, "auto_cleanup_grace_hours", 0))
        self.enterContext(patch.object(maintenance.db, "engine", self.engine))
        self.enterContext(patch.object(processing.db, "engine", self.engine))
        self.enterContext(patch.object(scan.db, "engine", self.engine))
        self.enterContext(patch.object(common.db, "engine", self.engine))
        self.enterContext(patch.object(processing, "heavy_writer", return_value=nullcontext(True)))
        state._task_failures.clear()

    def task(self, task_type, **kwargs):
        row = ProcessingTask(task_type=task_type, params=kwargs.pop("params", {}), **kwargs)
        self.session.add(row)
        self.session.commit()
        return row

    def reload(self, task):
        self.session.expire_all()
        return self.session.get(ProcessingTask, task.id)

    # --- Detect Missing Files -------------------------------------------------

    def test_missing_detection_skips_unreadable_files_instead_of_flagging_or_crashing(self):
        dead = self.media(path=str(self.root / "T7" / "dead.jpg"))
        present = self.root / "present.jpg"
        present.touch()
        self.media(path=str(present))
        gone = self.media(path=str(self.root / "gone.jpg"))
        task = self.task("clean_missing_files")

        with _eio_for("/T7/"), patch.object(maintenance, "delete_record") as remove:
            maintenance.clean_missing_files(task.id)

        task = self.reload(task)
        self.assertEqual(task.status, "completed")
        self.assertIsNone(self.session.get(Media, dead.id).missing_since)
        self.assertEqual([call.args[0] for call in remove.call_args_list], [gone.id])
        self.assertEqual(task.result["unreadable"], 1)
        self.assertEqual(task.result["flagged"], 1)
        failures = state.get_task_failures(task.id)
        self.assertEqual([f.path for f in failures], [dead.path])
        self.assertIn("Input/output error", failures[0].reason)
        self.assertIn("1 unreadable", summarize_task(task))

    # --- Process Unindexed Media ---------------------------------------------

    def test_media_processing_skips_unreadable_files_and_keeps_going(self):
        dead = self.media(path=str(self.root / "T7" / "dead.jpg"), faces_extracted=False)
        alive = self.root / "alive.jpg"
        alive.touch()
        live = self.media(path=str(alive), faces_extracted=False)
        processed: list[int] = []

        def process(media, session, scenes=None):
            processed.append(media.id)
            media.faces_extracted = True
            return True

        proc = SimpleNamespace(
            name="faces", active=False, handles_empty_scenes=True,
            unload=Mock(), reset_for_media=Mock(), process=process,
            get_pending_condition=lambda: Media.faces_extracted.is_(False),
        )
        proc.load_model = lambda: setattr(proc, "active", True)
        task = self.task("process_media")
        with _eio_for("/T7/"), patch.object(processing, "processors", [proc]), \
                patch.object(processing, "_get_or_extract_scenes", return_value=[Image.new("RGB", (4, 4))]):
            processing.run_media_processing(task.id)

        task = self.reload(task)
        self.assertEqual(task.status, "completed")
        self.assertEqual(processed, [live.id])
        self.assertIsNone(self.session.get(Media, dead.id).missing_since)
        self.assertEqual([f.path for f in state.get_task_failures(task.id)], [dead.path])

    # --- Crash reasons reach the task row -------------------------------------

    def test_guarded_crash_records_reason_in_task_result(self):
        task = self.task("clean_missing_files", status="running")

        def boom(_task_id):
            raise OSError(errno.EIO, "Input/output error", "/app/media/T7/x.jpg")

        common._run_task_guarded(boom, task.id)
        task = self.reload(task)
        self.assertEqual(task.status, "failed")
        self.assertEqual(task.result["error"], "OSError: [Errno 5] Input/output error: '/app/media/T7/x.jpg'")
        self.assertEqual(summarize_task(task), task.result["error"])

    def test_media_processing_crash_records_reason_in_task_result(self):
        task = self.task("process_media", status="running")
        with patch.object(processing, "_run_media_processing", side_effect=RuntimeError("model exploded")):
            processing.run_media_processing(task.id)
        task = self.reload(task)
        self.assertEqual(task.status, "failed")
        self.assertEqual(task.result["error"], "RuntimeError: model exploded")

    # --- Scan for New Files ---------------------------------------------------

    def test_scan_reports_unreadable_directories_instead_of_silent_success(self):
        if os.geteuid() == 0:
            self.skipTest("root ignores directory permissions")
        locked = self.root / "T7"
        locked.mkdir()
        locked.chmod(0)
        self.addCleanup(locked.chmod, stat.S_IRWXU)
        task = self.task("scan")

        scan.run_scan(task.id)

        task = self.reload(task)
        self.assertEqual(task.status, "completed")
        self.assertEqual(task.result["new_files"], 0)
        self.assertEqual(task.result["unreadable_dirs"], [str(locked)])
        failures = state.get_task_failures(task.id)
        self.assertEqual([f.path for f in failures], [str(locked)])
        self.assertEqual(summarize_task(task), "0 new files, 1 unreadable directory")

    def test_scan_summary_pluralises_unreadable_directories(self):
        task = ProcessingTask(
            task_type="scan", status="completed", total=0, processed=0,
            result={"new_files": 3, "skipped": 2, "unreadable_dirs": ["/a", "/b"]},
        )
        self.assertEqual(summarize_task(task), "3 new files, 2 skipped, 2 unreadable directories")

    def test_missing_summary_reports_unreadable_files(self):
        task = ProcessingTask(
            task_type="clean_missing_files", status="completed", total=5, processed=5,
            result={"flagged": 0, "recovered": 0, "removed": 0, "awaiting_review": 0,
                    "skipped_unmounted_roots": [], "unreadable": 5},
        )
        self.assertEqual(summarize_task(task), "No missing records · 5 unreadable (I/O error)")


class TrashDirectoryTests(DatabaseCase):
    def test_scan_never_indexes_trash_or_system_folders(self):
        kept = self.root / "album" / "kept.jpg"
        kept.parent.mkdir()
        kept.touch()
        for folder in (".Trash-1000/files/album", ".Trash/1000/files", "$RECYCLE.BIN/S-1-5", "System Volume Information", ".omoide"):
            (self.root / folder).mkdir(parents=True)
            (self.root / folder / "trashed.jpg").touch()

        found = set(scan._walk_media_candidates([self.root], frozenset({".jpg"}), skip_thumbnails=False))

        self.assertEqual(found, {kept})

