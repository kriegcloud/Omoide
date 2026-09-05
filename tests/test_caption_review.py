import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import BackgroundTasks, HTTPException
from sqlmodel import Session, SQLModel, create_engine

from app.api.datasets import (
    list_captions,
    mark_item_caption_reviewed,
    start_caption_generation,
    update_item_caption,
)
from app.config import settings
from app.models import (
    AnnotationAuthor,
    AnnotationKind,
    AnnotationReviewStatus,
    DatasetCaptionSource,
    DatasetItem,
    Media,
    MediaAnnotation,
    ProcessingTask,
    TrainingDataset,
)
from app.schemas.dataset import (
    DatasetCaptionGenerateRequest,
    DatasetCaptionUpdate,
)
from app.services.caption_lint import lint_caption
from app.tasks.dataset_caption_generation import generate_dataset_captions


class CaptionLintTests(unittest.TestCase):
    def setUp(self):
        self.dataset = TrainingDataset(
            id=1,
            name="Subject",
            slug="subject",
            person_id=1,
            trigger_word="tokperson",
            class_token="person",
        )

    def _codes(self, text: str, others: list[str] | None = None) -> set[str]:
        return {
            finding.code
            for finding in lint_caption(text, self.dataset, others or [])
        }

    def test_each_lint_rule_positive_and_negative(self):
        cases = [
            ("identity-leak", "a woman with blue eyes outside", "a woman in a blue coat outside"),
            ("other-people", "a couple walking through a park", "a person walking through a park"),
            ("text-artifacts", "a visible watermark in the corner", "sunlight in the lower corner"),
            ("too-short", "standing outside", "a person standing outside"),
            ("too-long", " ".join(f"word{index}" for index in range(76)), " ".join(f"word{index}" for index in range(75))),
            ("trigger-in-caption", "tokperson standing beside a tree", "a person standing beside a tree"),
        ]
        for code, positive, negative in cases:
            with self.subTest(code=code):
                self.assertIn(code, self._codes(positive))
                self.assertNotIn(code, self._codes(negative))

        text = "a person standing beside the old stone wall"
        self.assertIn("near-duplicate", self._codes(text, [text]))
        self.assertNotIn(
            "near-duplicate",
            self._codes(text, ["a cyclist crossing a busy city street"]),
        )


class CaptionReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}
        )
        SQLModel.metadata.create_all(self.engine)
        self.previous_presentation = settings.general.presentation_mode
        self.previous_annotations = settings.annotations.enabled
        settings.general.presentation_mode = False

    def tearDown(self):
        settings.general.presentation_mode = self.previous_presentation
        settings.annotations.enabled = self.previous_annotations
        self.engine.dispose()
        self.temp.cleanup()

    def _dataset(self, session: Session, *, caption_source=DatasetCaptionSource.ANNOTATION):
        dataset = TrainingDataset(
            name="Review",
            slug=f"review-{caption_source.value}",
            trigger_word="tokperson",
            class_token="person",
            caption_source=caption_source,
        )
        session.add(dataset)
        session.commit()
        return dataset

    def _item(self, session: Session, dataset: TrainingDataset, position: int = 0):
        media = Media(
            path=str(self.root / f"{dataset.id}-{position}.jpg"),
            filename=f"{position}.jpg",
            size=1,
            width=100,
            height=100,
        )
        session.add(media)
        session.commit()
        item = DatasetItem(
            dataset_id=dataset.id, media_id=media.id, position=position
        )
        session.add(item)
        session.commit()
        session.refresh(item)
        return item, media

    def _annotation(
        self,
        session: Session,
        media: Media,
        revision: int,
        status: AnnotationReviewStatus,
        text: str,
    ):
        annotation = MediaAnnotation(
            media_id=media.id,
            revision=revision,
            kind=AnnotationKind.CAPTION,
            author=AnnotationAuthor.MACHINE,
            review_status=status,
            content={"text": text},
        )
        session.add(annotation)
        session.commit()
        return annotation

    def test_listing_source_resolution_and_filters(self):
        with Session(self.engine) as session:
            dataset = self._dataset(session)
            override_item, override_media = self._item(session, dataset, 0)
            override_item.caption_override = "an override pose outdoors"
            session.add(override_item)
            approved_item, approved_media = self._item(session, dataset, 1)
            candidate_item, candidate_media = self._item(session, dataset, 2)
            template_item, _ = self._item(session, dataset, 3)
            session.commit()
            self._annotation(session, override_media, 1, AnnotationReviewStatus.CANDIDATE, "ignored machine caption")
            self._annotation(session, approved_media, 1, AnnotationReviewStatus.APPROVED, "an approved pose outdoors")
            candidate = self._annotation(session, candidate_media, 1, AnnotationReviewStatus.CANDIDATE, "a candidate pose indoors")

            page = list_captions(dataset.id, "all", None, 100, session)
            self.assertEqual(
                [row.source for row in page.items],
                ["override", "approved", "candidate", "template"],
            )
            self.assertEqual(page.items[2].annotation_id, candidate.id)
            self.assertIn("tokperson person", page.items[1].effective_caption)
            self.assertEqual(
                [row.item_id for row in list_captions(dataset.id, "approved", None, 100, session).items],
                [approved_item.id],
            )
            self.assertEqual(
                [row.item_id for row in list_captions(dataset.id, "candidate", None, 100, session).items],
                [override_item.id, candidate_item.id],
            )
            self.assertEqual(
                [row.item_id for row in list_captions(dataset.id, "missing", None, 100, session).items],
                [template_item.id],
            )

            none_dataset = self._dataset(session, caption_source=DatasetCaptionSource.NONE)
            self._item(session, none_dataset)
            none_row = list_captions(none_dataset.id, "all", None, 100, session).items[0]
            self.assertEqual(none_row.source, "none")
            self.assertIsNone(none_row.effective_caption)

    def test_caption_patch_and_reviewed_marker(self):
        with Session(self.engine) as session:
            dataset = self._dataset(session)
            item, _ = self._item(session, dataset)
            updated = update_item_caption(
                dataset.id,
                item.id,
                DatasetCaptionUpdate(text="  a new outdoor pose  "),
                session,
            )
            self.assertEqual(updated.caption_override, "a new outdoor pose")
            self.assertIsNotNone(updated.caption_reviewed_at)
            marker = mark_item_caption_reviewed(dataset.id, item.id, session)
            self.assertEqual(marker.item_id, item.id)
            self.assertIsNotNone(marker.caption_reviewed_at)

    def test_generation_skips_approved_and_records_counts(self):
        with Session(self.engine) as session:
            dataset = self._dataset(session)
            approved_item, approved_media = self._item(session, dataset, 0)
            excluded_item, _ = self._item(session, dataset, 1)
            excluded_item.excluded = True
            success_item, _ = self._item(session, dataset, 2)
            failed_item, _ = self._item(session, dataset, 3)
            session.add(excluded_item)
            session.commit()
            self._annotation(session, approved_media, 1, AnnotationReviewStatus.APPROVED, "approved caption")
            task = ProcessingTask(task_type="dataset_caption_generation")
            session.add(task)
            session.commit()
            task_id = task.id
            dataset_id = dataset.id
            failed_media_id = failed_item.media_id

        def create_attempt(_session, *, media_id, kind):
            self.assertEqual(kind, AnnotationKind.CAPTION)
            if media_id == failed_media_id:
                raise RuntimeError("generation failed")
            return SimpleNamespace(id=f"attempt-{media_id}")

        with (
            patch("app.tasks.dataset_caption_generation.db.engine", self.engine),
            patch("app.tasks.dataset_caption_generation.create_annotation_attempt", side_effect=create_attempt),
            patch("app.tasks.dataset_caption_generation.run_annotation_attempt"),
        ):
            generate_dataset_captions(
                task_id, dataset_id=dataset_id, only_missing=True
            )

        with Session(self.engine) as session:
            task = session.get(ProcessingTask, task_id)
            self.assertEqual(
                task.result, {"generated": 1, "skipped": 2, "failed": 1}
            )
            self.assertEqual(task.processed, 4)

    def test_generation_returns_503_when_annotations_are_disabled(self):
        settings.annotations.enabled = False
        with Session(self.engine) as session:
            dataset = self._dataset(session)
            with self.assertRaises(HTTPException) as raised:
                start_caption_generation(
                    dataset.id,
                    DatasetCaptionGenerateRequest(only_missing=True),
                    BackgroundTasks(),
                    session,
                )
            self.assertEqual(raised.exception.status_code, 503)

    def test_migration_head_is_single(self):
        script = ScriptDirectory.from_config(Config("alembic.ini"))
        self.assertEqual(script.get_heads(), ["c6d7e8f9a0b1"])


if __name__ == "__main__":
    unittest.main()
