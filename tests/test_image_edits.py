import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import piexif
from fastapi import BackgroundTasks, HTTPException
from PIL import Image
from sqlmodel import Session, SQLModel, create_engine

from app.api.media import edit_media
from app.models import Media
from app.schemas.media import (
    AdjustEditOp,
    CropEditOp,
    FlipEditOp,
    MediaEditRequest,
    ResizeEditOp,
    RotateEditOp,
)
from app.services.image_edits import apply_edit_ops, write_edited


class ImageEditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.source = self.root / "photo.jpg"
        self.image = Image.new("RGB", (3, 2))
        self.image.putdata(
            [
                (255, 0, 0),
                (0, 255, 0),
                (0, 0, 255),
                (255, 255, 0),
                (255, 0, 255),
                (0, 255, 255),
            ]
        )
        self.image.save(self.source)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_rotate_uses_lossless_transpose(self) -> None:
        result = apply_edit_ops(
            self.image, [RotateEditOp(op="rotate", degrees=90)]
        )
        self.assertEqual(result.size, (2, 3))
        self.assertEqual(result.getpixel((1, 0)), (255, 0, 0))

    def test_flip_axes(self) -> None:
        horizontal = apply_edit_ops(
            self.image, [FlipEditOp(op="flip", axis="horizontal")]
        )
        vertical = apply_edit_ops(
            self.image, [FlipEditOp(op="flip", axis="vertical")]
        )
        self.assertEqual(horizontal.getpixel((0, 0)), (0, 0, 255))
        self.assertEqual(vertical.getpixel((0, 0)), (255, 255, 0))

    def test_crop_and_resize(self) -> None:
        cropped = apply_edit_ops(
            self.image,
            [CropEditOp(op="crop", x=1, y=0, width=2, height=2)],
        )
        resized = apply_edit_ops(
            self.image, [ResizeEditOp(op="resize", width=6, height=4)]
        )
        self.assertEqual(cropped.size, (2, 2))
        self.assertEqual(cropped.getpixel((0, 0)), (0, 255, 0))
        self.assertEqual(resized.size, (6, 4))

    def test_adjustment_endpoints(self) -> None:
        black = apply_edit_ops(
            self.image, [AdjustEditOp(op="adjust", brightness=-100)]
        )
        gray = Image.new("RGB", (1, 1), (100, 150, 200))
        desaturated = apply_edit_ops(
            gray, [AdjustEditOp(op="adjust", saturation=-100)]
        )
        contrast = apply_edit_ops(
            gray, [AdjustEditOp(op="adjust", contrast=100)]
        )
        self.assertEqual(black.getbbox(), None)
        r, g, b = desaturated.getpixel((0, 0))
        self.assertEqual(r, g)
        self.assertEqual(g, b)
        self.assertNotEqual(contrast.getpixel((0, 0)), gray.getpixel((0, 0)))

    def test_ops_apply_in_list_order(self) -> None:
        crop_then_rotate = apply_edit_ops(
            self.image,
            [
                CropEditOp(op="crop", x=0, y=0, width=2, height=1),
                RotateEditOp(op="rotate", degrees=90),
            ],
        )
        rotate_then_crop = apply_edit_ops(
            self.image,
            [
                RotateEditOp(op="rotate", degrees=90),
                CropEditOp(op="crop", x=0, y=0, width=2, height=1),
            ],
        )
        self.assertEqual(crop_then_rotate.size, (1, 2))
        self.assertEqual(rotate_then_crop.size, (2, 1))

    def test_copy_naming_increments(self) -> None:
        first = write_edited(
            self.source, self.image, "copy", media_roots=[(self.root, False)]
        )
        second = write_edited(
            self.source, self.image, "copy", media_roots=[(self.root, False)]
        )
        self.assertEqual(first.name, "photo_edited.jpg")
        self.assertEqual(second.name, "photo_edited-2.jpg")

    def test_overwrite_is_atomic_and_removes_temp_file(self) -> None:
        replacement = Image.new("RGB", (5, 4), "purple")
        with patch("app.services.image_edits.os.replace", wraps=__import__("os").replace) as replace:
            result = write_edited(
                self.source,
                replacement,
                "overwrite",
                media_roots=[(self.root, False)],
            )
        self.assertEqual(result, self.source)
        with Image.open(self.source) as saved_image:
            self.assertEqual(saved_image.size, (5, 4))
        self.assertEqual(replace.call_count, 1)
        self.assertEqual(list(self.root.glob(".photo-edit-*")), [])

    def test_jpeg_exif_is_preserved_and_rotation_tag_removed(self) -> None:
        exif = {
            "0th": {
                piexif.ImageIFD.Make: b"Omoide Camera",
                piexif.ImageIFD.Orientation: 6,
            },
            "Exif": {},
            "GPS": {},
            "1st": {},
            "thumbnail": None,
        }
        self.image.save(self.source, exif=piexif.dump(exif))
        target = write_edited(
            self.source,
            self.image,
            "copy",
            media_roots=[(self.root, False)],
            rotated=True,
        )
        saved = piexif.load(str(target))
        self.assertEqual(saved["0th"][piexif.ImageIFD.Make], b"Omoide Camera")
        self.assertNotIn(piexif.ImageIFD.Orientation, saved["0th"])

    def test_video_is_rejected_by_api(self) -> None:
        engine = create_engine("sqlite://")
        SQLModel.metadata.create_all(engine, tables=[Media.__table__])
        with Session(engine) as session:
            video = Media(
                path=str(self.root / "clip.mp4"),
                filename="clip.mp4",
                size=10,
                duration=1.0,
            )
            session.add(video)
            session.commit()
            session.refresh(video)
            with self.assertRaises(HTTPException) as raised:
                edit_media(
                    video.id,
                    MediaEditRequest(
                        ops=[RotateEditOp(op="rotate", degrees=90)]
                    ),
                    BackgroundTasks(),
                    session,
                )
        engine.dispose()
        self.assertEqual(raised.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
