import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import cv2
import numpy as np
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlmodel import Session, SQLModel, create_engine, select

from app.api.datasets import (
    compare_training_run_likeness,
    get_training_run_likeness,
    rescore_training_run,
)
from app.config import settings
from app.models import (
    DatasetExport,
    DatasetExportLayout,
    DatasetExportStatus,
    TrainingDataset,
    TrainingRun,
    TrainingSample,
)
from app.services.likeness import (
    LikenessScorer,
    refresh_run_summary,
    score_pending_samples,
)


class FakeDetector:
    def __init__(self, faces):
        self.faces = faces
        self.calls = 0

    def get(self, _image):
        self.calls += 1
        return self.faces


class FixedScorer:
    def __init__(self, likeness=0.75):
        self.likeness = likeness
        self.calls = 0

    def score_image(self, _path, _centroid):
        self.calls += 1
        return self.likeness, 1, [1, 2, 30, 40]


class LikenessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}
        )
        SQLModel.metadata.create_all(self.engine)
        self.previous_presentation_mode = settings.general.presentation_mode
        settings.general.presentation_mode = False

    def tearDown(self):
        settings.general.presentation_mode = self.previous_presentation_mode
        self.engine.dispose()
        self.temp.cleanup()

    def _run(self, session: Session, *, person_id=7):
        dataset = TrainingDataset(
            name="Portraits",
            slug=f"portraits-{person_id}",
            person_id=person_id,
            trigger_word="subject",
            class_token="person",
        )
        session.add(dataset)
        session.commit()
        export = DatasetExport(
            dataset_id=dataset.id,
            layout=DatasetExportLayout.AI_TOOLKIT,
            status=DatasetExportStatus.COMPLETED,
            output_dir=str(self.root),
        )
        session.add(export)
        session.commit()
        run = TrainingRun(
            dataset_id=dataset.id,
            export_id=export.id,
            run_dir=str(self.root),
            config_yaml="config: {process: [{network: {linear: 16}, train: {lr: 0.0001}}]}",
            steps=1000,
        )
        session.add(run)
        session.commit()
        session.refresh(dataset)
        session.refresh(run)
        return dataset, run

    def _image(self, name="sample.jpg", width=320, height=240):
        path = self.root / name
        self.assertTrue(cv2.imwrite(str(path), np.zeros((height, width, 3), dtype=np.uint8)))
        return path

    def test_scoring_picks_largest_face_and_maps_bbox_to_sample_pixels(self):
        small = SimpleNamespace(
            bbox=np.array([10, 10, 210, 210], dtype=np.float32),
            normed_embedding=np.array([0, 1], dtype=np.float32),
        )
        large = SimpleNamespace(
            bbox=np.array([300, 100, 900, 500], dtype=np.float32),
            normed_embedding=np.array([1, 0], dtype=np.float32),
        )
        scorer = LikenessScorer.__new__(LikenessScorer)
        scorer.detector = FakeDetector([small, large])

        likeness, face_count, bbox = scorer.score_image(
            self._image(width=2560, height=1280), np.array([1, 0], dtype=np.float32)
        )

        self.assertAlmostEqual(likeness, 1.0)
        self.assertEqual(face_count, 2)
        self.assertEqual(bbox, [600, 200, 1200, 800])

    def test_no_face_sample_is_terminal_with_null_likeness(self):
        scorer = LikenessScorer.__new__(LikenessScorer)
        scorer.detector = FakeDetector([])
        with Session(self.engine) as session:
            _, run = self._run(session)
            sample = TrainingSample(run_id=run.id, step=100, path=str(self._image()))
            session.add(sample)
            session.commit()
            with (
                patch("app.services.likeness._scorer", scorer),
                patch(
                    "app.services.likeness.person_centroid",
                    return_value=np.array([1, 0], dtype=np.float32),
                ),
            ):
                score_pending_samples(session)
            session.refresh(sample)
            self.assertIsNotNone(sample.scored_at)
            self.assertIsNone(sample.likeness)
            self.assertEqual(sample.face_count, 0)

    def test_summary_and_best_step_are_computed_from_means(self):
        with Session(self.engine) as session:
            _, run = self._run(session)
            session.add_all(
                [
                    TrainingSample(run_id=run.id, step=100, path="a", likeness=0.5),
                    TrainingSample(run_id=run.id, step=100, path="b", likeness=0.9),
                    TrainingSample(run_id=run.id, step=200, path="c", likeness=0.8),
                ]
            )
            session.commit()
            refresh_run_summary(session, run.id)
            session.commit()
            session.refresh(run)
            self.assertEqual(run.likeness_best_step, 200)
            self.assertAlmostEqual(run.likeness_best, 0.8)
            self.assertEqual(run.likeness_summary["steps"][0]["n"], 2)
            self.assertAlmostEqual(run.likeness_summary["steps"][0]["mean"], 0.7)
            self.assertAlmostEqual(run.likeness_summary["steps"][0]["max"], 0.9)

    def test_per_tick_limit_is_honoured(self):
        scorer = FixedScorer()
        with Session(self.engine) as session:
            _, run = self._run(session)
            session.add_all(
                [TrainingSample(run_id=run.id, step=index, path=str(self._image(f"{index}.jpg"))) for index in range(5)]
            )
            session.commit()
            with (
                patch("app.services.likeness._scorer", scorer),
                patch(
                    "app.services.likeness.person_centroid",
                    return_value=np.array([1, 0], dtype=np.float32),
                ),
            ):
                self.assertEqual(score_pending_samples(session, limit=2), 2)
            scored = session.exec(
                select(TrainingSample).where(TrainingSample.scored_at.is_not(None))
            ).all()
            self.assertEqual(len(scored), 2)
            self.assertEqual(scorer.calls, 2)

    def test_dataset_without_person_is_marked_without_calling_scorer(self):
        scorer = FixedScorer()
        with Session(self.engine) as session:
            _, run = self._run(session, person_id=None)
            sample = TrainingSample(run_id=run.id, step=10, path="unused")
            session.add(sample)
            session.commit()
            with patch("app.services.likeness._scorer", scorer):
                score_pending_samples(session)
            session.refresh(sample)
            self.assertIsNotNone(sample.scored_at)
            self.assertIsNone(sample.likeness)
            self.assertEqual(scorer.calls, 0)

    def test_likeness_and_rescore_routes(self):
        with Session(self.engine) as session:
            dataset, run = self._run(session)
            sample = TrainingSample(
                run_id=run.id,
                step=100,
                path="sample",
                likeness=0.8,
                face_count=1,
                scored_at=run.created_at,
            )
            session.add(sample)
            session.commit()
            refresh_run_summary(session, run.id)
            session.commit()
            session.refresh(run)

            single = get_training_run_likeness(run.id, session)
            overlay = compare_training_run_likeness(dataset.id, None, session)
            self.assertEqual(single.best_step, 100)
            self.assertEqual(single.scored, 1)
            self.assertEqual(overlay[0].run_id, run.id)

            response = rescore_training_run(run.id, session)
            self.assertEqual(response, {"queued": 1})
            session.refresh(sample)
            session.refresh(run)
            self.assertIsNone(sample.scored_at)
            self.assertIsNone(sample.likeness)
            self.assertIsNone(run.likeness_best_step)

    def test_migration_head_remains_single(self):
        script = ScriptDirectory.from_config(Config("alembic.ini"))
        self.assertEqual(script.get_heads(), ["e8f9a0b1c2d4"])


if __name__ == "__main__":
    unittest.main()
