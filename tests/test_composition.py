import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlmodel import Session, SQLModel, create_engine, select

from app.models import DatasetItem, Face, Media, Person, TrainingDataset
from app.processors.faces import FaceProcessor
from app.services.curation import (
    compute_dataset_analysis,
    fill_dataset_gaps,
    kmeans,
)
from app.tasks.backfill import update_pose_matches


class CompositionTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}
        )
        SQLModel.metadata.create_all(self.engine)

    def test_pose_derivation_signs(self):
        frontal = [[30, 30], [70, 30], [50, 50], [35, 70], [65, 70]]
        yaw, pitch = FaceProcessor._estimate_pose(frontal)
        self.assertAlmostEqual(yaw, 0.0)
        self.assertAlmostEqual(pitch, 0.0)

        left = [[30, 30], [70, 30], [35, 50], [35, 70], [65, 70]]
        right = [[30, 30], [70, 30], [65, 50], [35, 70], [65, 70]]
        self.assertLess(FaceProcessor._estimate_pose(left)[0], 0)
        self.assertGreater(FaceProcessor._estimate_pose(right)[0], 0)
        self.assertEqual(FaceProcessor._estimate_pose(None), (None, None))

    def test_composition_histograms_and_default_gaps(self):
        with Session(self.engine) as session:
            person = Person(name="Subject", appearance_count=2)
            dataset = TrainingDataset(
                name="Set", slug="set", trigger_word="x", class_token="person"
            )
            session.add_all([person, dataset])
            session.commit()
            dataset.person_id = person.id
            for index, (face_width, yaw) in enumerate(((400, 0.0), (150, -50.0))):
                media = Media(
                    path=f"/tmp/composition-{index}.jpg",
                    filename=f"composition-{index}.jpg",
                    size=1,
                    width=1000,
                    height=1000,
                    laplacian_score=100,
                )
                session.add(media)
                session.flush()
                session.add(
                    DatasetItem(dataset_id=dataset.id, media_id=media.id, position=index)
                )
                session.add(
                    Face(
                        media_id=media.id,
                        person_id=person.id,
                        bbox=[0, 0, face_width, face_width],
                        yaw=yaw,
                        pitch=0,
                    )
                )
            session.commit()
            analysis = compute_dataset_analysis(session, dataset)

            self.assertEqual(analysis["composition"]["framing"]["close"], 1)
            self.assertEqual(analysis["composition"]["framing"]["half"], 1)
            self.assertEqual(analysis["composition"]["yaw"]["frontal"], 1)
            self.assertEqual(analysis["composition"]["yaw"]["left_profile"], 1)
            frontal_gap = next(
                gap
                for gap in analysis["gaps"]
                if gap["dimension"] == "yaw" and gap["band"] == "frontal"
            )
            self.assertEqual(frontal_gap, {
                "dimension": "yaw", "band": "frontal", "have": 1,
                "want": 1, "deficit": 0,
            })

    def test_fill_gaps_round_robin_respects_max_add(self):
        with Session(self.engine) as session:
            dataset = TrainingDataset(
                name="Set", slug="set", trigger_word="x", class_token="person"
            )
            media = [
                Media(path=f"/tmp/gap-{index}.jpg", filename=f"gap-{index}.jpg", size=1)
                for index in range(4)
            ]
            session.add(dataset)
            session.add_all(media)
            session.commit()
            fake_gaps = [
                {"dimension": "yaw", "band": "left_profile", "deficit": 2, "candidates": [media[0].id, media[1].id]},
                {"dimension": "framing", "band": "full", "deficit": 2, "candidates": [media[2].id, media[3].id]},
            ]
            with patch("app.services.curation.dataset_gaps", return_value=fake_gaps):
                added = fill_dataset_gaps(session, dataset, max_add=3)
            self.assertEqual(added, [media[0].id, media[2].id, media[1].id])
            self.assertEqual(
                len(session.exec(select(DatasetItem)).all()),
                3,
            )

    def test_kmeans_toy_vectors_is_deterministic(self):
        vectors = np.asarray(
            [[0, 0], [0, 0.1], [10, 10], [10.1, 10], [20, 0], [20.1, 0]],
            dtype=np.float32,
        )
        labels, centers = kmeans(vectors, 3, seed=16)
        labels_again, centers_again = kmeans(vectors, 3, seed=16)
        np.testing.assert_array_equal(labels, labels_again)
        np.testing.assert_allclose(centers, centers_again)
        self.assertEqual(len(set(labels.tolist())), 3)

    def test_pose_backfill_updates_existing_face_without_detaching_person(self):
        person = Person(id=7, name="Subject", appearance_count=1)
        face = Face(
            id=9,
            media_id=3,
            person_id=person.id,
            bbox=[10, 10, 40, 40],
        )
        detection = type("Detection", (), {
            "bbox": np.asarray([10, 10, 50, 50], dtype=np.float32),
            "kps": np.asarray([[20, 20], [40, 20], [30, 30], [22, 42], [38, 42]], dtype=np.float32),
        })()
        self.assertEqual(update_pose_matches([face], [detection]), 1)
        self.assertEqual(face.id, 9)
        self.assertEqual(face.person_id, 7)
        self.assertAlmostEqual(face.yaw, 0.0)
        self.assertIsNotNone(face.kps)

    def test_migration_has_single_head(self):
        root = Path(__file__).resolve().parents[1]
        config = Config(str(root / "alembic.ini"))
        config.set_main_option("script_location", str(root / "alembic"))
        heads = ScriptDirectory.from_config(config).get_heads()
        self.assertEqual(heads, ["d7e8f9a0b1c3"])


if __name__ == "__main__":
    unittest.main()
