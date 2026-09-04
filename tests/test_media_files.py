import errno
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services.media_files import (
    InvalidMediaPathError,
    MediaFileCollisionError,
    ReadOnlyMediaRootError,
    move_media_file,
    rename_media_file,
)


class MediaFileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name) / "media"
        self.root.mkdir()
        self.source_dir = self.root / "source"
        self.destination_dir = self.root / "destination"
        self.source_dir.mkdir()
        self.destination_dir.mkdir()
        self.source = self.source_dir / "photo.jpg"
        self.source.write_bytes(b"photo contents")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_move_media_file(self) -> None:
        target = move_media_file(
            self.source,
            "destination",
            [(self.root, False)],
        )
        self.assertEqual(target, self.destination_dir / "photo.jpg")
        self.assertEqual(target.read_bytes(), b"photo contents")
        self.assertFalse(self.source.exists())

    def test_rename_keeps_extension_when_new_name_has_none(self) -> None:
        target = rename_media_file(
            self.source,
            "summer",
            [(self.root, False)],
        )
        self.assertEqual(target.name, "summer.jpg")
        self.assertTrue(target.exists())

    def test_destination_outside_configured_roots_is_refused(self) -> None:
        outside = Path(self.temp_dir.name) / "outside"
        outside.mkdir()
        with self.assertRaises(InvalidMediaPathError):
            move_media_file(self.source, os.fspath(outside), [(self.root, False)])

    def test_read_only_source_root_is_refused(self) -> None:
        with self.assertRaises(ReadOnlyMediaRootError):
            rename_media_file(self.source, "renamed.jpg", [(self.root, True)])

    def test_collision_is_refused(self) -> None:
        (self.destination_dir / self.source.name).write_bytes(b"existing")
        with self.assertRaises(MediaFileCollisionError):
            move_media_file(
                self.source,
                "destination",
                [(self.root, False)],
            )

    def test_cross_device_fallback_copies_verifies_and_unlinks(self) -> None:
        target = self.destination_dir / self.source.name
        real_replace = os.replace

        def raise_exdev(source: Path, destination: Path) -> None:
            if Path(source) == self.source and Path(destination) == target:
                raise OSError(errno.EXDEV, "Cross-device link")
            real_replace(source, destination)

        with patch("app.services.media_files.os.replace", side_effect=raise_exdev):
            result = move_media_file(
                self.source,
                "destination",
                [(self.root, False)],
            )

        self.assertEqual(result, target)
        self.assertEqual(target.read_bytes(), b"photo contents")
        self.assertFalse(self.source.exists())


if __name__ == "__main__":
    unittest.main()
