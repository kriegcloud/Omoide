from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from sqlmodel import select

from round6_support import DatabaseCase
from app.models import Blacklist, Media
from app.api.media import bulk_delete_media
from app.schemas.media import MediaBulkDeleteRequest
from app.config import settings
from app.utils import delete_file
from app.api.blur import resolve_blurry
from app.schemas.blur import BlurResolveRequest


class DeleteFileSafetyTests(DatabaseCase):
    def test_late_writable_guard_refusal_rolls_back_catalog_changes(self):
        media = self.media()
        original = Path(media.path)
        original.touch()
        media_id = media.id
        with patch.object(type(settings.general), "ensure_media_path_writable", side_effect=[None, None, PermissionError("root became read-only")]):
            with self.assertRaises(HTTPException) as error:
                delete_file(self.session, media_id)
        self.assertEqual(error.exception.status_code, 403)
        # Existing callers may catch a refused file and continue their batch.
        self.session.commit()
        self.assertIsNotNone(self.session.get(Media, media_id))
        self.assertTrue(original.exists())

    def test_delete_file_removes_legacy_thumbnail_fallback(self):
        media = self.media(thumbnail_path=None)
        original = Path(media.path)
        original.touch()
        thumbnail = self.root / f"{media.id}.jpg"
        thumbnail.touch()
        with patch.object(type(settings.general), "ensure_media_path_writable"):
            delete_file(self.session, media.id)
        self.assertFalse(thumbnail.exists())

    def test_bulk_delete_isolates_failure_and_unknown_ids(self):
        first, failed, last = self.media(), self.media(), self.media()
        ids = [m.id for m in (first, failed, last)]
        from app.utils import delete_record
        def delete_one(media_id, session):
            if media_id == ids[1]:
                raise OSError("fixture failure")
            return delete_record(media_id, session)
        with patch("app.api._resolve.delete_record", side_effect=delete_one):
            result = bulk_delete_media(MediaBulkDeleteRequest(media_ids=[*ids, 99999, ids[0]], action="DELETE_RECORDS"), self.session)
        self.assertEqual(result["removed"], 2)
        self.assertEqual(result["processed_ids"], [ids[0], ids[2]])
        self.assertEqual(set(result["skipped_ids"]), {ids[1], 99999})
        self.assertEqual(result["errors"], [{"id": ids[1], "reason": "fixture failure"}])
        self.assertIsNotNone(self.session.get(Media, ids[1]))

    def test_bulk_file_delete_refuses_read_only_root(self):
        media = self.media()
        media_id = media.id
        with patch.object(type(settings.general), "ensure_media_path_writable", side_effect=PermissionError("read-only root")):
            result = bulk_delete_media(MediaBulkDeleteRequest(media_ids=[media_id], action="DELETE_FILES"), self.session)
        self.assertEqual(result["processed_ids"], [])
        self.assertEqual(result["skipped_ids"], [media_id])
        self.assertIsNotNone(self.session.get(Media, media_id))

    def test_bulk_delete_blacklists_path(self):
        media = self.media()
        path = media.path
        result = bulk_delete_media(MediaBulkDeleteRequest(media_ids=[media.id], action="BLACKLIST_RECORDS"), self.session)
        self.assertEqual(result["removed"], 1)
        self.assertEqual(self.session.exec(select(Blacklist.path)).all(), [path])

    def test_bulk_delete_all_unknown_ids_is_successful_noop(self):
        result = bulk_delete_media(MediaBulkDeleteRequest(media_ids=[99999], action="DELETE_RECORDS"), self.session)
        self.assertEqual(result, {"removed": 0, "processed_ids": [], "skipped_ids": [99999], "errors": []})

    def test_bulk_delete_has_presentation_guard(self):
        with patch.object(settings.general, "presentation_mode", True):
            with self.assertRaises(HTTPException) as error:
                bulk_delete_media(MediaBulkDeleteRequest(media_ids=[1], action="DELETE_RECORDS"), self.session)
        self.assertEqual(error.exception.status_code, 403)

    def test_review_response_identifies_successful_records(self):
        media = self.media(laplacian_score=10)
        media_id = media.id
        result = resolve_blurry(BlurResolveRequest(media_ids=[media_id], action="DELETE_RECORDS"), self.session)
        self.assertEqual(result.get("processed_ids"), [media_id])

    def test_unlink_failure_preserves_catalog_record(self):
        media = self.media()
        Path(media.path).touch()
        media_id = media.id
        with patch("app.config.GeneralSettings.ensure_media_path_writable"), patch.object(Path, "unlink", side_effect=OSError("disk refused")):
            with self.assertRaises((OSError, HTTPException)):
                delete_file(self.session, media_id)
        self.session.rollback()
        self.assertIsNotNone(self.session.get(Media, media_id))
        self.assertTrue(Path(media.path).exists())
