from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
from sqlmodel import text

from round6_support import DatabaseCase
from app.models import ProcessingTask, Status, Tag
from app.services.face_inference import AdaFaceSocketAnalysis
from app.tasks import auto_tagging, backfill, person_clustering


class TaskEdgeTests(DatabaseCase):
    def test_cancelled_and_interrupted_clustering_statuses_are_detected(self):
        for status in Status:
            task = ProcessingTask(task_type="cluster", status=status)
            self.session.add(task)
            self.session.commit()
            with self.subTest(status=status):
                self.assertEqual(
                    person_clustering._is_task_cancelled(self.session, task.id),
                    status in {Status.CANCELLED, Status.INTERRUPTED},
                )
        self.assertFalse(person_clustering._is_task_cancelled(self.session, "absent"))

    def test_existing_auto_tag_is_a_tag_model(self):
        tag = Tag(name="beach")
        self.session.add(tag)
        self.session.commit()
        cache = {}
        result = auto_tagging._get_or_create_tag(self.session, "Beach", cache)
        self.assertIsInstance(result, Tag)
        self.assertEqual(result.id, tag.id)
        self.assertIs(cache["beach"], result)

    def test_existing_auto_tag_task_is_returned_without_starting_worker(self):
        task = ProcessingTask(task_type="auto_tag_custom", status=Status.RUNNING)
        self.session.add(task)
        self.session.commit()
        with patch.object(auto_tagging.db, "engine", self.engine), patch.object(auto_tagging.threading, "Thread") as worker:
            result = auto_tagging.schedule_custom_auto_tagging(["beach"])
        self.assertIsInstance(result, ProcessingTask)
        self.assertEqual(result.id, task.id)
        worker.assert_not_called()

    def test_adaface_quality_backfill_uses_demographics_detector(self):
        task = ProcessingTask(task_type="backfill_face_quality")
        self.session.add(task)
        face = self.face(thumbnail_path="face.jpg")
        (self.root / "face.jpg").touch()
        detector = SimpleNamespace(detect=Mock(return_value=(np.array([[0, 0, 20, 20, 0.91]]), None)))
        processor = SimpleNamespace(
            name="faces", model=AdaFaceSocketAnalysis(self.root / "unused.sock"),
            demographics_model=SimpleNamespace(det_model=detector), load_model=Mock(),
        )
        with (
            patch.object(backfill, "processors", [processor]),
            patch.object(backfill.db, "engine", self.engine),
            patch.object(backfill, "heavy_writer", return_value=nullcontext(True)),
            patch.object(backfill.cv2, "imread", return_value=np.zeros((20, 20, 3), dtype=np.uint8)),
        ):
            backfill.run_backfill_face_quality(task.id)
        self.session.refresh(face)
        self.session.refresh(task)
        self.assertAlmostEqual(face.det_score, 0.91)
        self.assertEqual(task.status, Status.COMPLETED)
        detector.detect.assert_called_once()

    def test_leftover_matching_refreshes_person_centroid_and_appearance_count(self):
        person = self.person()
        face = self.face()
        vector = np.zeros(512, dtype=np.float32)
        vector[0] = 1
        self.session.exec(text("INSERT INTO face_embeddings(face_id, person_id, embedding) VALUES (:fid, -1, :v)").bindparams(fid=face.id, v=vector.tobytes()))
        self.session.commit()
        remaining = person_clustering._match_unassigned_to_existing(
            self.session, [face.id], vector.reshape(1, -1), "absent",
            prototypes=(np.array([person.id]), vector.reshape(1, -1)),
        )
        self.assertEqual(remaining, [])
        self.session.refresh(person)
        self.assertEqual(person.appearance_count, 1)
        row = self.session.exec(text("SELECT embedding FROM person_embeddings WHERE person_id=:pid").bindparams(pid=person.id)).first()
        self.assertIsNotNone(row, "Leftover matches must update the stored person centroid")
        np.testing.assert_allclose(np.frombuffer(row[0], dtype=np.float32), vector)
