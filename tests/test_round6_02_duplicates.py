from datetime import datetime
from unittest.mock import patch

from fastapi import HTTPException
from sqlmodel import select

from round6_support import DatabaseCase
from app.api.duplicates import get_duplicates, get_duplicate_stats, resolve_duplicate_group
from app.models import Blacklist, DuplicateGroup, DuplicateIgnore, DuplicateMedia, Media
from app.schemas.duplicates import ResolveDuplicatesRequest


class DuplicateOrderingTests(DatabaseCase):
    def group(self, media):
        group = DuplicateGroup()
        self.session.add(group)
        self.session.commit()
        self.session.add_all([DuplicateMedia(group_id=group.id, media_id=m.id) for m in media])
        self.session.commit()
        return group

    def page(self, **kw):
        return get_duplicates(self.session, cursor=None, limit=10, sort_by="count", media_type=None, min_count=2, **kw)

    def test_largest_then_pixels_then_id_and_best(self):
        small = self.media(size=1)
        low = self.media(size=8, width=10, height=10)
        high = self.media(size=8, width=20, height=20)
        tie = self.media(size=8, width=20, height=20)
        self.group([small, low, high, tie])
        items = self.page().items[0].items
        self.assertEqual([m.id for m in items], [high.id, tie.id, low.id, small.id])
        self.assertEqual([m.best for m in items], [True, False, False, False])

    def test_missing_members_do_not_count_or_appear(self):
        present = self.media()
        missing = self.media(missing_since=datetime.now())
        self.group([present, missing])
        self.assertEqual(self.page().items, [])
        self.assertEqual(get_duplicate_stats(self.session).total_groups, 0)

    def test_delete_actions_preserve_hidden_members_and_group_links(self):
        for action in ("DELETE_RECORDS", "DELETE_FILES", "BLACKLIST_RECORDS"):
            with self.subTest(action=action):
                master = self.media(path=str(self.root / action / "master.jpg"), size=3)
                other = self.media(path=str(self.root / action / "other.jpg"), size=2)
                missing = self.media(path=str(self.root / action / "missing.jpg"), size=1, missing_since=datetime.now())
                master_id, other_id, missing_id = master.id, other.id, missing.id
                missing_path = missing.path
                group_id = self.group([master, other, missing]).id
                with patch("app.config.GeneralSettings.ensure_media_path_writable"):
                    response = resolve_duplicate_group(
                        ResolveDuplicatesRequest(group_id=group_id, master_media_id=master_id, action=action),
                        self.session,
                    )
                self.assertEqual(response["skipped_read_only"], 0)
                self.assertIsNone(self.session.get(Media, other_id))
                self.assertIsNotNone(self.session.get(Media, missing_id))
                self.assertIsNotNone(self.session.get(DuplicateGroup, group_id))
                links = self.session.exec(select(DuplicateMedia.media_id).where(
                    DuplicateMedia.group_id == group_id
                )).all()
                self.assertEqual(set(links), {master_id, missing_id})
                self.assertIsNone(self.session.exec(select(Blacklist).where(Blacklist.path == missing_path)).first())

    def test_mark_not_duplicate_only_marks_and_unlinks_visible_members(self):
        first, second = self.media(), self.media()
        missing = self.media(missing_since=datetime.now())
        first_id, second_id, missing_id = first.id, second.id, missing.id
        group_id = self.group([first, second, missing]).id
        resolve_duplicate_group(
            ResolveDuplicatesRequest(group_id=group_id, action="MARK_NOT_DUPLICATE"), self.session,
        )
        pairs = self.session.exec(select(DuplicateIgnore.media_id_a, DuplicateIgnore.media_id_b)).all()
        self.assertEqual([tuple(pair) for pair in pairs], [(first_id, second_id)])
        self.assertEqual(self.session.exec(select(DuplicateMedia.media_id).where(
            DuplicateMedia.group_id == group_id
        )).all(), [missing_id])
        self.assertIsNotNone(self.session.get(DuplicateGroup, group_id))

    def test_hidden_member_cannot_be_selected_as_master(self):
        first, second = self.media(), self.media()
        missing = self.media(missing_since=datetime.now())
        group = self.group([first, second, missing])
        with self.assertRaises(HTTPException) as raised:
            resolve_duplicate_group(
                ResolveDuplicatesRequest(group_id=group.id, master_media_id=missing.id, action="DELETE_RECORDS"),
                self.session,
            )
        self.assertEqual(raised.exception.status_code, 404)
