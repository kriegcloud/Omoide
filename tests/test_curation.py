import tempfile
import unittest
from pathlib import Path

import numpy as np
from sqlmodel import Session, SQLModel, create_engine, select

from app.models import DatasetItem, Face, Media, Person, TrainingDataset
from app.services.curation import (
    auto_select_dataset,
    build_regularization_dataset,
    compute_dataset_analysis,
    farthest_point_sample,
    framing_for_ratio,
    hamming,
)


def make_media(path: Path, index: int, *, phash: str | None = None) -> Media:
    return Media(
        path=str(path / f"{index}.jpg"),
        filename=f"{index}.jpg",
        size=100,
        width=1000,
        height=1000,
        phash=phash,
        laplacian_score=float(index + 1) * 10,
    )


class CurationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
        SQLModel.metadata.create_all(self.engine)

    def tearDown(self):
        self.temp.cleanup()

    def test_framing_thresholds(self):
        self.assertEqual(framing_for_ratio(None), "none")
        self.assertEqual(framing_for_ratio(0.12), "closeup")
        self.assertEqual(framing_for_ratio(0.04), "portrait")
        self.assertEqual(framing_for_ratio(0.012), "half_body")
        self.assertEqual(framing_for_ratio(0.0119), "full_body")

    def test_hamming_and_duplicate_grouping(self):
        self.assertEqual(hamming("0000000000000000", "0000000000000003"), 2)
        with Session(self.engine) as session:
            dataset = TrainingDataset(name="Set", slug="set", trigger_word="x", class_token="person")
            rows = [
                make_media(self.root, 1, phash="0000000000000000"),
                make_media(self.root, 2, phash="0000000000000003"),
                make_media(self.root, 3, phash="ffffffffffffffff"),
            ]
            session.add(dataset)
            session.add_all(rows)
            session.commit()
            for position, row in enumerate(rows):
                session.add(DatasetItem(dataset_id=dataset.id, media_id=row.id, position=position))
            session.commit()
            analysis = compute_dataset_analysis(session, dataset)
            self.assertEqual(len(analysis["duplicates"]), 1)
            group = analysis["duplicates"][0]
            self.assertEqual(len(group["item_ids"]), 2)
            self.assertEqual(group["best_item_id"], max(group["item_ids"]))

    def test_farthest_point_sampling_picks_diverse_points(self):
        vectors = np.asarray([[0.0], [0.1], [10.0], [10.1]], dtype=np.float32)
        selected = farthest_point_sample(vectors, np.asarray([5, 1, 1, 1]), 2)
        self.assertEqual(selected[0], 0)
        self.assertIn(selected[1], (2, 3))

    def test_auto_select_dry_run_then_write(self):
        with Session(self.engine) as session:
            person = Person(name="Subject", appearance_count=3)
            dataset = TrainingDataset(name="Set", slug="set", trigger_word="x", class_token="person")
            rows = [make_media(self.root, index) for index in range(3)]
            session.add(person)
            session.add(dataset)
            session.add_all(rows)
            session.commit()
            dataset.person_id = person.id
            for position, row in enumerate(rows):
                session.add(DatasetItem(dataset_id=dataset.id, media_id=row.id, position=position))
                session.add(Face(media_id=row.id, person_id=person.id, bbox=[0, 0, 100 + position * 10, 100], frontality=0.9))
            session.commit()
            preview = auto_select_dataset(session, dataset, target_count=2, dry_run=True)
            self.assertEqual(len(preview["selected_item_ids"]), 2)
            self.assertFalse(any(session.exec(select(DatasetItem.excluded)).all()))
            result = auto_select_dataset(session, dataset, target_count=2, dry_run=False)
            excluded = session.exec(select(DatasetItem).where(DatasetItem.excluded.is_(True))).all()
            self.assertEqual(result, preview)
            self.assertEqual(len(excluded), 1)

    def test_regularization_uses_single_face_other_person_matching_gender(self):
        with Session(self.engine) as session:
            subject = Person(name="Subject", gender="female", appearance_count=1)
            right = Person(name="Right", gender="woman", appearance_count=1)
            wrong_gender = Person(name="Wrong", gender="male", appearance_count=1)
            session.add_all([subject, right, wrong_gender])
            session.commit()
            dataset = TrainingDataset(name="Subject", slug="subject", person_id=subject.id, trigger_word="subj", class_token="woman")
            valid = make_media(self.root, 1, phash="0000000000000000")
            two_faces = make_media(self.root, 2, phash="1111111111111111")
            wrong = make_media(self.root, 3, phash="2222222222222222")
            session.add(dataset)
            session.add_all([valid, two_faces, wrong])
            session.commit()
            session.add(Face(media_id=valid.id, person_id=right.id, bbox=[0, 0, 50, 50]))
            session.add(Face(media_id=two_faces.id, person_id=right.id, bbox=[0, 0, 50, 50]))
            session.add(Face(media_id=two_faces.id, person_id=subject.id, bbox=[60, 0, 40, 40]))
            session.add(Face(media_id=wrong.id, person_id=wrong_gender.id, bbox=[0, 0, 50, 50]))
            session.commit()
            regularization = build_regularization_dataset(session, dataset, target_count=10)
            items = session.exec(select(DatasetItem).where(DatasetItem.dataset_id == regularization.id)).all()
            session.refresh(dataset)
            self.assertEqual([item.media_id for item in items], [valid.id])
            self.assertEqual(regularization.class_token, "woman")
            self.assertEqual(regularization.caption_template, "{class}")
            self.assertEqual(dataset.regularization_dataset_id, regularization.id)


if __name__ == "__main__":
    unittest.main()
