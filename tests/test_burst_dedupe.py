import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlmodel import Session, SQLModel, create_engine

from app.models import DatasetItem, Face, Media, TrainingDataset
from app.services.curation import (
    compute_dataset_analysis,
    dedupe_dataset,
    reinclude_dataset_items,
)


class BurstDedupeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}
        )
        SQLModel.metadata.create_all(self.engine)

    def tearDown(self):
        self.engine.dispose()
        self.temp.cleanup()

    def _dataset(self, session: Session) -> TrainingDataset:
        dataset = TrainingDataset(
            name="Dedupe",
            slug="dedupe",
            trigger_word="subject",
            class_token="person",
        )
        session.add(dataset)
        session.commit()
        session.refresh(dataset)
        return dataset

    def _item(
        self,
        session: Session,
        dataset: TrainingDataset,
        index: int,
        *,
        phash: str,
        created_at: datetime,
        sharpness: float,
        yaw: float | None = None,
        face_size: int = 100,
    ) -> DatasetItem:
        media = Media(
            path=str(self.root / f"{index}.jpg"),
            filename=f"{index}.jpg",
            size=1,
            width=1000,
            height=1000,
            phash=phash,
            created_at=created_at,
            laplacian_score=sharpness,
        )
        session.add(media)
        session.commit()
        item = DatasetItem(
            dataset_id=dataset.id,
            media_id=media.id,
            position=index,
        )
        session.add(item)
        session.add(
            Face(
                media_id=media.id,
                bbox=[0, 0, face_size, face_size],
                yaw=yaw,
                det_score=0.9,
            )
        )
        session.commit()
        session.refresh(item)
        return item

    def test_burst_grouping_requires_time_and_phash(self):
        with Session(self.engine) as session:
            dataset = self._dataset(session)
            now = datetime.now()
            first = self._item(
                session, dataset, 1, phash="0000000000000000",
                created_at=now, sharpness=10,
            )
            second = self._item(
                session, dataset, 2, phash="00000000000000ff",
                created_at=now + timedelta(seconds=3), sharpness=20,
            )
            self._item(
                session, dataset, 3, phash="0000000000000fff",
                created_at=now + timedelta(seconds=10), sharpness=30,
            )

            result = dedupe_dataset(
                session, dataset, mode="burst", keep="sharpest",
                pose_aware=False, dry_run=True,
            )

            self.assertEqual(len(result["groups"]), 1)
            self.assertEqual(result["groups"][0]["kind"], "burst")
            self.assertEqual(result["groups"][0]["keep"], [second.id])
            self.assertEqual(result["groups"][0]["drop"], [first.id])

    def test_near_grouping_uses_phash_without_time_limit(self):
        with Session(self.engine) as session:
            dataset = self._dataset(session)
            now = datetime.now()
            first = self._item(
                session, dataset, 1, phash="0000000000000000",
                created_at=now, sharpness=10,
            )
            second = self._item(
                session, dataset, 2, phash="0000000000000003",
                created_at=now + timedelta(days=30), sharpness=20,
            )

            analysis = compute_dataset_analysis(session, dataset)

            self.assertEqual(
                analysis["groups"],
                [{"kind": "near", "keep": [second.id], "drop": [first.id]}],
            )

    def test_pose_aware_keeps_one_per_distinct_yaw_band(self):
        with Session(self.engine) as session:
            dataset = self._dataset(session)
            now = datetime.now()
            items = [
                self._item(
                    session, dataset, index, phash="0000000000000000",
                    created_at=now + timedelta(days=index),
                    sharpness=float(index), yaw=yaw,
                )
                for index, yaw in enumerate((-35.0, -28.0, 0.0, 32.0), start=1)
            ]

            result = dedupe_dataset(
                session, dataset, mode="near", keep="sharpest",
                pose_aware=True, dry_run=True,
            )

            group = result["groups"][0]
            self.assertEqual(set(group["keep"]), {items[1].id, items[2].id, items[3].id})
            self.assertEqual(group["drop"], [items[0].id])

    def test_keep_rule_sharpest_vs_largest_face(self):
        with Session(self.engine) as session:
            dataset = self._dataset(session)
            now = datetime.now()
            sharp = self._item(
                session, dataset, 1, phash="0000000000000000",
                created_at=now, sharpness=100, face_size=80,
            )
            large = self._item(
                session, dataset, 2, phash="0000000000000001",
                created_at=now + timedelta(days=1), sharpness=20, face_size=240,
            )

            sharpest = dedupe_dataset(
                session, dataset, mode="near", keep="sharpest",
                pose_aware=False, dry_run=True,
            )
            largest = dedupe_dataset(
                session, dataset, mode="near", keep="largest_face",
                pose_aware=False, dry_run=True,
            )

            self.assertEqual(sharpest["groups"][0]["keep"], [sharp.id])
            self.assertEqual(largest["groups"][0]["keep"], [large.id])

    def test_dry_run_does_not_change_items_and_apply_sets_reason(self):
        with Session(self.engine) as session:
            dataset = self._dataset(session)
            now = datetime.now()
            keep = self._item(
                session, dataset, 1, phash="0000000000000000",
                created_at=now, sharpness=20,
            )
            drop = self._item(
                session, dataset, 2, phash="00000000000000ff",
                created_at=now + timedelta(seconds=1), sharpness=10,
            )

            preview = dedupe_dataset(
                session, dataset, mode="burst", keep="sharpest",
                pose_aware=False, dry_run=True,
            )
            session.refresh(drop)
            self.assertEqual(preview["excluded"], 1)
            self.assertFalse(drop.excluded)
            self.assertIsNone(drop.excluded_reason)

            applied = dedupe_dataset(
                session, dataset, mode="burst", keep="sharpest",
                pose_aware=False, dry_run=False,
            )
            session.refresh(keep)
            session.refresh(drop)
            self.assertEqual(applied, preview)
            self.assertFalse(keep.excluded)
            self.assertTrue(drop.excluded)
            self.assertEqual(drop.excluded_reason, "burst")

    def test_reinclude_restores_only_requested_reason(self):
        with Session(self.engine) as session:
            dataset = self._dataset(session)
            now = datetime.now()
            burst = self._item(
                session, dataset, 1, phash="0000000000000000",
                created_at=now, sharpness=10,
            )
            duplicate = self._item(
                session, dataset, 2, phash="ffffffffffffffff",
                created_at=now, sharpness=10,
            )
            manual = self._item(
                session, dataset, 3, phash="aaaaaaaaaaaaaaaa",
                created_at=now, sharpness=10,
            )
            for item, reason in (
                (burst, "burst"),
                (duplicate, "duplicate"),
                (manual, "manual"),
            ):
                item.excluded = True
                item.excluded_reason = reason
                session.add(item)
            session.commit()

            included = reinclude_dataset_items(
                session, dataset, reason="burst"
            )

            self.assertEqual(included, 1)
            for item in (burst, duplicate, manual):
                session.refresh(item)
            self.assertFalse(burst.excluded)
            self.assertIsNone(burst.excluded_reason)
            self.assertTrue(duplicate.excluded)
            self.assertEqual(duplicate.excluded_reason, "duplicate")
            self.assertTrue(manual.excluded)
            self.assertEqual(manual.excluded_reason, "manual")

    def test_migration_head_is_single(self):
        script = ScriptDirectory.from_config(Config("alembic.ini"))
        self.assertEqual(script.get_heads(), ["f9a0b1c2d3e5"])


if __name__ == "__main__":
    unittest.main()
