import unittest
from datetime import datetime

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlmodel import Session, SQLModel, create_engine

from app.api.datasets import list_triage, review_item
from app.config import settings
from app.models import DatasetItem, Face, Media, TrainingDataset


class TriageTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}
        )
        SQLModel.metadata.create_all(self.engine)
        self.previous_presentation = settings.general.presentation_mode
        settings.general.presentation_mode = False

    def tearDown(self):
        settings.general.presentation_mode = self.previous_presentation
        self.engine.dispose()

    def _dataset(self, session: Session) -> TrainingDataset:
        dataset = TrainingDataset(
            name="Triage",
            slug="triage",
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
        position: int,
        *,
        excluded: bool = False,
        reviewed: bool = False,
        caption: str | None = None,
        width: int = 1000,
        height: int = 750,
    ) -> DatasetItem:
        media = Media(
            path=f"/media/{position}.jpg",
            filename=f"{position}.jpg",
            size=1,
            width=width,
            height=height,
        )
        session.add(media)
        session.commit()
        item = DatasetItem(
            dataset_id=dataset.id,
            media_id=media.id,
            position=position,
            excluded=excluded,
            reviewed_at=datetime.now() if reviewed else None,
            caption_override=caption,
        )
        session.add(item)
        session.commit()
        session.refresh(item)
        return item

    def test_unreviewed_items_sort_first_and_envelope_counts(self):
        with Session(self.engine) as session:
            dataset = self._dataset(session)
            reviewed = self._item(session, dataset, 0, reviewed=True)
            second = self._item(session, dataset, 2)
            first = self._item(session, dataset, 1)

            page = list_triage(dataset.id, None, "all", 50, session)

            self.assertEqual(
                [entry.item.id for entry in page.items],
                [first.id, second.id, reviewed.id],
            )
            self.assertEqual(page.reviewed_count, 1)
            self.assertEqual(page.total_count, 3)
            self.assertIsNone(page.next_cursor)

    def test_findings_and_excluded_filters(self):
        with Session(self.engine) as session:
            dataset = self._dataset(session)
            finding = self._item(
                session,
                dataset,
                0,
                caption="a visible watermark in the corner",
            )
            excluded = self._item(
                session,
                dataset,
                1,
                excluded=True,
                caption="a person standing outside today",
            )
            self._item(
                session,
                dataset,
                2,
                caption="a person sitting beside a window",
            )

            findings = list_triage(dataset.id, None, "findings", 50, session)
            excluded_page = list_triage(
                dataset.id, None, "excluded", 50, session
            )

            self.assertEqual([entry.item.id for entry in findings.items], [finding.id])
            self.assertIn("text-artifacts", {row.code for row in findings.items[0].findings})
            self.assertEqual(
                [entry.item.id for entry in excluded_page.items], [excluded.id]
            )

    def test_review_endpoint_sets_reviewed_at(self):
        with Session(self.engine) as session:
            dataset = self._dataset(session)
            item = self._item(session, dataset, 0)

            result = review_item(dataset.id, item.id, session)

            self.assertIsNotNone(result.reviewed_at)
            session.refresh(item)
            self.assertIsNotNone(item.reviewed_at)

    def test_face_bbox_is_mapped_to_source_pixels(self):
        with Session(self.engine) as session:
            dataset = self._dataset(session)
            item = self._item(
                session, dataset, 0, width=2560, height=1280
            )
            session.add(
                Face(
                    media_id=item.media_id,
                    bbox=[100, 50, 200, 300],
                    frontality=0.9,
                )
            )
            session.commit()

            entry = list_triage(dataset.id, None, "all", 50, session).items[0]

            self.assertEqual(entry.face_bbox, (200, 100, 400, 600))
            self.assertIsNotNone(entry.face_crop_suggestion)

    def test_migration_has_single_expected_head(self):
        script = ScriptDirectory.from_config(Config("alembic.ini"))
        self.assertEqual(script.get_heads(), ["f9a0b1c2d3e5"])


if __name__ == "__main__":
    unittest.main()
