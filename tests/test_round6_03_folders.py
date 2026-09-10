from unittest.mock import patch

from round6_support import DatabaseCase
from app.api.blur import get_blurry_media, resolve_blurry
from app.schemas.blur import BlurResolveRequest
from app.config import settings
from app.models import Media
from app.models import DuplicateGroup, DuplicateMedia
from app.api.duplicates import get_duplicates, get_duplicate_stats


class ReviewFolderTests(DatabaseCase):
    def test_folder_descendants_are_case_sensitive_on_posix(self):
        wanted = self.media(path=str(self.root / "a%_" / "photo.jpg"), laplacian_score=1)
        nested = self.media(path=str(self.root / "a%_" / "nested" / "photo.jpg"), laplacian_score=2)
        self.media(path=str(self.root / "A%_" / "nested" / "photo.jpg"), laplacian_score=3)
        for folder in ("a%_", str(self.root / "a%_")):
            with self.subTest(folder=folder), patch.object(type(settings.general), "resolved_media_dirs", return_value=[(self.root, False)]):
                page = get_blurry_media(self.session, threshold=100, cursor=None, limit=50, media_type=None, folder=folder)
            self.assertEqual([m.id for m in page.items], [wanted.id, nested.id])
            self.assertEqual(page.total, 2)

    def test_duplicate_folder_qualifies_whole_group_and_stats(self):
        match = self.media(path=str(self.root / "a%_" / "photo.jpg"), size=20)
        outside = self.media(path=str(self.root / "outside" / "photo.jpg"), size=10)
        wrong = self.media(path=str(self.root / "a%_b" / "photo.jpg"))
        wrong2 = self.media(path=str(self.root / "abc" / "photo.jpg"))
        groups = [DuplicateGroup(), DuplicateGroup()]
        self.session.add_all(groups)
        self.session.commit()
        self.session.add_all([DuplicateMedia(group_id=g.id, media_id=m.id) for g, items in zip(groups, [[match, outside], [wrong, wrong2]]) for m in items])
        self.session.commit()
        folder = str(self.root / "a%_")
        result = get_duplicates(self.session, cursor=None, limit=10, sort_by="count", media_type=None, min_count=2, folder=folder)
        self.assertEqual([group.group_id for group in result.items], [groups[0].id])
        self.assertEqual([m.id for m in result.items[0].items], [match.id, outside.id])
        stats = get_duplicate_stats(self.session, folder=folder)
        self.assertEqual((stats.total_groups, stats.total_items, stats.total_size_bytes, stats.total_reclaimable_bytes), (1, 2, 30, 10))

    def test_literal_wildcards_and_separator_boundary_in_review(self):
        wanted = self.media(path=str(self.root / "a%_" / "photo.jpg"), laplacian_score=1)
        nested = self.media(path=str(self.root / "a%_" / "nested" / "photo.jpg"), laplacian_score=2)
        self.media(path=str(self.root / "a%_b" / "photo.jpg"), laplacian_score=3)
        self.media(path=str(self.root / "abc" / "photo.jpg"), laplacian_score=4)
        with patch.object(type(settings.general), "resolved_media_dirs", return_value=[(self.root, False)]):
            page = get_blurry_media(self.session, threshold=100, cursor=None, limit=50, media_type=None, folder="a%_")
        self.assertEqual([m.id for m in page.items], [wanted.id, nested.id])
        self.assertEqual(page.total, 2)

    def test_select_all_resolve_obeys_same_folder(self):
        wanted = self.media(path=str(self.root / "a" / "photo.jpg"), laplacian_score=1)
        other = self.media(path=str(self.root / "ab" / "photo.jpg"), laplacian_score=1)
        wanted_id, other_id = wanted.id, other.id
        result = resolve_blurry(BlurResolveRequest(action="DELETE_RECORDS", select_all=True, folder=str(self.root / "a")), self.session)
        self.assertEqual(result["processed_ids"], [wanted_id])
        self.assertIsNotNone(self.session.get(Media, other_id))
