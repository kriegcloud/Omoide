import json
import tempfile
import unittest
from pathlib import Path

import yaml
from PIL import Image
from sqlmodel import Session, SQLModel, create_engine, select

from app.api.datasets import add_items, create_from_person
from app.config import settings
from app.models import (
    AnnotationAuthor,
    AnnotationKind,
    AnnotationReviewStatus,
    DatasetCaptionSource,
    DatasetExport,
    DatasetExportLayout,
    DatasetItem,
    Face,
    Media,
    MediaAnnotation,
    Person,
    PersonMediaLink,
    ProcessingTask,
    TrainingDataset,
)
from app.schemas.dataset import DatasetItemsRequest
from app.services.datasets import build_export, pick_bucket, resolve_caption, slugify


def media(path: Path, *, duration=None) -> Media:
    return Media(
        path=str(path),
        filename=path.name,
        size=path.stat().st_size if path.exists() else 1,
        width=1200,
        height=800,
        duration=duration,
    )


class DatasetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
        SQLModel.metadata.create_all(self.engine)
        self.previous_datasets_dir = settings.general.datasets_dir
        settings.general.datasets_dir = self.root / "exports"

    def tearDown(self):
        settings.general.datasets_dir = self.previous_datasets_dir
        self.temp.cleanup()

    def test_slugify(self):
        self.assertEqual(slugify("  Jane Dœ — Portraits! "), "jane-d-portraits")
        self.assertEqual(slugify("Already---slugged"), "already-slugged")

    def test_pick_bucket_never_upscales(self):
        self.assertEqual(pick_bucket(1600, 900, [512, 768, 1024]), 1024)
        self.assertEqual(pick_bucket(700, 400, [512, 768, 1024]), 512)
        self.assertEqual(pick_bucket(400, 300, [512, 768, 1024]), 400)

    def test_resolve_caption_precedence_and_name_substitution(self):
        with Session(self.engine) as session:
            source = self.root / "caption.jpg"
            Image.new("RGB", (32, 32)).save(source)
            person_row = Person(name="Jane Doe", appearance_count=1)
            media_row = media(source)
            session.add(person_row)
            session.add(media_row)
            session.commit()
            dataset = TrainingDataset(
                name="Jane", slug="jane", person_id=person_row.id,
                trigger_word="jdx", class_token="woman",
                caption_source=DatasetCaptionSource.ANNOTATION,
            )
            session.add(dataset)
            session.commit()
            item = DatasetItem(dataset_id=dataset.id, media_id=media_row.id)
            session.add(item)
            session.add(MediaAnnotation(
                media_id=media_row.id, revision=1, kind=AnnotationKind.CAPTION,
                author=AnnotationAuthor.MACHINE, review_status=AnnotationReviewStatus.APPROVED,
                schema_version="omoide.annotation/v1", content={"caption": "Jane Doe beside jane-doe"},
            ))
            session.add(MediaAnnotation(
                media_id=media_row.id, revision=2, kind=AnnotationKind.CAPTION,
                author=AnnotationAuthor.MACHINE, review_status=AnnotationReviewStatus.CANDIDATE,
                schema_version="omoide.annotation/v1", content={"caption": "newer candidate"},
            ))
            session.commit()
            self.assertEqual(resolve_caption(dataset, item, media_row, person_row, session), "jdx woman, jdx beside jdx")
            item.caption_override = "Custom Jane Doe"
            self.assertEqual(resolve_caption(dataset, item, media_row, person_row, session), "jdx woman, Custom jdx")

    def test_add_items_dedupes_and_skips_video(self):
        with Session(self.engine) as session:
            image_path = self.root / "image.jpg"
            video_path = self.root / "video.mp4"
            Image.new("RGB", (20, 20)).save(image_path)
            video_path.write_bytes(b"video")
            dataset = TrainingDataset(name="Set", slug="set", trigger_word="set", class_token="person")
            still = media(image_path)
            video = media(video_path, duration=2.0)
            session.add(dataset)
            session.add(still)
            session.add(video)
            session.commit()
            first = add_items(dataset.id, DatasetItemsRequest(media_ids=[still.id, still.id, video.id]), session)
            second = add_items(dataset.id, DatasetItemsRequest(media_ids=[still.id]), session)
            self.assertEqual(first.added_ids, [still.id])
            self.assertEqual(first.skipped_ids, [video.id])
            self.assertEqual(second.skipped_ids, [still.id])

    def test_from_person_combines_faces_and_manual_links(self):
        with Session(self.engine) as session:
            paths = [self.root / f"person-{index}.jpg" for index in range(2)]
            for path in paths:
                Image.new("RGB", (20, 20)).save(path)
            person_row = Person(name="Ada Example", gender="female", appearance_count=2)
            first, second = media(paths[0]), media(paths[1])
            session.add(person_row)
            session.add(first)
            session.add(second)
            session.commit()
            session.add(Face(media_id=first.id, person_id=person_row.id, bbox=[0, 0, 10, 10]))
            session.add(PersonMediaLink(person_id=person_row.id, media_id=second.id))
            session.commit()
            result = create_from_person(person_row.id, session)
            self.assertEqual(result.item_count, 2)
            self.assertEqual(result.trigger_word, "ada-example")
            self.assertEqual(result.class_token, "woman")

    def test_export_smoke_all_layouts(self):
        with Session(self.engine) as session:
            sources = [self.root / f"source-{index}.jpg" for index in range(2)]
            for index, source in enumerate(sources):
                Image.new("RGB", (1300, 900), (30 + index, 80, 120)).save(source)
            media_rows = [media(source) for source in sources]
            session.add_all(media_rows)
            session.commit()
            for layout in DatasetExportLayout:
                dataset = TrainingDataset(
                    name=layout.value, slug=layout.value, trigger_word="subjectx",
                    class_token="person", caption_source=DatasetCaptionSource.TEMPLATE,
                    export_layout=layout,
                )
                session.add(dataset)
                session.commit()
                for position, media_row in enumerate(media_rows):
                    session.add(DatasetItem(dataset_id=dataset.id, media_id=media_row.id, position=position))
                export = DatasetExport(dataset_id=dataset.id, layout=layout)
                task = ProcessingTask(task_type="export_dataset")
                session.add(export)
                session.add(task)
                session.commit()
                manifest = build_export(session, export.id, task.id)
                session.refresh(export)
                output = Path(export.output_dir)
                self.assertTrue((output / "manifest.json").is_file())
                self.assertEqual(len(manifest["items"]), 2)
                for entry in manifest["items"]:
                    exported = output / entry["output_file"]
                    self.assertTrue(exported.is_file())
                    self.assertTrue(exported.with_suffix(".txt").is_file())
                    self.assertEqual(len(entry["source_sha256"]), 64)
                    self.assertEqual(len(entry["output_sha256"]), 64)
                disk_manifest = json.loads((output / "manifest.json").read_text())
                self.assertEqual(disk_manifest["items"][0]["output_sha256"], manifest["items"][0]["output_sha256"])
                if layout == DatasetExportLayout.AI_TOOLKIT:
                    config_path = output / "config.yaml"
                    self.assertTrue(config_path.is_file())
                    config = yaml.safe_load(config_path.read_text())
                    self.assertEqual(config["config"]["process"][0]["trigger_word"], "subjectx")


if __name__ == "__main__":
    unittest.main()
