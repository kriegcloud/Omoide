import importlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


_CONFIG_HOME = tempfile.TemporaryDirectory()
os.environ["XDG_CONFIG_HOME"] = _CONFIG_HOME.name

from sqlmodel import Session, SQLModel, create_engine  # noqa: E402

from app.models import Media  # noqa: E402

media_api = importlib.import_module("app.api.media")


class MediaFolderPathTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        SQLModel.metadata.create_all(self.engine, tables=[Media.__table__])
        self.session = Session(self.engine)

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def add_media(self, *paths: str) -> list[Media]:
        items = [
            Media(
                path=path,
                filename=Path(path).name,
                size=100 + index,
            )
            for index, path in enumerate(paths)
        ]
        self.session.add_all(items)
        self.session.commit()
        for item in items:
            self.session.refresh(item)
        return items

    def list_media(self, folder: str, *, recursive: bool):
        return media_api.list_media(
            tags=None,
            person_id=None,
            folder=folder,
            recursive=recursive,
            camera_make=None,
            camera_model=None,
            sort="latest",
            cursor=None,
            limit=100,
            session=self.session,
        )

    def patch_roots(self, *roots: str):
        return patch.object(
            type(media_api.settings.general),
            "resolved_media_dirs",
            return_value=[(Path(root), True) for root in roots],
        )

    def test_mount_prefix_is_hidden_and_sibling_filter_is_exact(self) -> None:
        root_file, nested_file, sibling_file, stale_file = self.add_media(
            "/app/media/t7/root.jpg",
            "/app/media/t7/DCIM/nested.jpg",
            "/app/media/t7xfer/archive/sibling.jpg",
            "/unmounted/private/stale.jpg",
        )

        with self.patch_roots("/app/media"):
            root_listing = media_api.list_media_folders(
                parent=None,
                preview_limit=0,
                session=self.session,
            )
            t7_listing = media_api.list_media_folders(
                parent="t7",
                preview_limit=0,
                session=self.session,
            )
            t7_direct = self.list_media("t7", recursive=False)
            t7_recursive = self.list_media("t7", recursive=True)
            t7xfer_recursive = self.list_media("t7xfer", recursive=True)

        self.assertEqual(
            [folder.name for folder in root_listing.folders],
            ["t7", "t7xfer"],
        )
        self.assertNotIn("app", [folder.name for folder in root_listing.folders])
        self.assertEqual(root_listing.direct_media_count, 0)

        self.assertEqual(t7_listing.current_path, "t7")
        self.assertEqual(t7_listing.direct_media_count, 1)
        self.assertEqual(
            [(folder.name, folder.media_count) for folder in t7_listing.folders],
            [("DCIM", 1)],
        )
        self.assertEqual([item.id for item in t7_direct.items], [root_file.id])
        self.assertEqual(
            {item.id for item in t7_recursive.items},
            {root_file.id, nested_file.id},
        )
        self.assertEqual(
            [item.id for item in t7xfer_recursive.items],
            [sibling_file.id],
        )
        self.assertNotIn(stale_file.id, {item.id for item in t7_recursive.items})

    def test_every_configured_root_is_normalized_before_filtering(self) -> None:
        alpha, beta, stale = self.add_media(
            "/first/library/alpha/a.jpg",
            "/second/library/beta/b.jpg",
            "/third/unmounted/gamma/c.jpg",
        )

        with self.patch_roots("/first/library", "/second/library"):
            listing = media_api.list_media_folders(
                parent=None,
                preview_limit=0,
                session=self.session,
            )
            alpha_page = self.list_media("alpha", recursive=True)
            beta_page = self.list_media("beta", recursive=True)

        self.assertEqual(
            [folder.name for folder in listing.folders],
            ["alpha", "beta"],
        )
        self.assertEqual([item.id for item in alpha_page.items], [alpha.id])
        self.assertEqual([item.id for item in beta_page.items], [beta.id])
        self.assertNotIn(
            stale.id,
            {item.id for item in [*alpha_page.items, *beta_page.items]},
        )

    def test_folder_filter_treats_sql_wildcards_as_plain_text(self) -> None:
        literal, similar = self.add_media(
            "/app/media/under_score/literal.jpg",
            "/app/media/underXscore/similar.jpg",
        )

        with self.patch_roots("/app/media"):
            page = self.list_media("under_score", recursive=True)

        self.assertEqual([item.id for item in page.items], [literal.id])
        self.assertNotEqual(page.items[0].id, similar.id)


if __name__ == "__main__":
    unittest.main()
