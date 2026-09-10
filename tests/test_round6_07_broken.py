"""Broken media retries advance past failures; scan retains thumbnail diagnostics."""
from __future__ import annotations

import importlib
import shutil
import subprocess
from contextlib import nullcontext
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import text
from sqlmodel import select

from round6_support import DatabaseCase
from app.config import MediaDirectory, settings
from app.models import Media, ProcessingTask, Scene
from app.schemas.broken import BrokenResolveRequest, BrokenRetryRequest

broken = importlib.import_module("app.api.broken")
scan = importlib.import_module("app.tasks.scan")
utils = importlib.import_module("app.utils")


class BrokenRetryTests(DatabaseCase):
    def test_retry_cursor_advances_past_first_25_persistent_failures(self):
        ids = [self.media(processing_error="bad video").id for _ in range(60)]
        attempted = []

        def thumbnail(media):
            attempted.append(media.id)
            return (None, "still bad") if media.id in ids[:25] else (f"{media.id}.jpg", None)

        with patch.object(broken, "generate_thumbnail", side_effect=thumbnail):
            first = broken.retry_broken(BrokenRetryRequest(select_all=True), self.session)
            second = broken.retry_broken(BrokenRetryRequest(select_all=True, after_id=ids[24]), self.session)
            third = broken.retry_broken(BrokenRetryRequest(select_all=True, after_id=ids[49]), self.session)
        self.assertEqual(attempted, ids)
        self.assertEqual((first.retried, first.cleared, first.still_broken, first.remaining), (25, 0, 25, 35))
        self.assertEqual((second.retried, second.cleared, second.remaining), (25, 25, 10))
        self.assertEqual((third.retried, third.cleared, third.remaining), (10, 10, 0))
        self.assertEqual((first.next_cursor, second.next_cursor, third.next_cursor), (ids[24], ids[49], None))

    def test_retry_cursor_preserves_selected_id_scope(self):
        ids = [self.media(processing_error="bad video").id for _ in range(30)]
        selected = ids[1:28]
        attempted = []
        with patch.object(broken, "generate_thumbnail", side_effect=lambda media: (attempted.append(media.id) or None, "still bad")):
            first = broken.retry_broken(BrokenRetryRequest(media_ids=selected), self.session)
            second = broken.retry_broken(BrokenRetryRequest(media_ids=selected, after_id=selected[24]), self.session)
        self.assertEqual(attempted, selected)
        self.assertEqual((first.remaining, second.remaining), (2, 0))

    def test_retry_followup_empty_after_concurrent_removal_is_finished(self):
        row = self.media(processing_error="bad video")
        response = broken.retry_broken(BrokenRetryRequest(select_all=True, after_id=row.id), self.session)
        self.assertEqual((response.retried, response.remaining, response.next_cursor), (0, 0, None))

    def test_resolve_preserves_successful_ids_and_per_item_errors(self):
        payload = {"removed": 1, "processed_ids": [2], "skipped_ids": [1], "errors": [{"id": 1, "reason": "read-only root"}]}
        with patch.object(broken, "resolve_media_action", return_value=payload):
            response = broken.resolve_broken(BrokenResolveRequest(media_ids=[1, 2], action="DELETE_FILES"), self.session)
        self.assertEqual(response.model_dump(), payload)

    def test_retry_rejects_presentation_mode_before_thumbnailing(self):
        self.media(processing_error="bad video")
        with patch.object(settings.general, "presentation_mode", True), patch.object(broken, "generate_thumbnail") as thumbnail:
            with self.assertRaises(HTTPException) as error:
                broken.retry_broken(BrokenRetryRequest(select_all=True), self.session)
        self.assertEqual(error.exception.status_code, 403)
        thumbnail.assert_not_called()

    def test_missing_media_is_excluded_from_broken_list_and_retry(self):
        visible = self.media(processing_error="bad video")
        self.media(processing_error="file missing", missing_since=datetime(2026, 1, 1))
        page = broken.list_broken_media(session=self.session, cursor=None, limit=50)
        self.assertEqual([item.id for item in page.items], [visible.id])
        self.assertEqual(page.total, 1)
        with patch.object(broken, "generate_thumbnail", return_value=(None, "still bad")) as thumbnail:
            response = broken.retry_broken(BrokenRetryRequest(select_all=True), self.session)
        self.assertEqual(response.retried, 1)
        self.assertEqual([call.args[0].id for call in thumbnail.call_args_list], [visible.id])

    def test_retry_success_reruns_thumbnailing_and_resets_processing(self):
        row = self.media(processing_error="ffmpeg failure", thumbnail_path=None, extracted_scenes=True,
                         faces_extracted=True, ran_auto_tagging=True, embeddings_created=True,
                         laplacian_score=4.0)
        self.session.add(Scene(media_id=row.id, start_time=0, end_time=1))
        self.session.commit()
        with patch.object(broken, "generate_thumbnail", return_value=("fixed.jpg", None)) as thumbnail:
            response = broken.retry_broken(BrokenRetryRequest(media_ids=[row.id]), self.session)
        self.assertEqual(response.cleared, 1)
        thumbnail.assert_called_once()
        self.session.refresh(row)
        self.assertEqual(row.thumbnail_path, "fixed.jpg")
        self.assertIsNone(row.processing_error)
        self.assertFalse(row.extracted_scenes)
        self.assertFalse(row.faces_extracted)
        self.assertFalse(row.ran_auto_tagging)
        self.assertFalse(row.embeddings_created)
        self.assertIsNone(row.laplacian_score)
        self.assertEqual(self.session.exec(select(Scene)).all(), [])
        self.assertEqual(self.session.exec(text("PRAGMA foreign_key_check")).all(), [])


class BrokenScanTests(DatabaseCase):
    def scan_rejected_video(self):
        if shutil.which("ffmpeg") is None:
            self.skipTest("ffmpeg is required for the rejected-video fixture")
        path = self.root / "rejected.mp4"
        path.write_bytes(b"invalid mp4 fixture")
        task = ProcessingTask(task_type="scan")
        self.session.add(task)
        self.session.commit()
        # Isolate accepted metadata from the genuine ffmpeg thumbnail failure.
        metadata = Media(path=str(path), filename=path.name, size=path.stat().st_size)
        with patch.object(scan.db, "engine", self.engine), patch.object(settings.general, "media_dirs", [MediaDirectory(path=self.root)]), patch.object(scan, "process_file", return_value=(metadata, None, None)), patch.object(scan, "heavy_writer", return_value=nullcontext(True)), patch.object(utils, "get_ffmpeg_accel_config", return_value=SimpleNamespace(hwaccel_args=[])):
            with self.assertLogs("app", level="WARNING") as logs:
                scan.run_scan(task.id)
        return logs.records

    def test_scan_thumbnail_failure_is_already_listed_in_broken_media(self):
        self.scan_rejected_video()
        self.session.expire_all()
        page = broken.list_broken_media(session=self.session, cursor=None, limit=50)
        self.assertEqual(page.total, 1)
        self.assertEqual(page.items[0].filename, "rejected.mp4")
        self.assertIn("ffmpeg", page.items[0].processing_error)
        self.assertIsNone(page.items[0].thumbnail_path)

    def test_scan_thumbnail_failure_logs_once_at_warning(self):
        records = self.scan_rejected_video()
        self.assertEqual(len(records), 1, [record.getMessage() for record in records])
        self.assertEqual(records[0].levelname, "WARNING")

    def test_scan_thumbnail_timeout_logs_once_at_warning(self):
        with patch.object(utils, "run_silent", side_effect=subprocess.TimeoutExpired(["ffmpeg"], 20)):
            records = self.scan_rejected_video()
        self.assertEqual(len(records), 1, [record.getMessage() for record in records])
        self.assertEqual(records[0].levelname, "WARNING")

    def test_ffmpeg_failure_preserves_decoder_reason(self):
        row = self.media(path=str(self.root / "rejected.mp4"))
        failure = subprocess.CalledProcessError(1, ["ffmpeg"], stderr="moov atom not found")
        with patch.object(utils, "run_silent", side_effect=failure), patch.object(utils, "get_ffmpeg_accel_config", return_value=SimpleNamespace(hwaccel_args=[])):
            thumbnail, reason = utils.generate_thumbnail(row)
        self.assertIsNone(thumbnail)
        self.assertIn("moov atom not found", reason)
