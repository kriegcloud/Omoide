"""Regression proofs for processor success, bounded paging and durable resumes."""
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image
from sqlmodel import select
from sqlalchemy import event, text

from round6_support import DatabaseCase
from app.config import settings
from app.models import Face, Media, ProcessingTask, Scene
from app.processors.faces import FaceProcessor
from app.processors.embedding_extractor import EmbeddingExtractor
from app.tasks import media_processing as processing, pipeline, resume


class ProcessingTests(DatabaseCase):
    def setUp(self):
        super().setUp()
        self.enterContext(patch.object(processing.db, "engine", self.engine))
        self.enterContext(patch.object(processing, "heavy_writer", return_value=nullcontext(True)))
        self.enterContext(patch.object(processing, "set_task_progress"))
        self.enterContext(patch.object(processing, "clear_task_progress"))

    def task(self, task_type="run_processor", **kwargs):
        row = ProcessingTask(task_type=task_type, params=kwargs.pop("params", {}), **kwargs)
        self.session.add(row)
        self.session.commit()
        return row

    def processor(self, callback, pending=True):
        return SimpleNamespace(
            name="faces", active=True, handles_empty_scenes=False,
            load_model=Mock(), unload=Mock(), reset_for_media=Mock(), process=callback,
            get_pending_condition=lambda: Media.faces_extracted.is_(False) if pending else None,
        )

    def test_failure_does_not_complete_other_processors(self):
        for result in (False, TimeoutError("inference timed out")):
            with self.subTest(result=result):
                media = self.media()
                proc = self.processor(Mock(side_effect=result) if isinstance(result, Exception) else Mock(return_value=result))
                with patch.object(processing, "processors", [proc]):
                    self.assertFalse(processing._apply_processors(media, [object()], self.session))
                self.assertFalse(media.faces_extracted)
                self.assertFalse(media.embeddings_created)
                self.assertFalse(media.ran_auto_tagging)
                self.assertIsNone(media.laplacian_score)
                self.assertIn("faces", media.processing_error)

    def test_empty_scenes_do_not_mark_processors_complete(self):
        media = self.media()
        self.assertFalse(processing._apply_processors(media, [], self.session))
        self.assertFalse(media.faces_extracted)
        self.assertFalse(media.embeddings_created)
        self.assertIsNotNone(media.processing_error)

    def test_single_processor_no_progress_is_one_explicit_failure(self):
        media = self.media()
        Image.new("RGB", (20, 20)).save(media.path)
        proc = self.processor(Mock(return_value=True))
        task = self.task()
        with patch.object(processing, "processors", [proc]), patch.object(
            processing, "_is_task_cancelled", side_effect=lambda _: proc.process.call_count > 3
        ):
            processing.run_single_processor(task.id, "faces")
        self.session.expire_all()
        self.assertEqual(proc.process.call_count, 1)
        self.assertIn("progress", self.session.get(Media, media.id).processing_error)
        self.assertEqual(self.session.get(ProcessingTask, task.id).status, "failed")

    def test_forced_paging_does_not_skip_after_missing_row(self):
        ids = []
        for index in range(103):
            media = self.media()
            ids.append(media.id)
            if index:
                Image.new("RGB", (2, 2)).save(media.path)
        seen = []
        def process(media, session, **kwargs):
            seen.append(media.id)
            media.faces_extracted = True
            return True
        task = self.task()
        with patch.object(processing, "processors", [self.processor(process)]):
            processing.run_single_processor(task.id, "faces", force=True)
        self.assertEqual(seen, ids[1:])

    def test_forced_resume_retains_force_and_skips_committed_cursor(self):
        first, second = self.media(faces_extracted=True), self.media(faces_extracted=True)
        Image.new("RGB", (2, 2)).save(second.path)
        task = self.task(params={"processor_name": "faces", "force": True, "last_media_id": first.id})
        with patch.object(resume, "run_single_processor") as run:
            resume.build_resume_callable(task)(task.id)
            run.assert_called_once_with(task.id, "faces", force=True)
        seen = []
        proc = self.processor(lambda media, session, **kw: seen.append(media.id) or True)
        with patch.object(processing, "processors", [proc]):
            processing.run_single_processor(task.id, "faces", force=True)
        self.assertEqual(seen, [second.id])

    def test_failed_forced_item_is_retried_without_replaying_successes(self):
        first, second = self.media(), self.media()
        for media in (first, second):
            Image.new("RGB", (2, 2)).save(media.path)
        task = self.task()
        seen = []
        def process(media, session, **kwargs):
            seen.append(media.id)
            if media.id == first.id and seen.count(first.id) == 1:
                return False
            media.faces_extracted = True
            return True
        with patch.object(processing, "processors", [self.processor(process)]):
            processing.run_single_processor(task.id, "faces", force=True)
            self.session.expire_all()
            old = self.session.get(ProcessingTask, task.id)
            self.assertEqual(old.status, "failed")
            successor = self.task(params={**old.params, "resumed_from": old.id})
            processing.run_single_processor(successor.id, "faces", force=True)
        self.assertEqual(seen, [first.id, second.id, first.id])

    def test_selection_resume_does_not_reset_completed_processor_items(self):
        first, second = self.media(), self.media()
        for media in (first, second):
            Image.new("RGB", (2, 2)).save(media.path)
        task = self.task("run_processor_for_media", params={"completed_media": {"faces": [first.id]}})
        seen = []
        def process(media, session, **kw):
            seen.append(media.id)
            media.faces_extracted = True
            return True
        with patch.object(processing, "processors", [self.processor(process)]):
            processing.run_processors_for_media(task.id, ["faces"], [first.id, second.id])
        self.assertEqual(seen, [second.id])

    def test_matching_checkpoint_survives_cancel_and_matches_on_resume(self):
        face = self.face()
        task = self.task("process_media", status="cancelled", params={"pending_face_ids": [face.id]})
        with patch.object(processing, "match_faces_to_persons", return_value={face.id: 1}) as match:
            processing._record_face_matches(self.session, task, set())
            match.assert_not_called()
            task.status = "running"
            processing._record_face_matches(self.session, task, set())
            self.assertEqual(match.call_args.args[1], [face.id])
            self.assertEqual(task.params["pending_face_ids"], [])

    def test_matching_recovers_face_committed_before_item_checkpoint(self):
        face = self.face()
        task = self.task("process_media", status="running", params={
            "matching_media_id": face.media_id, "matching_after_face_id": face.id - 1,
        })
        processing._save_pending_faces(self.session, task, set())
        self.assertEqual(task.params["pending_face_ids"], [face.id])

    def test_cancelled_media_task_is_not_restarted_by_worker(self):
        task = self.task("process_media", status="cancelled")
        proc = self.processor(Mock(return_value=True))
        with patch.object(processing, "processors", [proc]):
            processing._run_media_processing(task.id)
        self.session.expire_all()
        self.assertEqual(self.session.get(ProcessingTask, task.id).status, "cancelled")
        proc.load_model.assert_not_called()

    def test_cancelled_scan_does_not_advance_chain(self):
        for status in ("cancelled", "failed", "interrupted"):
            with self.subTest(status=status):
                task = self.task("scan", status=status)
                with patch.object(pipeline, "run_scan"), patch.object(pipeline, "run_media_processing_and_chain") as run:
                    pipeline.run_scan_and_chain(task.id)
                    run.assert_not_called()

    def test_disabled_cleanup_does_not_advance_cancelled_chain(self):
        task = self.task("clean_missing_files", status="cancelled")
        with patch.object(settings.scan, "auto_clean_on_scan", False), patch.object(pipeline, "run_scan_and_chain") as run:
            pipeline.run_cleanup_and_chain(task.id)
            run.assert_not_called()

    def test_resumed_pipeline_preserves_continuation(self):
        task = self.task("process_media", params={"chain": True})
        self.assertIs(resume.build_resume_callable(task), processing.run_media_processing_and_chain)

    def test_completion_does_not_overwrite_concurrent_cancellation(self):
        task = self.task(status="running")
        raced = []
        def cancel(connection, cursor, statement, parameters, context, many):
            if statement.startswith("UPDATE processingtask") and not raced:
                raced.append(True)
                connection.exec_driver_sql("UPDATE processingtask SET status='cancelled' WHERE id=?", (task.id,))
        event.listen(self.engine, "before_cursor_execute", cancel)
        try:
            processing._finish_processing_task(self.session, task)
        finally:
            event.remove(self.engine, "before_cursor_execute", cancel)
        self.session.expire_all()
        self.assertEqual(self.session.get(ProcessingTask, task.id).status, "cancelled")

    def test_later_selected_processor_success_preserves_failure(self):
        media = self.media()
        Image.new("RGB", (2, 2)).save(media.path)
        task = self.task("run_processor_for_media")
        faces = self.processor(Mock(return_value=False))
        def tag(media, session, **kwargs):
            media.ran_auto_tagging = True
            return True
        tagger = self.processor(tag, pending=False)
        tagger.name = "auto_tagger"
        with patch.object(processing, "processors", [faces, tagger]):
            processing.run_processors_for_media(task.id, ["faces", "auto_tagger"], [media.id])
        self.session.expire_all()
        self.assertIn("faces", self.session.get(Media, media.id).processing_error)

    def test_selected_finalization_preserves_interruption_after_status_refresh(self):
        media = self.media()
        Image.new("RGB", (2, 2)).save(media.path)
        task = self.task("run_processor_for_media")
        def process(media, session, **kwargs):
            media.faces_extracted = True
            return True
        calls = []
        def stopped(task_id):
            calls.append(task_id)
            if len(calls) == 3:
                with self.engine.begin() as connection:
                    connection.exec_driver_sql("UPDATE processingtask SET status='interrupted' WHERE id=?", (task_id,))
                return True
            return False
        with patch.object(processing, "processors", [self.processor(process)]), patch.object(processing, "_is_task_cancelled", side_effect=stopped):
            processing.run_processors_for_media(task.id, ["faces"], [media.id])
        self.session.expire_all()
        self.assertEqual(self.session.get(ProcessingTask, task.id).status, "interrupted")

    def test_partial_video_embeddings_do_not_hide_inference_failure(self):
        media = self.media(duration=4)
        first = Scene(media_id=media.id, start_time=0, end_time=1)
        second = Scene(media_id=media.id, start_time=1, end_time=2, thumbnail_path="scene.jpg")
        self.session.add_all([first, second])
        self.session.commit()
        self.session.exec(text("INSERT INTO scene_embeddings(scene_id, media_id, embedding) VALUES (:sid, :mid, :vec)").bindparams(
            sid=first.id, mid=media.id, vec=np.ones(512, dtype=np.float32).tobytes()))
        self.session.commit()
        Image.new("RGB", (2, 2)).save(self.root / "scene.jpg")
        extractor = EmbeddingExtractor()
        extractor._clip_model = object()
        with patch.object(extractor, "_get_embeddings_batch", side_effect=TimeoutError("inference timeout")):
            self.assertFalse(extractor.process(media, self.session, [first, second]))
        self.assertFalse(media.embeddings_created)
        self.assertIn("timeout", media.processing_error)

    def test_pipeline_successor_has_resume_params(self):
        task = self.task("scan", status="completed")
        with patch.object(pipeline, "run_scan"), patch.object(pipeline, "run_media_processing_and_chain"):
            pipeline.run_scan_and_chain(task.id)
        successor = self.session.exec(select(ProcessingTask).where(ProcessingTask.task_type == "process_media")).one()
        self.assertIsInstance(successor.params, dict)

    def test_embedding_without_output_is_retryable_failure(self):
        media = self.media()
        self.assertFalse(EmbeddingExtractor().process(media, self.session, []))
        self.assertFalse(media.embeddings_created)
        self.assertIsNotNone(media.processing_error)


class FaceInferenceTests(DatabaseCase):
    def test_invalid_scene_is_retryable_failure(self):
        media = self.media()
        processor = FaceProcessor()
        processor.model = SimpleNamespace(get=Mock(return_value=[]))
        scene = (Scene(media_id=media.id, start_time=0, end_time=1), np.zeros((0, 0, 3)))
        self.assertFalse(processor.process(media, self.session, [scene]))
        self.assertFalse(media.faces_extracted)
        self.assertIsNotNone(media.processing_error)

    def test_inference_timeout_is_failure_not_zero_faces(self):
        media = self.media()
        processor = FaceProcessor()
        processor.model = SimpleNamespace(get=Mock(side_effect=TimeoutError("inference deadline")))
        self.assertFalse(processor.process(media, self.session, [Image.new("RGB", (20, 20))]))
        self.assertFalse(media.faces_extracted)
        self.assertIn("inference", media.processing_error.lower())

    def test_zero_faces_is_success(self):
        media = self.media()
        processor = FaceProcessor()
        processor.model = SimpleNamespace(get=Mock(return_value=[]))
        self.assertTrue(processor.process(media, self.session, [Image.new("RGB", (20, 20))]))
        self.assertTrue(media.faces_extracted)
        self.assertIsNone(media.processing_error)

    def test_video_thumbnail_uses_original_detector_coordinate_system(self):
        media = self.media(duration=4, width=1920, height=1080)
        Image.new("RGB", (360, 202)).save(self.root / "scene.jpg")
        scene = Scene(media_id=media.id, thumbnail_path="scene.jpg", start_time=0, end_time=1)
        processor = FaceProcessor()
        processor.model = SimpleNamespace(get=Mock(return_value=[object()]))
        def parsed(*args, **kwargs):
            return [(Face(media_id=media.id, bbox=[36, 20, 72, 40], kps=[[36, 20]] * 5), np.ones(512, dtype=np.float32))]
        with patch.object(processor, "_needs_padded_retry", return_value=False), patch.object(processor, "_parse_faces", side_effect=parsed):
            self.assertTrue(processor.process(media, self.session, [scene]))
        face = self.session.exec(select(Face)).one()
        self.assertEqual(face.bbox, [128, 71, 256, 143])
        self.assertAlmostEqual(face.kps[0][0], 128)
