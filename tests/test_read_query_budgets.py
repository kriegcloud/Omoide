"""Read-path budgets on disposable SQLite; no HTTP server or model downloads."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import threading
import time
from unittest.mock import patch

import numpy as np
from sqlalchemy import event
from sqlmodel import select

from round6_support import DatabaseCase
from app.api.stats import get_stats
from app.api.memories import get_memories
import importlib
search = importlib.import_module("app.api.search")
from app.models import Media
from app.pagination import paginate


class ReadQueryBudgets(DatabaseCase):
    def measure(self, call):
        statements = []
        def capture(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)
        event.listen(self.engine, 'before_cursor_execute', capture)
        start = time.perf_counter()
        try:
            result = call()
        finally:
            elapsed = (time.perf_counter() - start) * 1000
            event.remove(self.engine, 'before_cursor_execute', capture)
        print(f'{self._testMethodName}: {len(statements)} SQL, {elapsed:.3f} ms')
        return result, statements

    def test_stats_budget_and_fresh_totals(self):
        self.media(size=10, is_favorite=True, created_at=datetime(2020, 1, 1))
        self.media(size=20, duration=2.5, missing_since=datetime.now(), created_at=datetime(2020, 1, 1))
        result, sql = self.measure(lambda: get_stats(self.session))
        self.assertEqual(result.totals.model_dump(), dict(media=2, images=1, videos=1,
            favorites=1, size_bytes=30, video_seconds=2.5, with_gps=0, persons=0,
            faces=0, tags=0, albums=0, events=0))
        self.assertEqual(result.per_year[0].model_dump(), dict(year=2020, images=1, videos=1))
        self.assertLessEqual(len(sql), 12)
        self.media(size=7, duration=0, is_favorite=True)
        fresh = get_stats(self.session)
        self.assertEqual((fresh.totals.media, fresh.totals.videos, fresh.totals.size_bytes), (3, 2, 37))
        self.session.rollback()
        self.assertEqual(get_stats(self.session).totals.media, 3)

    def test_memories_bound_loaded_rows_and_preserve_visibility(self):
        ids = {}
        for year in (2019, 2020):
            ids[year] = []
            for i in range(20):
                ids[year].append(self.media(created_at=datetime(year, 5, 6, 12), thumbnail_path='thumb.jpg').id)
        self.media(created_at=datetime(2020, 5, 6), thumbnail_path='missing.jpg', missing_since=datetime.now())
        self.media(created_at=datetime(2020, 5, 6), thumbnail_path='bad.jpg', processing_error='bad')
        self.media(created_at=datetime(2020, 5, 6), thumbnail_path=None)
        self.media(created_at=datetime(datetime.now().year, 5, 6), thumbnail_path='current.jpg')
        self.session.expunge_all()
        loaded = []
        def on_load(session, obj):
            if isinstance(obj, Media):
                loaded.append(obj.id)
        event.listen(self.session, 'loaded_as_persistent', on_load)
        try:
            groups, _ = self.measure(lambda: get_memories(date='05-06', per_year=2, session=self.session))
        finally:
            event.remove(self.session, 'loaded_as_persistent', on_load)
        self.assertEqual([(g.year, [m.id for m in g.items]) for g in groups],
                         [(y, list(reversed(ids[y][-2:]))) for y in (2020, 2019)])
        self.assertLessEqual(len(loaded), 4)
        # No result cache: newly eligible rows and changed visibility appear immediately.
        newest = self.media(created_at=datetime(2020, 5, 6, 13), thumbnail_path='new.jpg')
        self.assertEqual(get_memories(date='05-06', per_year=1, session=self.session)[0].items[0].id, newest.id)
        newest.missing_since = datetime.now()
        self.session.add(newest)
        self.session.commit()
        self.assertEqual(get_memories(date='05-06', per_year=1, session=self.session)[0].items[0].id, ids[2020][-1])

    def test_pagination_exact_terminal_page(self):
        first = self.media()
        second = self.media()
        rows, cursor = paginate(self.session, select(Media), [(Media.id, True)], cursor=None, limit=1)
        self.assertEqual([m.id for m in rows], [first.id])
        self.assertIsNotNone(cursor)
        rows, cursor = paginate(self.session, select(Media), [(Media.id, True)], cursor=cursor, limit=1)
        self.assertEqual([m.id for m in rows], [second.id])
        self.assertIsNone(cursor)

    def test_warm_embedding_not_blocked_by_unrelated_inference(self):
        entered, release = threading.Event(), threading.Event()
        class Encoder:
            def encode_texts(self, texts):
                if texts == ['slow']:
                    entered.set()
                    release.wait(3)
                return np.array([[1., 2.]])
        encoder = Encoder()
        with patch.object(search, 'get_clip_encoder', return_value=encoder):
            search.encode_text_query('warm')
            with ThreadPoolExecutor(max_workers=2) as pool:
                slow = pool.submit(search.encode_text_query, 'slow')
                self.assertTrue(entered.wait(1))
                warm = pool.submit(search.encode_text_query, 'warm')
                try:
                    self.assertEqual(warm.result(timeout=.3), [1., 2.])
                finally:
                    release.set()
                    slow.result(timeout=2)

    def test_top_cameras_skip_blank_make_and_model(self):
        # R8-01: 2,436 screenshots with make=None, model="" ranked as an unnamed top camera.
        from app.models import ExifData
        for _ in range(3):
            self.session.add(ExifData(media_id=self.media().id, make=None, model=""))
        self.session.add(ExifData(media_id=self.media().id, make="  ", model="   "))
        self.session.add(ExifData(media_id=self.media().id, make="Apple ", model=" iPhone 15"))
        self.session.add(ExifData(media_id=self.media().id, make="Apple", model="iPhone 15"))
        self.session.add(ExifData(media_id=self.media().id, make="Canon", model=None))
        self.session.commit()
        cameras = [(c.make, c.model, c.count) for c in get_stats(self.session).cameras]
        self.assertEqual(cameras, [("Apple", "iPhone 15", 2), ("Canon", None, 1)])

    def test_empty_stats_and_memories(self):
        stats = get_stats(self.session)
        self.assertTrue(all(value == 0 for value in stats.totals.model_dump().values()))
        self.assertEqual(stats.per_year, [])
        self.assertEqual(get_memories(date='02-30', per_year=2, session=self.session), [])

    def test_embedding_single_flight_failure_retry_and_defensive_copy(self):
        entered, release = threading.Event(), threading.Event()
        calls = []
        class Encoder:
            def encode_texts(self, texts):
                calls.append(texts)
                entered.set()
                release.wait(2)
                return np.array([[1., 2.]])
        encoder = Encoder()
        with patch.object(search, 'get_clip_encoder', return_value=encoder):
            with ThreadPoolExecutor(max_workers=4) as pool:
                futures = [pool.submit(search.encode_text_query, 'same') for _ in range(4)]
                self.assertTrue(entered.wait(1))
                release.set()
                vectors = [f.result(timeout=2) for f in futures]
            self.assertEqual(len(calls), 1)
            vectors[0][0] = 99
            self.assertEqual(search.encode_text_query('same'), [1., 2.])
            with patch.object(encoder, 'encode_texts', side_effect=ValueError('fixture failure')):
                with self.assertRaisesRegex(ValueError, 'fixture failure'):
                    search.encode_text_query('retry')
            self.assertEqual(search.encode_text_query('retry'), [1., 2.])
            self.assertEqual(search._text_embedding_pending, {})

    def test_review_counts_remain_fresh_on_continuation(self):
        from app.api.nofaces import get_no_faces_media
        for _ in range(3):
            self.media(faces_extracted=True)
        first, _ = self.measure(lambda: get_no_faces_media(
            self.session, cursor=None, limit=1, media_type=None, scope='processed'))
        self.assertEqual(first.total, 3)
        hidden = self.session.get(Media, first.items[0].id)
        hidden.missing_since = datetime.now()
        self.session.add(hidden)
        self.session.commit()
        second, _ = self.measure(lambda: get_no_faces_media(
            self.session, cursor=first.next_cursor, limit=1, media_type=None, scope='processed'))
        self.assertEqual(second.total, 2)

    def test_review_totals_can_be_skipped_explicitly(self):
        from app.api.blur import get_blurry_media
        from app.api.lowresolution import get_low_res_media
        from app.api.noexifdate import get_no_exif_date_media
        from app.api.nofaces import get_no_faces_media
        from app.api.shortvideos import get_short_videos
        from app.api.untagged import get_untagged_media
        self.media(duration=1, width=10, height=10, laplacian_score=1, faces_extracted=True)
        for listing, extra in (
            (get_blurry_media, dict(threshold=100, media_type=None)),
            (get_low_res_media, dict(max_pixels=1000, media_type=None)),
            (get_no_exif_date_media, dict(media_type=None)),
            (get_no_faces_media, dict(media_type=None, scope='processed')),
            (get_short_videos, dict(max_duration=10)),
            (get_untagged_media, dict(media_type=None)),
        ):
            with self.subTest(endpoint=listing.__name__):
                default = listing(self.session, cursor=None, limit=50, **extra)
                page, sql = self.measure(lambda: listing(self.session, cursor=None, limit=50,
                                                         include_total=False, **extra))
                self.assertEqual([m.id for m in page.items], [m.id for m in default.items])
                self.assertIsNone(page.total)
                self.assertFalse(any('count(' in statement.lower() for statement in sql))

    def test_populated_vector_search_orders_and_person_scope(self):
        from sqlalchemy import text
        from app.models import Person, Scene
        from app.vectors import vector_to_blob
        vector = np.zeros(512, dtype=np.float32)
        vector[0] = 1
        person = self.person()
        ids = []
        for index in range(6):
            media = self.media(inserted_at=datetime(2020, 1, index + 1))
            ids.append(media.id)
            self.face(media=media, person=person)
            scene = Scene(media_id=media.id, start_time=0, end_time=1)
            self.session.add(scene)
            self.session.flush()
            self.session.exec(text('INSERT INTO media_embeddings VALUES (:id, :vec)').bindparams(
                id=media.id, vec=vector_to_blob(vector)))
            self.session.exec(text('INSERT INTO scene_embeddings VALUES (:id, :media, :vec)').bindparams(
                id=scene.id, media=media.id, vec=vector_to_blob(vector)))
        self.session.commit()
        search.invalidate_person_name_cache()
        self.addCleanup(search.invalidate_person_name_cache)
        with patch.object(search, 'encode_text_query', return_value=vector):
            for query in ('beach', 'Person beach'):
                for order in ('date', 'relevance'):
                    cursor, actual = None, []
                    for _ in range(4):
                        page, _ = self.measure(lambda: search.search_combined(query=query, cursor=cursor,
                            limit=2, order_by=order, session=self.session))
                        actual.extend(m.id for m in page.media)
                        cursor = page.next_cursor
                        if cursor is None:
                            break
                    self.assertEqual(actual, ids[::-1] if order == 'date' else ids)
                page, _ = self.measure(lambda: search.search_scenes(query=query, cursor=None,
                    limit=10, session=self.session))
                self.assertEqual([s.media_id for s in page.items], ids)
