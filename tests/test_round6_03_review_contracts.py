from datetime import datetime

from round6_support import DatabaseCase
from app.api.blur import get_blurry_media
from app.api.lowresolution import get_low_res_media
from app.api.noexifdate import get_no_exif_date_media
from app.api.nopersons import get_no_persons_media
from app.api.shortvideos import get_short_videos
from app.api.untagged import get_untagged_media


class ReviewVisibilityContracts(DatabaseCase):
    def test_all_six_review_lists_count_only_visible_folder_members(self):
        folder = str(self.root / "matching")
        values = dict(duration=1, width=10, height=10, laplacian_score=1, faces_extracted=True)
        visible = self.media(path=folder + "/visible.jpg", **values)
        self.media(path=folder + "/missing.jpg", missing_since=datetime.now(), **values)
        self.media(path=str(self.root / "elsewhere" / "other.jpg"), **values)
        for listing, extra in (
            (get_blurry_media, dict(threshold=100, media_type=None)),
            (get_low_res_media, dict(max_pixels=1000, media_type=None)),
            (get_no_exif_date_media, dict(media_type=None)),
            (get_no_persons_media, dict(media_type=None, scope="processed")),
            (get_short_videos, dict(max_duration=10)),
            (get_untagged_media, dict(media_type=None)),
        ):
            with self.subTest(endpoint=listing.__name__):
                page = listing(self.session, cursor=None, limit=50, folder=folder, **extra)
                self.assertEqual([m.id for m in page.items], [visible.id])
                self.assertEqual(page.total, 1)

    def test_low_resolution_cursor_uses_stored_pixel_count_and_id(self):
        first = self.media(width=10, height=10)
        second = self.media(width=10, height=10)
        page = get_low_res_media(self.session, max_pixels=1000, cursor=None, limit=1, media_type=None)
        self.assertEqual(page.next_cursor, f"100_{first.id}")
        next_page = get_low_res_media(self.session, max_pixels=1000, cursor=page.next_cursor, limit=1, media_type=None)
        self.assertEqual([m.id for m in next_page.items], [second.id])
