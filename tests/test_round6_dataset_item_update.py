"""PATCH /api/datasets/{id}/items/{item} must accept an ISO reviewed_at timestamp.

Lane M reproduced a StatementError because the route dumped the payload in JSON
mode, handing SQLAlchemy a string for a DateTime column.
"""

import unittest
from datetime import datetime, timezone

from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine

from app.api.datasets import update_item
from app.models import DatasetItem, Media, TrainingDataset
from app.schemas.dataset import DatasetItemUpdate


class DatasetItemUpdateTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False})

        @event.listens_for(self.engine, "connect")
        def foreign_keys(connection, _record):
            connection.execute("PRAGMA foreign_keys=ON")

        SQLModel.metadata.create_all(self.engine)

    def tearDown(self):
        self.engine.dispose()

    def _item(self, session):
        dataset = TrainingDataset(name="Test", slug="test", trigger_word="subjectx", class_token="person")
        session.add(dataset)
        session.flush()
        media = Media(path="/test/0.jpg", filename="0.jpg", size=1, width=100, height=100)
        session.add(media)
        session.flush()
        item = DatasetItem(dataset_id=dataset.id, media_id=media.id, position=0)
        session.add(item)
        session.commit()
        return dataset, item

    def test_iso_reviewed_at_is_stored_as_naive_utc_datetime(self):
        with Session(self.engine) as session:
            dataset, item = self._item(session)
            payload = DatasetItemUpdate.model_validate({"reviewed_at": "2026-09-10T15:00:00Z", "excluded": False})
            update_item(dataset.id, item.id, payload, session)
            session.refresh(item)
            self.assertIsInstance(item.reviewed_at, datetime)
            self.assertIsNone(item.reviewed_at.tzinfo)
            self.assertEqual(item.reviewed_at, datetime(2026, 9, 10, 15, 0, 0))
            self.assertFalse(item.excluded)

    def test_offset_timestamp_is_normalised_to_utc(self):
        with Session(self.engine) as session:
            dataset, item = self._item(session)
            payload = DatasetItemUpdate.model_validate({"reviewed_at": "2026-09-10T10:00:00-05:00"})
            update_item(dataset.id, item.id, payload, session)
            session.refresh(item)
            self.assertEqual(item.reviewed_at, datetime(2026, 9, 10, 15, 0, 0))

    def test_unset_fields_are_not_touched(self):
        with Session(self.engine) as session:
            dataset, item = self._item(session)
            item.weight = 1.5
            session.commit()
            update_item(dataset.id, item.id, DatasetItemUpdate.model_validate({"excluded": True, "excluded_reason": "manual"}), session)
            session.refresh(item)
            self.assertEqual(item.weight, 1.5)
            self.assertTrue(item.excluded)
            self.assertIsNone(item.reviewed_at)


if __name__ == "__main__":
    unittest.main()
