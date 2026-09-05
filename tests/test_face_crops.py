import unittest

from sqlmodel import Session, SQLModel, create_engine

from app.api.datasets import batch_crop_items
from app.models import DatasetItem, Face, Media, Person, TrainingDataset
from app.schemas.dataset import DatasetBatchCropRequest
from app.services.face_crops import bbox_to_source_pixels, suggest_crop


class FaceCropTests(unittest.TestCase):
    def test_bbox_scaling_uses_detector_long_side(self):
        bbox = [100, 80, 200, 160]
        self.assertEqual(
            bbox_to_source_pixels(bbox, 4032, 3024),
            (315, 252, 630, 504),
        )
        self.assertEqual(
            bbox_to_source_pixels(bbox, 1000, 750),
            (100, 80, 200, 160),
        )

    def test_each_framing_contains_and_horizontally_centres_face(self):
        face = (1700, 700, 300, 300)
        face_center = face[0] + face[2] / 2
        heights = []
        for framing in ("closeup", "portrait", "half_body", "full_body"):
            with self.subTest(framing=framing):
                crop, _ = suggest_crop(face, 4000, 5000, framing, "2:3")
                self.assertLessEqual(crop.x, face[0])
                self.assertLessEqual(crop.y, face[1])
                self.assertGreaterEqual(crop.x + crop.width, face[0] + face[2])
                self.assertGreaterEqual(crop.y + crop.height, face[1] + face[3])
                self.assertAlmostEqual(crop.x + crop.width / 2, face_center, delta=1)
                heights.append(crop.height)
        self.assertEqual(heights, sorted(heights))

    def test_requested_aspect_is_exact_when_it_fits(self):
        for aspect, ratio in {
            "1:1": (1, 1),
            "2:3": (2, 3),
            "3:4": (3, 4),
            "4:5": (4, 5),
            "9:16": (9, 16),
        }.items():
            with self.subTest(aspect=aspect):
                crop, _ = suggest_crop((1800, 1500, 200, 200), 4000, 4000, "portrait", aspect)
                self.assertEqual(crop.width * ratio[1], crop.height * ratio[0])

    def test_crop_clamps_at_image_edges(self):
        crop, _ = suggest_crop((0, 0, 160, 180), 1000, 800, "portrait", "4:5")
        self.assertEqual((crop.x, crop.y), (0, 0))
        self.assertLessEqual(crop.x + crop.width, 1000)
        self.assertLessEqual(crop.y + crop.height, 800)
        self.assertGreaterEqual(crop.width, 160)
        self.assertGreaterEqual(crop.height, 180)

    def test_batch_crop_skips_item_without_subject_face(self):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
        SQLModel.metadata.create_all(engine)
        with Session(engine) as session:
            person = Person(name="Subject", appearance_count=1)
            media_with_face = Media(
                path="/tmp/face.jpg", filename="face.jpg", size=1, width=1200, height=1600
            )
            media_without_face = Media(
                path="/tmp/no-face.jpg", filename="no-face.jpg", size=1, width=1200, height=1600
            )
            session.add_all([person, media_with_face, media_without_face])
            session.commit()
            dataset = TrainingDataset(
                name="Subject",
                slug="subject",
                person_id=person.id,
                trigger_word="subjectx",
                class_token="person",
            )
            session.add(dataset)
            session.commit()
            first = DatasetItem(dataset_id=dataset.id, media_id=media_with_face.id, position=0)
            second = DatasetItem(dataset_id=dataset.id, media_id=media_without_face.id, position=1)
            session.add_all([first, second])
            session.commit()
            session.add(
                Face(
                    media_id=media_with_face.id,
                    person_id=person.id,
                    bbox=[500, 250, 180, 180],
                )
            )
            session.commit()

            result = batch_crop_items(
                dataset.id,
                DatasetBatchCropRequest(
                    framing="portrait",
                    aspect="2:3",
                    overwrite_existing_ops=False,
                ),
                session,
            )

            self.assertEqual(result.updated_ids, [first.id])
            self.assertEqual(len(result.skipped), 1)
            self.assertEqual(result.skipped[0].item_id, second.id)
            self.assertEqual(result.skipped[0].reason, "No subject face")
            session.refresh(first)
            self.assertEqual(first.edit_ops[0]["op"], "crop")


if __name__ == "__main__":
    unittest.main()
