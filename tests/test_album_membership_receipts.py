"""Direct route fixtures: no TestClient/event-loop dependency."""
import importlib
import os
import tempfile
import unittest
from unittest.mock import patch

_CONFIG = tempfile.TemporaryDirectory()
os.environ.setdefault('XDG_CONFIG_HOME', _CONFIG.name)
from sqlmodel import SQLModel, Session, create_engine, select
from app.models import Album, AlbumMediaLink, Media
api = importlib.import_module('app.api.albums')


class AlbumMembershipReceiptTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://')
        SQLModel.metadata.create_all(self.engine, tables=[Album.__table__, Media.__table__, AlbumMediaLink.__table__])
        self.session = Session(self.engine)
        self.album = Album(name='Fixture')
        self.session.add(self.album)
        for i in range(1, 4):
            self.session.add(Media(id=i, path=f'/fixture/{i}.jpg', filename=f'{i}.jpg', size=1))
        self.session.commit()
        self.session.add(AlbumMediaLink(album_id=self.album.id, media_id=1))
        self.session.commit()

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def test_add_exact_inverse_deduplicates_and_skips_invalid(self):
        result = api.add_media_to_album(self.album.id, api.AlbumMediaRequest(media_ids=[2, 1, 999, 2, 3]), self.session)
        self.assertEqual(result.added_ids, [2, 3])
        self.assertEqual(result.removed_ids, [])
        self.assertEqual(result.skipped_ids, [1, 999])
        self.assertEqual(result.media_count, 3)
        again = api.add_media_to_album(self.album.id, api.AlbumMediaRequest(media_ids=[2, 3]), self.session)
        self.assertEqual(again.added_ids, [])
        self.assertEqual(again.skipped_ids, [2, 3])

    def test_remove_only_actual_members_and_clear_cover(self):
        self.album.cover_media_id = 1
        self.session.add(self.album)
        self.session.commit()
        result = api.remove_media_from_album(self.album.id, api.AlbumMediaRequest(media_ids=[2, 1, 1, 999]), self.session)
        self.assertEqual(result.removed_ids, [1])
        self.assertEqual(result.added_ids, [])
        self.assertEqual(result.skipped_ids, [2, 999])
        self.assertIsNone(self.session.get(Album, self.album.id).cover_media_id)

    def test_empty_request_is_exact_noop(self):
        for route in (api.add_media_to_album, api.remove_media_from_album):
            result = route(self.album.id, api.AlbumMediaRequest(media_ids=[]), self.session)
            self.assertEqual((result.added_ids, result.removed_ids, result.skipped_ids), ([], [], []))

    def test_commit_failure_has_no_success_receipt(self):
        with patch.object(api, 'safe_commit', side_effect=RuntimeError('commit failed')):
            with self.assertRaisesRegex(RuntimeError, 'commit failed'):
                api.add_media_to_album(self.album.id, api.AlbumMediaRequest(media_ids=[2]), self.session)
        self.session.rollback()
        self.assertEqual([row.media_id for row in self.session.exec(select(AlbumMediaLink)).all()], [1])
