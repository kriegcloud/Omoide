import importlib
import os
import tempfile
import unittest
from pathlib import Path


_CONFIG_HOME = tempfile.TemporaryDirectory()
os.environ["XDG_CONFIG_HOME"] = _CONFIG_HOME.name

importlib.import_module("app.api.media")
from app.tasks.scan import _walk_media_candidates  # noqa: E402


class MediaCandidateTraversalTests(unittest.TestCase):
    def test_symlinked_directories_loops_and_files_are_not_followed(self) -> None:
        with (
            tempfile.TemporaryDirectory() as root_temp,
            tempfile.TemporaryDirectory() as outside_temp,
        ):
            root = Path(root_temp)
            outside = Path(outside_temp)
            nested = root / "nested"
            nested.mkdir()
            (root / ".omoide").mkdir()

            included = root / "root.jpg"
            nested_included = nested / "nested.png"
            outside_media = outside / "outside.jpg"
            hidden_internal = root / ".omoide" / "hidden.jpg"
            for path in (
                included,
                nested_included,
                outside_media,
                hidden_internal,
            ):
                path.touch()

            try:
                (root / "linked-tree").symlink_to(
                    outside,
                    target_is_directory=True,
                )
                (nested / "loop").symlink_to(root, target_is_directory=True)
                (root / "linked-file.jpg").symlink_to(outside_media)
            except OSError as exc:
                self.skipTest(f"Symlinks are unavailable: {exc}")

            candidates = set(
                _walk_media_candidates(
                    [root],
                    frozenset({".jpg", ".png"}),
                    skip_thumbnails=False,
                )
            )

        self.assertEqual(candidates, {included, nested_included})


if __name__ == "__main__":
    unittest.main()
