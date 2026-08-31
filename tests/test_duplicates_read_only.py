import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch


_CONFIG_HOME = tempfile.TemporaryDirectory()
os.environ["XDG_CONFIG_HOME"] = _CONFIG_HOME.name

from fastapi import HTTPException  # noqa: E402
from sqlmodel import Session, SQLModel, create_engine, delete, select  # noqa: E402

from app import utils  # noqa: E402
from app.api.duplicates import resolve_duplicate_group  # noqa: E402
from app.models import DuplicateGroup, DuplicateMedia, Media  # noqa: E402
from app.schemas.duplicates import ResolveDuplicatesRequest  # noqa: E402


class ResolveDuplicateGroupReadOnlyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        SQLModel.metadata.create_all(
            self.engine,
            tables=[
                Media.__table__,
                DuplicateGroup.__table__,
                DuplicateMedia.__table__,
            ],
        )
        self.session = Session(self.engine)

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def add_group(self, count: int) -> tuple[int, list[int]]:
        group = DuplicateGroup()
        media = [
            Media(
                path=f"/app/media/photo-{index}.jpg",
                filename=f"photo-{index}.jpg",
                size=100 + index,
            )
            for index in range(count)
        ]
        self.session.add(group)
        self.session.add_all(media)
        self.session.commit()
        self.session.refresh(group)
        for item in media:
            self.session.refresh(item)
            self.session.add(
                DuplicateMedia(group_id=group.id, media_id=item.id)
            )
        self.session.commit()
        return group.id, [item.id for item in media]

    def remove_media_record(self, media_id: int) -> None:
        self.session.exec(
            delete(DuplicateMedia).where(DuplicateMedia.media_id == media_id)
        )
        self.session.exec(delete(Media).where(Media.id == media_id))
        self.session.commit()

    def test_read_only_skip_retains_unresolved_group_and_links(self) -> None:
        group_id, media_ids = self.add_group(3)
        master_id, deleted_id, read_only_id = media_ids

        def delete_file(_session: Session, media_id: int) -> None:
            if media_id == read_only_id:
                raise HTTPException(
                    status_code=403,
                    detail="Media directory is read-only.",
                )
            self.remove_media_record(media_id)

        request = ResolveDuplicatesRequest(
            group_id=group_id,
            master_media_id=master_id,
            action="DELETE_FILES",
        )
        with patch("app.api.duplicates.delete_file", side_effect=delete_file):
            with self.assertRaises(HTTPException) as raised:
                resolve_duplicate_group(request, self.session)

        remaining_group = self.session.get(DuplicateGroup, group_id)
        remaining_links = self.session.exec(
            select(DuplicateMedia).where(
                DuplicateMedia.group_id == group_id
            )
        ).all()

        self.assertIsNotNone(remaining_group)
        self.assertEqual(
            {link.media_id for link in remaining_links},
            {master_id, read_only_id},
        )
        self.assertIsNone(self.session.get(Media, deleted_id))
        self.assertEqual(raised.exception.status_code, 409)
        self.assertIn("remains unresolved", raised.exception.detail)
        self.assertIn("1 file(s) were kept", raised.exception.detail)
        self.assertIn("retained for review", raised.exception.detail)

    def test_zero_skips_removes_resolved_group(self) -> None:
        group_id, media_ids = self.add_group(2)
        master_id, deleted_id = media_ids
        request = ResolveDuplicatesRequest(
            group_id=group_id,
            master_media_id=master_id,
            action="DELETE_FILES",
        )

        with patch(
            "app.api.duplicates.delete_file",
            side_effect=lambda _session, media_id: self.remove_media_record(
                media_id
            ),
        ):
            result = resolve_duplicate_group(request, self.session)

        self.assertIsNone(self.session.get(DuplicateGroup, group_id))
        self.assertEqual(
            self.session.exec(
                select(DuplicateMedia).where(
                    DuplicateMedia.group_id == group_id
                )
            ).all(),
            [],
        )
        self.assertIsNone(self.session.get(Media, deleted_id))
        self.assertEqual(
            result,
            {
                "message": f"Group {group_id} resolved successfully.",
                "skipped_read_only": 0,
            },
        )


class DeleteFileReadOnlyPreflightTests(unittest.TestCase):
    def test_read_only_preflight_happens_before_record_deletion(self) -> None:
        media = Media(
            id=42,
            path="/app/media/photo.jpg",
            filename="photo.jpg",
            size=100,
        )
        session = MagicMock()
        session.get.return_value = media

        with (
            patch.object(
                type(utils.settings.general),
                "ensure_media_path_writable",
                side_effect=PermissionError("Media directory is read-only."),
            ),
            patch("app.utils.delete_record") as delete_record,
        ):
            with self.assertRaises(HTTPException) as raised:
                utils.delete_file(session, media.id)

        self.assertEqual(raised.exception.status_code, 403)
        delete_record.assert_not_called()


if __name__ == "__main__":
    unittest.main()
