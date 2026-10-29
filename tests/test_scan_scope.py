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
    FolderIndexCache,
    ScanScope,
    build_folder_index,
    folder_status,
    list_scope_folders,
    normalize_scope_folder,
    normalize_scope_pattern,
    search_folder_index,
    search_scope_folders,
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


class SearchScopeFoldersTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        for rel in [
            "Holiday",
            "Holidays 2024",
            "Archive/Old Holiday Trip",
            "Archive/holiday",
            "Archive/raw/Holiday extras",
            "Archive/@eaDir/holiday thumbs",
            ".omoide/holiday",
            ".Trash-1000/holiday",
            "Work/Projects/Alpha",
        ]:
            (self.root / rel).mkdir(parents=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _search(self, query: str, scope: ScanScope | None = None, **kwargs) -> dict:
        return search_scope_folders([self.root], query, scope or ScanScope(), **kwargs)

    def test_ranks_exact_then_prefix_then_substring_then_depth_then_name(self) -> None:
        result = self._search("holiday")
        self.assertEqual(
            [f["path"] for f in result["folders"]],
            [
                "Holiday",
                "Archive/holiday",
                "Holidays 2024",
                "Archive/raw/Holiday extras",
                "Archive/@eaDir/holiday thumbs",
                "Archive/Old Holiday Trip",
            ],
        )
        self.assertFalse(result["truncated"])

    def test_path_style_query_matches_relative_path(self) -> None:
        result = self._search("projects/al")
        self.assertEqual([f["path"] for f in result["folders"]], ["Work/Projects/Alpha"])
        self.assertEqual(
            [f["path"] for f in self._search("/Work/Projects/")["folders"]],
            ["Work/Projects", "Work/Projects/Alpha"],
        )

    def test_excluded_folders_are_still_found_with_status(self) -> None:
        scope = ScanScope(exclude=("Archive/raw",), patterns=("@eaDir",))
        by_path = {f["path"]: f for f in self._search("holiday", scope)["folders"]}
        self.assertEqual(by_path["Archive/raw/Holiday extras"]["status"], "excluded")
        self.assertEqual(by_path["Archive/@eaDir/holiday thumbs"]["status"], "excluded")
        self.assertEqual(by_path["Holiday"]["status"], "scanned")

    def test_internal_dirs_are_skipped(self) -> None:
        paths = [f["path"] for f in self._search("holiday")["folders"]]
        self.assertFalse(any(p.startswith((".omoide", ".Trash")) for p in paths))

    def test_symlinks_are_not_followed(self) -> None:
        with tempfile.TemporaryDirectory() as outside_tmp:
            (Path(outside_tmp) / "Holiday outside").mkdir()
            try:
                (self.root / "linked").symlink_to(outside_tmp, target_is_directory=True)
                (self.root / "Holiday link").symlink_to(self.root / "Work", target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"Symlinks are unavailable: {exc}")
            paths = [f["path"] for f in self._search("holiday")["folders"]]
            self.assertNotIn("linked/Holiday outside", paths)
            self.assertNotIn("Holiday link", paths)
            self.assertEqual(self._search("linked")["folders"], [])

    def test_limit_truncates_and_reports(self) -> None:
        result = self._search("holiday", limit=2)
        self.assertEqual([f["path"] for f in result["folders"]], ["Holiday", "Holidays 2024"])
        self.assertTrue(result["truncated"])
        exact = self._search("holiday", limit=6)
        self.assertEqual(len(exact["folders"]), 6)
        self.assertFalse(exact["truncated"])

    def test_max_dirs_and_time_budget_truncate_and_report(self) -> None:
        shallow = self._search("holiday", max_dirs=1)
        self.assertTrue(shallow["truncated"])
        self.assertEqual(
            [f["path"] for f in shallow["folders"]], ["Holiday", "Holidays 2024"]
        )
        self.assertTrue(self._search("holiday", time_budget_s=0)["truncated"])

    def test_trail_lists_ancestors_with_draft_statuses(self) -> None:
        scope = ScanScope(include=("Archive/raw",))
        match = next(
            f
            for f in self._search("extras", scope)["folders"]
            if f["path"] == "Archive/raw/Holiday extras"
        )
        self.assertEqual(
            match["trail"],
            [
                {"name": "Archive", "path": "Archive", "status": "partial"},
                {"name": "raw", "path": "Archive/raw", "status": "scanned"},
            ],
        )
        self.assertEqual(match["status"], "scanned")
        self.assertEqual(match["name"], "Holiday extras")
        self.assertFalse(match["has_subfolders"])
        top = self._search("work")["folders"][0]
        self.assertEqual((top["trail"], top["has_subfolders"]), ([], True))

    def test_identical_paths_merge_across_roots(self) -> None:
        with tempfile.TemporaryDirectory() as other_tmp:
            other = Path(other_tmp)
            (other / "Holiday" / "Day 1").mkdir(parents=True)
            result = search_scope_folders([self.root, other], "holiday", ScanScope())
            paths = [f["path"] for f in result["folders"]]
            self.assertEqual(paths.count("Holiday"), 1)
            self.assertTrue(result["folders"][0]["has_subfolders"])

    def test_index_search_matches_live_walk(self) -> None:
        index = build_folder_index([self.root])
        self.assertTrue(index.complete)
        scope = ScanScope(exclude=("Archive/raw",))
        for query in ["holiday", "HOLIDAY", "projects/al", "/Work/", "work", "zz"]:
            with self.subTest(query=query):
                self.assertEqual(
                    search_folder_index(index, query, scope),
                    self._search(query, scope),
                )
        self.assertEqual(
            search_folder_index(index, "holiday", scope, limit=2),
            self._search("holiday", scope, limit=2),
        )

    def test_incomplete_index_reports_truncated(self) -> None:
        index = build_folder_index([self.root], max_dirs=1)
        self.assertFalse(index.complete)
        result = search_folder_index(index, "holiday", ScanScope())
        self.assertEqual(
            [f["path"] for f in result["folders"]], ["Holiday", "Holidays 2024"]
        )
        self.assertTrue(result["truncated"])

    def test_index_cache_serves_stale_index_until_rebuilt(self) -> None:
        cache = FolderIndexCache(ttl_s=0, background=False)
        self.assertIsNone(cache.lookup([self.root], prime=False))
        self.assertIsNone(cache.lookup([self.root]))  # builds synchronously
        first = cache.lookup([self.root], prime=False)
        self.assertIsNotNone(first)
        (self.root / "Holiday new").mkdir()
        self.assertIs(cache.lookup([self.root]), first)  # stale: answers, then rebuilds
        rebuilt = cache.lookup([self.root], prime=False)
        self.assertIsNot(rebuilt, first)
        self.assertIn(("Holiday new",), rebuilt.folders)

    def test_short_query_returns_nothing(self) -> None:
        for query in ["", " ", "h", " h ", "/"]:
            with self.subTest(query=query):
                self.assertEqual(
                    self._search(query), {"folders": [], "truncated": False}
                )


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


class ScanFolderSearchEndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        from unittest.mock import patch

        self.config_api = importlib.import_module("app.api.config")
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        for i in range(120):
            (root / "Trips" / f"trip {i:03d}").mkdir(parents=True)
        (root / "Raw").mkdir()
        self.cache = FolderIndexCache(background=False)
        self._patches = [
            patch.object(
                type(self.config_api.settings.general),
                "resolved_media_dirs",
                return_value=[(root, False)],
            ),
            patch.object(self.config_api, "folder_index_cache", self.cache),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self) -> None:
        for p in reversed(self._patches):
            p.stop()
        self._tmp.cleanup()

    def _call(self, q: str, **kwargs):
        params = {"limit": 25, "include": [], "exclude": [], "pattern": []}
        params.update(kwargs)
        return self.config_api.search_scan_folders(q=q, **params)

    def test_search_uses_draft_scope_and_returns_trail(self) -> None:
        result = self._call("trip 007", exclude=["Trips"])
        self.assertEqual([f.path for f in result.folders], ["Trips/trip 007"])
        self.assertEqual(result.folders[0].status, "excluded")
        self.assertEqual(
            [(c.name, c.path, c.status) for c in result.folders[0].trail],
            [("Trips", "Trips", "excluded")],
        )
        self.assertFalse(result.truncated)

    def test_falls_back_to_live_walk_until_the_index_is_built(self) -> None:
        live = self._call("trip 01")
        self.assertFalse(live.indexed)
        indexed = self._call("trip 01")
        self.assertTrue(indexed.indexed)
        self.assertEqual(
            [f.path for f in indexed.folders], [f.path for f in live.folders]
        )
        self.assertEqual(len(indexed.folders), 10)

    def test_listing_the_top_level_primes_the_index(self) -> None:
        self.config_api.list_scan_folders(parent="", include=[], exclude=[], pattern=[])
        self.assertTrue(self._call("raw").indexed)

    def test_limit_is_capped_and_truncation_reported(self) -> None:
        result = self._call("trip ", limit=500)
        self.assertEqual(len(result.folders), 100)
        self.assertTrue(result.truncated)

    def test_short_query_returns_empty(self) -> None:
        result = self._call("r")
        self.assertEqual((result.folders, result.truncated), ([], False))

    def test_invalid_draft_scope_is_400(self) -> None:
        from fastapi import HTTPException

        for kwargs in [{"exclude": [".."]}, {"include": ["a/../b"]}, {"pattern": ["a/b"]}]:
            with self.subTest(kwargs=kwargs), self.assertRaises(HTTPException) as ctx:
                self._call("raw", **kwargs)
            self.assertEqual(ctx.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
