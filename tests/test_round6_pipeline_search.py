"""Search cursor, profile identity and encoder lifecycle audit regressions."""
import importlib
from datetime import datetime
from unittest.mock import patch

import numpy as np
from sqlalchemy import text
from sqlmodel import Session, SQLModel, create_engine

from round6_support import DatabaseCase
from app import config
from app.models import Media, Person, PersonMediaLink, Scene
from app.utils import vector_to_blob

search = importlib.import_module("app.api.search")


class SearchRegressionTests(DatabaseCase):
    def setUp(self):
        super().setUp()
        self.vector = np.zeros(512, dtype=np.float32)
        self.vector[0] = 1
        search.invalidate_person_name_cache()
        self.addCleanup(search.invalidate_person_name_cache)
        self.encoder = self.enterContext(patch.object(search, "encode_text_query", return_value=self.vector))

    def add_vectors(self, count=4, *, scenes=False, exact=False):
        vector = self.vector.copy()
        if not exact:
            vector[1] = .25
        ids = []
        for index in range(count):
            media = self.media(inserted_at=datetime(2026, 1, 1), type="video" if scenes else "image")
            if scenes:
                scene = Scene(media_id=media.id, start_time=index, end_time=index + 1)
                self.session.add(scene)
                self.session.flush()
                self.session.exec(text("INSERT INTO scene_embeddings VALUES (:id, :media, :vec)").bindparams(
                    id=scene.id, media=media.id, vec=vector_to_blob(vector)))
                ids.append(scene.id)
            else:
                self.session.exec(text("INSERT INTO media_embeddings VALUES (:id, :vec)").bindparams(
                    id=media.id, vec=vector_to_blob(vector)))
                ids.append(media.id)
        self.session.commit()
        return ids

    def combined(self, query="beach", cursor=None, limit=2):
        return search.search_combined(query=query, cursor=cursor, limit=limit,
                                      order_by="relevance", session=self.session)

    def test_media_distance_ties_cross_pages_without_losses(self):
        expected = self.add_vectors()
        first = self.combined()
        second = self.combined(cursor=first.next_cursor)
        self.assertEqual([m.id for m in first.media + second.media], expected)
        self.assertIsNone(second.next_cursor)

    def test_exact_zero_distance_is_not_dropped_on_first_page(self):
        expected = self.add_vectors(1, exact=True)
        self.assertEqual([m.id for m in self.combined().media], expected)

    def test_scene_distance_ties_cross_pages_without_losses(self):
        expected = self.add_vectors(scenes=True)
        first = search.search_scenes(query="beach", cursor=None, limit=2, session=self.session)
        second = search.search_scenes(query="beach", cursor=first.next_cursor, limit=2, session=self.session)
        self.assertEqual([m.scene_id for m in first.items + second.items], expected)

    def test_tie_group_larger_than_knn_window_remains_reachable(self):
        expected = self.add_vectors(9)
        cursor, actual = None, []
        with patch.object(search, "_MAX_KNN_RESULTS", 3):
            for _ in range(6):
                result = self.combined(cursor=cursor)
                actual.extend(m.id for m in result.media)
                cursor = result.next_cursor
                if cursor is None:
                    break
        self.assertEqual(actual, expected)

    def test_person_only_search_uses_all_names_and_real_date_cursor(self):
        ids = self.add_vectors(4)
        alice, bob = Person(name="Alice", appearance_count=0), Person(name="Bob", appearance_count=0)
        self.session.add_all([alice, bob])
        self.session.commit()
        for mid in ids:
            self.face(media=self.session.get(Media, mid), person=alice)
            if mid != ids[0]:
                self.face(media=self.session.get(Media, mid), person=bob)
        first = self.combined("Alice Bob")
        self.assertEqual([m.id for m in first.media], ids[:1:-1])
        self.assertIsNotNone(first.next_cursor)
        second = self.combined("Alice Bob", cursor=first.next_cursor)
        self.assertEqual([m.id for m in second.media], [ids[1]])
        self.assertIsNone(second.next_cursor)
        semantic = self.combined("Alice Bob beach", limit=10)
        self.assertEqual([m.id for m in semantic.media], ids[1:])

    def test_empty_media_vectors_do_not_load_encoder(self):
        self.combined()
        self.encoder.assert_not_called()

    def test_empty_scene_vectors_do_not_load_encoder_when_media_vectors_exist(self):
        self.add_vectors(1)
        search.search_scenes(query="beach", cursor=None, limit=2, session=self.session)
        self.encoder.assert_not_called()

    def test_name_cache_isolated_by_database_identity(self):
        self.session.add(Person(id=1, name="Alice", appearance_count=0))
        self.session.commit()
        self.assertEqual(search._get_person_cache(self.session).name_to_id, {"alice": 1})
        other = create_engine("sqlite://")
        self.addCleanup(other.dispose)
        SQLModel.metadata.create_all(other)
        with Session(other) as session:
            session.add(Person(id=1, name="Bob", appearance_count=0))
            session.commit()
            self.assertEqual(search._get_person_cache(session).name_to_id, {"bob": 1})


class TextTowerLifecycleTests(DatabaseCase):
    def test_query_cache_key_includes_model_id_even_when_bundle_is_unchanged(self):
        class Tower:
            pass
        tower = Tower()
        with patch.object(search, "get_clip_bundle", return_value=(tower, None, None)), patch.object(
            search, "_encode_text_query_uncached", return_value=[1.0]
        ) as encode:
            with patch.object(config.settings.ai, "clip_model", config.ClipModel.VIT_B_32):
                search.encode_text_query("beach")
            with patch.object(config.settings.ai, "clip_model", config.ClipModel.ROBERTA_BASE_VIT_B_32):
                search.encode_text_query("beach")
            self.assertEqual(encode.call_count, 2)

    def test_text_search_keeps_shared_bundle_warm_after_processor_release(self):
        class Tower:
            pass
        tower = Tower()
        with patch.object(config, "_clip_model", tower), patch.object(config, "_clip_refs", 1), patch.object(
            search, "_encode_text_query_uncached", return_value=[1.0]
        ):
            search.encode_text_query("beach")
            config.release_clip()
            self.assertIs(config._clip_model, tower)
            config._reset_clip_after_settings_change()
            self.assertIsNone(config._clip_model)


class SearchHandlerPerformanceTests(DatabaseCase):
    def test_combined_beach_handler_profile_with_fake_encoder(self):
        """Benchmark the GET handler directly; no weights, socket, or timing assertion."""
        import time
        from math import sin

        class FakeTower:
            calls = 0

        tower = FakeTower()
        vector = np.zeros(512, dtype=np.float32)
        vector[0] = 1

        def encode(*_):
            tower.calls += 1
            # Deterministic CPU work makes skipped inference visible in timings.
            sum(sin(i) for i in range(20_000))
            return vector.tolist()

        def request():
            return search.search_combined(query="beach", limit=20, cursor=None,
                                          order_by="relevance", session=self.session)

        search.invalidate_person_name_cache()
        self.addCleanup(search.invalidate_person_name_cache)
        with patch.object(search, "get_clip_bundle", return_value=(tower, None, None)), patch.object(
            search, "_encode_text_query_uncached", side_effect=encode
        ):
            # Reproduce the old empty-table path while retaining its existing LRU.
            with patch.object(search, "_has_search_vectors", return_value=True):
                before_start = time.perf_counter()
                before_result = request()
                before = time.perf_counter() - before_start
            self.assertEqual(tower.calls, 1)
            after_start = time.perf_counter()
            after_result = request()
            after = time.perf_counter() - after_start
            self.assertEqual(tower.calls, 1)
            self.assertEqual(before_result.model_dump(), after_result.model_dump())
            media = self.media()
            self.session.exec(text("INSERT INTO media_embeddings VALUES (:id, :vec)").bindparams(
                id=media.id, vec=vector_to_blob(vector)))
            self.session.commit()
            # Same query now uses the previously cached embedding, without work.
            request()
            request()
            self.assertEqual(tower.calls, 1)
        print(
            "\nGET /api/search/combined?query=beach handler, fake CPU encoder, empty vectors: "
            f"before {before:.6f}s / 1 encode; after {after:.6f}s / 0 encodes. "
            "Direct handler timing, excludes HTTP transport and real CLIP weights."
        )
