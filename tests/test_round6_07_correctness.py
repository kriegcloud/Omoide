from datetime import date, datetime

from round6_support import DatabaseCase
from app.api.media import list_media, list_images, list_videos, list_favorites, get_media
from app.api.person import get_person_timeline, get_appearances
from app.models import MediaTagLink, PersonMediaLink, Tag, TimelineEvent


def media_page(session, **kwargs):
    args = dict(tags=None, person_id=None, folder=None, recursive=True,
                camera_make=None, camera_model=None, sort="newest", cursor=None, limit=100, session=session)
    args.update(kwargs)
    return list_media(**args)


class ListCorrectnessTests(DatabaseCase):
    def test_multiple_matching_tags_do_not_duplicate_media(self):
        media = self.media()
        first, second = Tag(name="one"), Tag(name="two")
        self.session.add_all([first, second])
        self.session.commit()
        self.session.add_all([MediaTagLink(media_id=media.id, tag_id=t.id) for t in (first, second)])
        self.session.commit()
        page = media_page(self.session, tags=["one", "two"])
        self.assertEqual([m.id for m in page.items], [media.id])

    def test_timeline_cursor_keeps_remainder_of_day_and_type_ties(self):
        person = self.person()
        media = [self.media(created_at=datetime(2025, 1, 2, 12, n)) for n in range(3)]
        self.session.add_all([PersonMediaLink(person_id=person.id, media_id=m.id) for m in media])
        self.session.add(TimelineEvent(person_id=person.id, title="Event", event_date=date(2025, 1, 2)))
        self.session.commit()
        cursor, seen = None, []
        for _ in range(5):
            page = get_person_timeline(person.id, self.session, cursor=cursor, limit=1)
            for item in page["items"]:
                obj = item.get("items") or item.get("event")
                seen.append((item["type"], obj.id))
            cursor = page["next_cursor"]
            if cursor is None:
                break
        self.assertEqual(len(seen), 4)
        self.assertEqual(len(set(seen)), 4)

    def test_missing_media_hidden_in_browse_but_available_in_detail(self):
        present = self.media(is_favorite=True)
        missing = self.media(missing_since=datetime.now(), is_favorite=True)
        self.assertEqual([m.id for m in media_page(self.session).items], [present.id])
        self.assertEqual([m.id for m in list_images(limit=50, session=self.session, cursor=None).items], [present.id])
        detail = get_media(missing.id, self.session)
        self.assertIsNotNone(detail.media.missing_since)
        self.assertEqual([m.id for m in list_favorites(limit=50, session=self.session, cursor=None).items], [present.id])
        video = self.media(duration=1)
        self.media(duration=1, missing_since=datetime.now())
        self.assertEqual([m.id for m in list_videos(limit=50, session=self.session, cursor=None).items], [video.id])
        person = self.person()
        self.session.add_all([PersonMediaLink(person_id=person.id, media_id=m.id) for m in (present, missing)])
        self.session.commit()
        appearances = get_appearances(person.id, with_person_ids=[], tags=None, limit=30, cursor=None, session=self.session)
        self.assertEqual([m.id for m in appearances.items], [present.id])
