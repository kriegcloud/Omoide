import importlib
import os
import tempfile
import unittest
from pathlib import Path


_CONFIG_HOME = tempfile.TemporaryDirectory()
os.environ["XDG_CONFIG_HOME"] = _CONFIG_HOME.name
os.environ.pop("IS_DOCKER", None)

importlib.import_module("app.api.media")
from pydantic import ValidationError  # noqa: E402

from app import config  # noqa: E402
from app.scan_scope import (  # noqa: E402
    ScanScope,
    folder_status,
    list_scope_folders,
    normalize_scope_folder,
    normalize_scope_pattern,
)
from app.tasks.scan import _walk_media_candidates  # noqa: E402

SUFFIXES = frozenset({".jpg"})


def _tree(root: Path, *files: str) -> None:
    for rel in files:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()


def _walk(root: Path, scope: ScanScope | None, errors: list | None = None) -> set[str]:
    return {
        p.relative_to(root).as_posix()
        for p in _walk_media_candidates(
            [root],
            SUFFIXES,
            skip_thumbnails=False,
            scope=scope,
            on_error=(errors.append if errors is not None else None),
        )
    }


class ScopeNormalizationTests(unittest.TestCase):
    def test_folder_paths_are_normalized_to_relative_posix(self) -> None:
        self.assertEqual(normalize_scope_folder("  /T7\\Photos//2024/ "), "T7/Photos/2024")

    def test_folder_paths_reject_traversal_and_empty(self) -> None:
        for bad in ["", "   ", "/", "../etc", "T7/../x", "./T7"]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                normalize_scope_folder(bad)

    def test_patterns_are_names_not_paths(self) -> None:
        self.assertEqual(normalize_scope_pattern("  @eaDir "), "@eaDir")
        for bad in ["", "a/b", "a\\b", "."]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                normalize_scope_pattern(bad)

    def test_settings_validate_and_dedupe_scope_lists(self) -> None:
        scan = config.ScanSettings(
            include_folders=["T7/Photos", "/T7/Photos/", "T7XFER"],
            exclude_folders=["T7/Photos/raw"],
            exclude_folder_patterns=["@eaDir", "@EADIR", "*.photoslibrary"],
        )
        self.assertEqual(scan.include_folders, ["T7/Photos", "T7XFER"])
        self.assertEqual(scan.exclude_folders, ["T7/Photos/raw"])
        self.assertEqual(scan.exclude_folder_patterns, ["@eaDir", "*.photoslibrary"])

    def test_settings_reject_traversal(self) -> None:
        with self.assertRaises(ValidationError):
            config.ScanSettings(exclude_folders=["../outside"])

    def test_scope_defaults_to_whole_library(self) -> None:
        scan = config.ScanSettings()
        self.assertEqual(
            (scan.include_folders, scan.exclude_folders, scan.exclude_folder_patterns),
            ([], [], []),
        )


class ScopeWalkTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        _tree(
            self.root,
            "top.jpg",
            "A/a.jpg",
            "A/raw/r.jpg",
            "A/sub/@eaDir/thumb.jpg",
            "A/sub/s.jpg",
            "B/b.jpg",
            "B/Photos.photoslibrary/p.jpg",
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_no_scope_walks_everything(self) -> None:
        self.assertEqual(len(_walk(self.root, None)), 7)
        self.assertEqual(len(_walk(self.root, ScanScope())), 7)

    def test_include_restricts_walk_to_listed_folders(self) -> None:
        self.assertEqual(
            _walk(self.root, ScanScope(include=("B",))),
            {"B/b.jpg", "B/Photos.photoslibrary/p.jpg"},
        )

    def test_exclude_prunes_subtree_and_wins_over_include(self) -> None:
        got = _walk(self.root, ScanScope(include=("A",), exclude=("A/raw", "A/sub")))
        self.assertEqual(got, {"A/a.jpg"})
        self.assertEqual(_walk(self.root, ScanScope(include=("A",), exclude=("A",))), set())

    def test_patterns_match_folder_names_case_insensitively_at_any_depth(self) -> None:
        got = _walk(self.root, ScanScope(patterns=("@EADIR", "*.photoslibrary")))
        self.assertNotIn("A/sub/@eaDir/thumb.jpg", got)
        self.assertNotIn("B/Photos.photoslibrary/p.jpg", got)
        self.assertIn("A/sub/s.jpg", got)

    def test_nested_includes_do_not_yield_twice(self) -> None:
        found = list(
            _walk_media_candidates(
                [self.root],
                SUFFIXES,
                skip_thumbnails=False,
                scope=ScanScope(include=("A", "A/sub")),
            )
        )
        self.assertEqual(len(found), len(set(found)))
        self.assertEqual(len(found), 4)

    def test_missing_include_is_reported_not_silently_empty(self) -> None:
        errors: list = []
        self.assertEqual(_walk(self.root, ScanScope(include=("Gone",)), errors), set())
        self.assertEqual(len(errors), 1)

    def test_include_through_symlink_is_not_followed(self) -> None:
        with tempfile.TemporaryDirectory() as outside_tmp:
            outside = Path(outside_tmp)
            _tree(outside, "o.jpg")
            try:
                (self.root / "link").symlink_to(outside, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"Symlinks are unavailable: {exc}")
            self.assertEqual(_walk(self.root, ScanScope(include=("link",))), set())


class FolderStatusTests(unittest.TestCase):
    scope = ScanScope(include=("A/sub", "B"), exclude=("B/raw",), patterns=("@eaDir",))

    def test_statuses(self) -> None:
        cases = {
            "A": "partial",
            "A/sub": "scanned",
            "A/sub/deep": "scanned",
            "A/other": "outside",
            "B/raw": "excluded",
            "B/raw/x": "excluded",
            "B/@eaDir": "excluded",
            "C": "outside",
            "C/.omoide": "excluded",
        }
        for rel, expected in cases.items():
            with self.subTest(rel=rel):
                self.assertEqual(folder_status(rel, self.scope), expected)

    def test_everything_scanned_without_includes(self) -> None:
        self.assertEqual(folder_status("Anything/at/all", ScanScope()), "scanned")

    def test_parent_of_include_inside_exclude_is_not_partial(self) -> None:
        scope = ScanScope(include=("A/sub",), exclude=("A/sub",))
        self.assertEqual(folder_status("A", scope), "outside")


class ListScopeFoldersTests(unittest.TestCase):
    def test_lists_real_subfolders_with_status_and_skips_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _tree(root, "A/x/f.jpg", "B/f.jpg", "f.jpg")
            (root / ".omoide").mkdir()
            try:
                (root / "link").symlink_to(root / "A", target_is_directory=True)
            except OSError:
                pass
            listing = list_scope_folders([root], "", ScanScope(exclude=("B",)))
            by_name = {f["name"]: f for f in listing}
            self.assertEqual(sorted(by_name), ["A", "B"])
            self.assertEqual(by_name["A"]["status"], "scanned")
            self.assertTrue(by_name["A"]["has_subfolders"])
            self.assertEqual(by_name["B"]["status"], "excluded")
            self.assertFalse(by_name["B"]["has_subfolders"])
            nested = list_scope_folders([root], "A", ScanScope())
            self.assertEqual([f["path"] for f in nested], ["A/x"])

    def test_rejects_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(ValueError):
            list_scope_folders([Path(tmp)], "../", ScanScope())


class ScanFoldersEndpointTests(unittest.TestCase):
    def test_endpoint_previews_draft_scope_and_rejects_traversal(self) -> None:
        from unittest.mock import patch

        from fastapi import HTTPException

        config_api = importlib.import_module("app.api.config")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _tree(root, "A/f.jpg", "B/f.jpg")
            with patch.object(
                type(config_api.settings.general),
                "resolved_media_dirs",
                return_value=[(root, False)],
            ):
                listing = config_api.list_scan_folders(
                    parent="", include=["A"], exclude=[], pattern=[]
                )
                self.assertEqual(
                    {f.name: f.status for f in listing.folders},
                    {"A": "scanned", "B": "outside"},
                )
                with self.assertRaises(HTTPException) as ctx:
                    config_api.list_scan_folders(
                        parent="..", include=[], exclude=[], pattern=[]
                    )
                self.assertEqual(ctx.exception.status_code, 400)
                with self.assertRaises(HTTPException):
                    config_api.list_scan_folders(
                        parent="", include=[], exclude=[], pattern=["a/b"]
                    )


if __name__ == "__main__":
    unittest.main()
