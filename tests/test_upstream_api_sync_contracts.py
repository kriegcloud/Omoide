"""Merged upstream pagination/geotagging must retain workstation visibility rules."""
from datetime import datetime
from unittest.mock import patch

from round6_support import DatabaseCase
from app.api.media.core import update_geolocation
from app.api.media.geotagging import get_missing_geo
from app.api.media.listing import list_media
from app.api.person.core import list_persons
from app.config import settings
from app.models import ExifData, Person
from app.schemas.media import GeoUpdate


class UpstreamApiSyncContracts(DatabaseCase):
    def test_media_opaque_pagination_keeps_literal_folder_and_missing_filter(self):
        wanted = [self.media(path=str(self.root / 'a%_' / f'{i}.jpg')) for i in range(3)]
        self.media(path=str(self.root / 'a%_b' / 'outside.jpg'))
        self.media(path=str(self.root / 'a%_' / 'missing.jpg'), missing_since=datetime.now())
        with patch.object(type(settings.general), 'resolved_media_dirs', return_value=[(self.root, False)]):
            arguments = dict(tags=None, person_id=None, folder='a%_', recursive=True,
                             camera_make=None, camera_model=None, sort='latest',
                             limit=2, session=self.session)
            first = list_media(cursor=None, **arguments)
            second = list_media(cursor=first.next_cursor, **arguments)
        self.assertIsNotNone(first.next_cursor)
        self.assertEqual({m.id for m in first.items + second.items}, {m.id for m in wanted})
        self.assertIsNone(second.next_cursor)

    def test_upstream_favorite_sort_keeps_fork_hidden_and_gender_filters(self):
        favorite = Person(name='Favorite', appearance_count=1, is_favorite=True, gender='female')
        ordinary = Person(name='Ordinary', appearance_count=5, gender='female')
        self.session.add_all([favorite, ordinary,
                             Person(name='Hidden', appearance_count=0, is_favorite=True, gender='female', hidden_at=datetime.now()),
                             Person(name='Other gender', appearance_count=0, is_favorite=True, gender='male')])
        self.session.commit()
        args = dict(name=None, sort='favorites', limit=1, hidden=False, gender='female', session=self.session)
        first = list_persons(cursor=None, **args)
        second = list_persons(cursor=first.next_cursor, **args)
        self.assertEqual([p.id for p in first.items + second.items], [favorite.id, ordinary.id])
        self.assertTrue(first.items[0].is_favorite)
        self.assertEqual(list_persons(cursor=second.next_cursor, **args).items, [])

    def test_missing_geo_includes_no_exif_but_hides_missing_and_obeys_folder(self):
        wanted = self.media(path=str(self.root / 'a%_' / 'image.jpg'))
        self.media(path=str(self.root / 'a%_b' / 'outside.jpg'))
        self.media(path=str(self.root / 'a%_' / 'missing.jpg'), missing_since=datetime.now())
        with patch.object(type(settings.general), 'resolved_media_dirs', return_value=[(self.root, False)]), \
             patch.object(settings.general, 'write_location_to_files', False):
            result = get_missing_geo(session=self.session, folder='a%_', sort='latest', cursor=None, limit=50)
        self.assertEqual([m.id for m in result.items], [wanted.id])

    def test_manual_location_flag_clears_stale_place_without_touching_original(self):
        media = self.media()
        exif = ExifData(media_id=media.id, lat=1, lon=2, city='Old city', country='Old country')
        self.session.add(exif)
        self.session.commit()
        with patch.object(settings.general, 'write_location_to_files', False), \
             patch('app.api.media.core.update_exif_gps') as writer:
            update_geolocation(media.id, GeoUpdate(latitude=10, longitude=20), self.session)
        self.session.refresh(exif)
        self.assertEqual((exif.lat, exif.lon), (10, 20))
        self.assertTrue(exif.location_set_manually)
        self.assertIsNone(exif.city)
        self.assertIsNone(exif.country)
        writer.assert_not_called()


class OnnxSearchCacheContracts(DatabaseCase):
    def test_current_encoder_vectors_are_cached_but_replacement_invalidates(self):
        import importlib
        import numpy as np

        search = importlib.import_module('app.api.search')

        class Encoder:
            def __init__(self, value):
                self.value = value
                self.queries = []

            def encode_texts(self, queries):
                self.queries.extend(queries)
                return np.asarray([[self.value] for _ in queries])

        first = Encoder(1.0)
        replacement = Encoder(2.0)
        with patch.object(search, 'get_clip_encoder', return_value=first) as loader:
            vector = search.encode_text_query('  Beach  ')
            vector[0] = 99
            self.assertEqual(search.encode_text_query('Beach'), [1.0])
            self.assertEqual(first.queries, ['Beach'])
            loader.return_value = replacement
            self.assertEqual(search.encode_text_query('Beach'), [2.0])
            loader.return_value = first
            self.assertEqual(search.encode_text_query('Beach'), [1.0])
            self.assertEqual(first.queries, ['Beach', 'Beach'])

    def test_cache_does_not_retain_released_onnx_encoder(self):
        import gc
        import importlib
        import weakref
        import numpy as np

        search = importlib.import_module('app.api.search')

        class Encoder:
            def encode_texts(self, queries):
                return np.asarray([[1.0]])

        encoder = Encoder()
        reference = weakref.ref(encoder)
        current = [encoder]
        with patch.object(search, 'get_clip_encoder', side_effect=lambda: current[0]):
            search.encode_text_query('beach')
            current[0] = None
            del encoder
            gc.collect()
            self.assertIsNone(reference())
            self.assertEqual(len(search._text_embedding_cache), 0)
