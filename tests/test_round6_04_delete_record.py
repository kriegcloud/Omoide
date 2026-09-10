from datetime import datetime

from sqlmodel import select, text

from round6_support import DatabaseCase
from app.models import (
    Album, AlbumMediaLink, DatasetItem, DuplicateGroup, DuplicateIgnore,
    DuplicateMedia, Event, EventMediaLink, ExifData, Face, Media,
    MediaCurationStats, MediaTagLink, PersonMediaLink, PersonRelationship,
    Scene, Tag, TrainingDataset,
)
from app.utils import delete_record


class MediaForeignKeyDeletionTests(DatabaseCase):
    def test_delete_record_cleans_every_unmanaged_media_foreign_key(self):
        media, other = self.media(), self.media()
        first, second = self.person(), self.person()
        album = Album(name="album", cover_media_id=media.id)
        event = Event(start_at=datetime.now(), end_at=datetime.now(), cover_media_id=media.id, media_count=1)
        dataset = TrainingDataset(name="dataset", slug="dataset", trigger_word="test", class_token="person", cover_media_id=media.id)
        group, tag = DuplicateGroup(), Tag(name="tag")
        self.session.add_all([album, event, dataset, group, tag])
        self.session.commit()
        refs = [
            Face(media_id=media.id, bbox=[0, 0, 1, 1], person_id=first.id),
            AlbumMediaLink(album_id=album.id, media_id=media.id),
            EventMediaLink(event_id=event.id, media_id=media.id),
            DatasetItem(dataset_id=dataset.id, media_id=media.id),
            DuplicateMedia(group_id=group.id, media_id=media.id),
            DuplicateIgnore(media_id_a=media.id, media_id_b=other.id),
            DuplicateIgnore(media_id_a=other.id, media_id_b=media.id),
            ExifData(media_id=media.id),
            MediaCurationStats(media_id=media.id, brightness_mean=1, contrast_std=1),
            MediaTagLink(media_id=media.id, tag_id=tag.id),
            PersonMediaLink(media_id=media.id, person_id=first.id),
            PersonRelationship(person_a_id=first.id, person_b_id=second.id, last_media_id=media.id),
            Scene(media_id=media.id, start_time=0, end_time=1),
        ]
        self.session.add_all(refs)
        self.session.commit()
        expected = {(row.__tablename__, key) for row, key in [
            (refs[0], "media_id"), (album, "cover_media_id"),
            (event, "cover_media_id"), (dataset, "cover_media_id"),
            *[(row, "media_id") for row in refs[1:5]],
            (refs[5], "media_id_a"), (refs[6], "media_id_b"),
            *[(row, "media_id") for row in refs[7:11]],
            (refs[11], "last_media_id"), (refs[12], "media_id"),
        ]}
        discovered = set()
        for (table,) in self.session.exec(text("SELECT name FROM sqlite_master WHERE type='table'")):
            for fk in self.session.exec(text(f'PRAGMA foreign_key_list("{table}")')):
                if fk[2] == "media" and fk[6] == "NO ACTION":
                    discovered.add((table, fk[3]))
        self.assertEqual(discovered, expected, "Seed every new unmanaged media FK in this regression")
        media_id = media.id
        delete_record(media_id, self.session)
        self.session.expire_all()
        self.assertIsNone(self.session.get(Media, media_id))
        self.assertEqual(self.session.exec(select(DatasetItem)).all(), [])
        self.assertEqual(self.session.exec(select(MediaCurationStats)).all(), [])
        self.assertIsNone(self.session.get(TrainingDataset, dataset.id).cover_media_id)
        self.assertEqual(self.session.exec(text("PRAGMA foreign_key_check")).all(), [])
