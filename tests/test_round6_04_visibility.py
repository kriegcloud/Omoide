"""Missing media stay out of browse results, covers, counts and search pages."""
import io
import importlib
from datetime import datetime
from unittest.mock import patch

import numpy as np
from fastapi import UploadFile

from round6_support import DatabaseCase
from app.models import Album, AlbumMediaLink, Event, EventMediaLink, ExifData, MediaTagLink, Scene, Tag
from app.utils import vector_to_blob

albums = importlib.import_module("app.api.albums")
events = importlib.import_module("app.api.events")
memories = importlib.import_module("app.api.memories")
places = importlib.import_module("app.api.places")
search = importlib.import_module("app.api.search")


class MissingMediaVisibilityTests(DatabaseCase):
    def setUp(self):
        super().setUp()
        self.visible = self.media(
            filename="vacation.jpg", thumbnail_path="visible.jpg",
            created_at=datetime(2020, 6, 3, 10), inserted_at=datetime(2020, 6, 3, 10),
        )
        self.missing = self.media(
            filename="vacation.png", thumbnail_path="missing.jpg",
            created_at=datetime(2020, 6, 3, 12), inserted_at=datetime(2020, 6, 3, 12),
            missing_since=datetime(2026, 6, 3),
        )
        self.query_vector = np.zeros(512, dtype=np.float32)
        self.query_vector[0] = 1
        for media, distance in ((self.missing, .01), (self.visible, .1)):
            vector = self.query_vector.copy()
            vector[1] = distance
            self.session.connection().exec_driver_sql(
                "INSERT INTO media_embeddings(media_id, embedding) VALUES (?, ?)",
                (media.id, vector_to_blob(vector)),
            )
        self.session.commit()
        self.enterContext(patch.object(search, "encode_text_query", return_value=self.query_vector))
        self.enterContext(patch.object(search, "encode_uploaded_image", return_value=self.query_vector))
        self.enterContext(patch.object(search, "_MAX_KNN_RESULTS", 1))
        search.invalidate_person_name_cache()
        self.addCleanup(search.invalidate_person_name_cache)

    def album(self):
        album = Album(name="Trip", cover_media_id=self.missing.id)
        self.session.add(album)
        self.session.commit()
        for media in (self.visible, self.missing):
            self.session.add(AlbumMediaLink(album_id=album.id, media_id=media.id))
        self.session.commit()
        return album

    def event(self):
        event = Event(start_at=self.visible.created_at, end_at=self.missing.created_at,
                      cover_media_id=self.missing.id, media_count=2)
        self.session.add(event)
        self.session.commit()
        for media in (self.visible, self.missing):
            self.session.add(EventMediaLink(event_id=event.id, media_id=media.id))
        self.session.commit()
        return event

    def add_person(self):
        person = self.person()
        person.name = "Alice"
        self.session.add(person)
        self.session.commit()
        self.face(media=self.missing, person=person)
        self.face(media=self.visible, person=person)

    def add_scenes(self):
        for media, distance in ((self.missing, .01), (self.visible, .1)):
            scene = Scene(media_id=media.id, start_time=0, end_time=1)
            self.session.add(scene)
            self.session.flush()
            vector = self.query_vector.copy()
            vector[1] = distance
            self.session.connection().exec_driver_sql(
                "INSERT INTO scene_embeddings(scene_id, media_id, embedding) VALUES (?, ?, ?)",
                (scene.id, media.id, vector_to_blob(vector)),
            )
        self.session.commit()

    def combined(self, query="beach", order_by="relevance"):
        return search.search_combined(query=query, order_by=order_by, cursor=None,
                                      limit=1, session=self.session)

    def test_filename_matches_exclude_missing(self):
        self.assertEqual([m.id for m in self.combined("vacation").media], [self.visible.id])

    def test_tag_search_nested_media_excludes_missing(self):
        tag = Tag(name="trip")
        self.session.add(tag)
        self.session.commit()
        for media in (self.visible, self.missing):
            self.session.add(MediaTagLink(tag_id=tag.id, media_id=media.id))
        self.session.commit()
        result = search.search_tags(query="trip", cursor=None, limit=1, session=self.session)
        self.assertEqual([m.id for m in result.items[0].media], [self.visible.id])

    def test_person_search_excludes_missing_before_limit(self):
        self.add_person()
        self.assertEqual([m.id for m in self.combined("Alice").media], [self.visible.id])

    def test_global_relevance_filters_missing_before_knn_limit(self):
        self.assertEqual([m.id for m in self.combined().media], [self.visible.id])

    def test_person_relevance_filters_missing_before_page_limit(self):
        self.add_person()
        self.assertEqual([m.id for m in self.combined("Alice beach").media], [self.visible.id])

    def test_global_date_filters_missing_before_knn_limit(self):
        self.assertEqual([m.id for m in self.combined(order_by="date").media], [self.visible.id])

    def test_person_date_filters_missing_before_page_limit(self):
        self.add_person()
        self.assertEqual([m.id for m in self.combined("Alice beach", "date").media], [self.visible.id])

    def test_uploaded_image_filters_missing_before_knn_limit(self):
        result = search.search_by_image(file=UploadFile(file=io.BytesIO(b"fixture")),
                                        limit=1, session=self.session)
        self.assertEqual([m.id for m in result], [self.visible.id])

    def test_global_scenes_filter_missing_before_knn_limit(self):
        self.add_scenes()
        result = search.search_scenes(query="beach", cursor=None, limit=1, session=self.session)
        self.assertEqual([scene.media_id for scene in result.items], [self.visible.id])

    def test_person_scenes_filter_missing_before_page_limit(self):
        self.add_scenes()
        self.add_person()
        result = search.search_scenes(query="Alice beach", cursor=None, limit=1, session=self.session)
        self.assertEqual([scene.media_id for scene in result.items], [self.visible.id])

    def test_album_count_excludes_missing_members(self):
        self.assertEqual(albums._album_read(self.session, self.album()).media_count, 1)

    def test_album_cover_excludes_explicit_missing_cover(self):
        self.assertEqual(albums._album_read(self.session, self.album()).cover_thumbnail, "visible.jpg")

    def test_event_count_excludes_missing_members(self):
        self.assertEqual(events._event_read(self.session, self.event()).media_count, 1)

    def test_event_cover_excludes_explicit_missing_cover(self):
        self.assertEqual(events._event_read(self.session, self.event()).cover_thumbnail, "visible.jpg")

    def test_album_and_event_media_exclude_missing_before_pagination(self):
        album, event = self.album(), self.event()
        for page in (
            albums.list_album_media(album.id, cursor=None, limit=1, session=self.session),
            events.list_event_media(event.id, cursor=None, limit=1, session=self.session),
        ):
            self.assertEqual([m.id for m in page.items], [self.visible.id])

    def test_places_cover_excludes_missing_media(self):
        for media in (self.visible, self.missing):
            self.session.add(ExifData(media_id=media.id, city="Austin", country="US"))
        self.session.commit()
        countries = places.list_places(self.session)
        self.assertEqual(countries[0].count, 1)
        self.assertEqual(countries[0].cities[0].cover_thumbnail, "visible.jpg")
        page = places.list_place_media(city="Austin", country="US", cursor=None, limit=1, session=self.session)
        self.assertEqual([m.id for m in page.items], [self.visible.id])

    def test_memories_and_highlights_exclude_missing_media_and_counts(self):
        groups = memories.get_memories(date="06-03", per_year=1, session=self.session)
        self.assertEqual([m.id for group in groups for m in group.items], [self.visible.id])
        highlights = memories.get_highlights(year=2020, limit=1, session=self.session)
        self.assertEqual([m.id for m in highlights], [self.visible.id])
        years = memories.get_highlight_years(self.session)
        self.assertEqual([(year.year, year.count) for year in years], [(2020, 1)])
