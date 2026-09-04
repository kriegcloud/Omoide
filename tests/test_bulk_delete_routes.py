import importlib
import os
import tempfile
import unittest
from datetime import datetime


_CONFIG_HOME = tempfile.TemporaryDirectory()
os.environ["XDG_CONFIG_HOME"] = _CONFIG_HOME.name

from sqlmodel import Session, SQLModel, create_engine, select  # noqa: E402

from app.models import (  # noqa: E402
    Album,
    AlbumMediaLink,
    Event,
    EventMediaLink,
    Media,
    MediaTagLink,
    Person,
    PersonTagLink,
    Tag,
)

albums_api = importlib.import_module("app.api.albums")
events_api = importlib.import_module("app.api.events")
tags_api = importlib.import_module("app.api.tags")


class BulkDeleteRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        SQLModel.metadata.create_all(
            self.engine,
            tables=[
                Media.__table__,
                Person.__table__,
                Album.__table__,
                AlbumMediaLink.__table__,
                Event.__table__,
                EventMediaLink.__table__,
                Tag.__table__,
                MediaTagLink.__table__,
                PersonTagLink.__table__,
            ],
        )
        self.session = Session(self.engine)

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def test_album_bulk_delete_skips_unknown_ids(self) -> None:
        album = Album(name="Trip")
        media = Media(path="/media/album.jpg", filename="album.jpg", size=1)
        self.session.add_all([album, media])
        self.session.commit()
        self.session.refresh(album)
        self.session.refresh(media)
        self.session.add(AlbumMediaLink(album_id=album.id, media_id=media.id))
        self.session.commit()

        result = albums_api.delete_albums_bulk(
            albums_api.AlbumBulkDeleteRequest(album_ids=[album.id, 9999]),
            self.session,
        )

        self.assertEqual(result.deleted_ids, [album.id])
        self.assertEqual(result.skipped_ids, [9999])
        self.assertIsNone(self.session.get(Album, album.id))
        self.assertEqual(self.session.exec(select(AlbumMediaLink)).all(), [])

    def test_event_bulk_delete_skips_unknown_ids(self) -> None:
        event = Event(start_at=datetime.now(), end_at=datetime.now())
        media = Media(path="/media/event.jpg", filename="event.jpg", size=1)
        self.session.add_all([event, media])
        self.session.commit()
        self.session.refresh(event)
        self.session.refresh(media)
        self.session.add(EventMediaLink(event_id=event.id, media_id=media.id))
        self.session.commit()

        result = events_api.delete_events_bulk(
            events_api.EventBulkDeleteRequest(event_ids=[event.id, 9999]),
            self.session,
        )

        self.assertEqual(result.deleted_ids, [event.id])
        self.assertEqual(result.skipped_ids, [9999])
        self.assertIsNone(self.session.get(Event, event.id))
        self.assertEqual(self.session.exec(select(EventMediaLink)).all(), [])

    def test_tag_bulk_delete_skips_unknown_ids(self) -> None:
        tag = Tag(name="holiday")
        media = Media(path="/media/tag.jpg", filename="tag.jpg", size=1)
        person = Person(name="Tagged", appearance_count=0)
        self.session.add_all([tag, media, person])
        self.session.commit()
        for item in (tag, media, person):
            self.session.refresh(item)
        self.session.add_all(
            [
                MediaTagLink(media_id=media.id, tag_id=tag.id),
                PersonTagLink(person_id=person.id, tag_id=tag.id),
            ]
        )
        self.session.commit()

        result = tags_api.delete_tags_bulk(
            tags_api.TagBulkDeleteRequest(tag_ids=[tag.id, 9999]),
            self.session,
        )

        self.assertEqual(result.deleted_ids, [tag.id])
        self.assertEqual(result.skipped_ids, [9999])
        self.assertIsNone(self.session.get(Tag, tag.id))
        self.assertEqual(self.session.exec(select(MediaTagLink)).all(), [])
        self.assertEqual(self.session.exec(select(PersonTagLink)).all(), [])


if __name__ == "__main__":
    unittest.main()
