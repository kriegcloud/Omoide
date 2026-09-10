"""Synthetic request profile; timings are evidence, not flaky CI thresholds."""

import cProfile
import gc
import io
import importlib
import os
import pstats
import tempfile
import unittest
import weakref
from unittest.mock import patch

import numpy as np
from sqlmodel import Session, SQLModel, create_engine

_CONFIG_HOME = tempfile.TemporaryDirectory()
os.environ["XDG_CONFIG_HOME"] = _CONFIG_HOME.name

import app.api  # Bootstrap task imports in the application order.
from app.database import _attach_engine_listeners
from app.models import Face, Media, Person

face_api = importlib.import_module("app.api.face")
search_api = importlib.import_module("app.api.search")


class SyntheticTextTower:
    """Small real CPU projection workload; no weights or downloads needed."""

    def __init__(self):
        import torch

        generator = torch.Generator().manual_seed(90609)
        self.weights = torch.randn(256, 256, generator=generator) / 16
        self.calls = 0

    def parameters(self):
        return iter(())

    def encode_text(self, tokens):
        self.calls += 1
        for _ in range(16):
            tokens = tokens @ self.weights
        return tokens


def synthetic_tokenizer(queries):
    import torch

    rows = []
    for query in queries:
        counts = np.bincount(list(query.encode("utf-8")), minlength=256).astype(np.float32)
        counts[0] += 1
        rows.append(counts)
    return torch.tensor(np.asarray(rows))


class TextQueryCacheTests(unittest.TestCase):
    def setUp(self):
        self.tower = SyntheticTextTower()
        self.bundle = [self.tower, None, synthetic_tokenizer]
        self.enterContext(patch.object(search_api, "get_clip_bundle", side_effect=lambda: tuple(self.bundle)))

    def test_repeated_normalized_query_encodes_once(self):
        first = search_api.encode_text_query("Beach")
        self.assertEqual(first, search_api.encode_text_query("Beach"))
        self.assertEqual(self.tower.calls, 1)
        self.assertEqual(first, search_api.encode_text_query("  Beach  "))
        self.assertEqual(first, search_api.encode_text_query("\tBeach\n"))
        self.assertEqual(self.tower.calls, 1)

    def test_alternate_case_internal_spaces_and_empty_queries_keep_their_semantics(self):
        for query in ("Beach", "beach", "beach day", "beach  day", ""):
            first = search_api.encode_text_query(query)
            self.assertEqual(first, search_api.encode_text_query(query))
        self.assertEqual(self.tower.calls, 5)
        self.assertEqual(search_api.encode_text_query("   "), search_api.encode_text_query(""))
        self.assertEqual(self.tower.calls, 5)

    def test_returned_vectors_cannot_mutate_the_cached_embedding(self):
        first = search_api.encode_text_query("beach")
        expected = first.copy()
        first[0] = 999
        self.assertEqual(search_api.encode_text_query("beach"), expected)
        self.assertEqual(self.tower.calls, 1)

    def test_bundle_change_invalidates_cached_queries(self):
        search_api.encode_text_query("beach")
        replacement = SyntheticTextTower()
        self.bundle[0] = replacement
        search_api.encode_text_query("beach")
        self.assertEqual(replacement.calls, 1)
        self.bundle[0] = self.tower
        search_api.encode_text_query("beach")
        self.assertEqual(self.tower.calls, 2)

    def test_cache_does_not_keep_released_model_alive(self):
        search_api.encode_text_query("beach")
        reference = weakref.ref(self.tower)
        self.bundle[0] = None
        self.tower = None
        gc.collect()
        self.assertIsNone(reference())
        self.assertEqual(len(search_api._text_embedding_cache), 0)

    def test_cache_evicts_the_least_recently_used_query(self):
        with patch.object(search_api, "_TEXT_EMBEDDING_CACHE_SIZE", 2):
            search_api.encode_text_query("one")
            search_api.encode_text_query("two")
            search_api.encode_text_query("one")
            search_api.encode_text_query("three")
            search_api.encode_text_query("one")
            self.assertEqual(self.tower.calls, 3)
            search_api.encode_text_query("two")
            self.assertEqual(self.tower.calls, 4)

    def test_profile_repeated_query_before_and_after_cache(self):
        repeats = 50
        before = cProfile.Profile()
        before.enable()
        for _ in range(repeats):
            search_api._encode_text_query_uncached("beach", self.tower, synthetic_tokenizer)
        before.disable()
        before_calls = self.tower.calls
        after = cProfile.Profile()
        after.enable()
        for _ in range(repeats):
            search_api.encode_text_query("  beach  ")
        after.disable()
        self.assertEqual(before_calls, repeats)
        self.assertEqual(self.tower.calls - before_calls, 1)
        print(
            f"\nSynthetic CPU text tower, {repeats} identical requests: "
            f"uncached {pstats.Stats(before).total_tt:.6f}s / {before_calls} encodes; "
            f"cached {pstats.Stats(after).total_tt:.6f}s / 1 encode "
            "(mechanism benchmark, not real CLIP weights)."
        )


class OrphanSuggestionsPerformanceTests(unittest.TestCase):
    FACE_COUNT = 30_000
    PERSON_COUNT = 100
    ASSIGNED_PER_PERSON = 20

    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine("sqlite://")
        cls.addClassCleanup(cls.engine.dispose)
        _attach_engine_listeners(cls.engine)
        SQLModel.metadata.create_all(cls.engine)
        random = np.random.default_rng(90609)
        prototypes = random.normal(size=(cls.PERSON_COUNT, 512)).astype(np.float32)
        prototypes /= np.linalg.norm(prototypes, axis=1, keepdims=True)
        person_indices = np.arange(cls.FACE_COUNT) % cls.PERSON_COUNT
        vectors = prototypes[person_indices] + random.normal(
            scale=0.015, size=(cls.FACE_COUNT, 512)
        ).astype(np.float32)
        vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
        assigned_count = cls.PERSON_COUNT * cls.ASSIGNED_PER_PERSON
        with cls.engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE VIRTUAL TABLE face_embeddings USING vec0("
                "face_id integer primary key, person_id integer, embedding float[512])"
            )
            connection.execute(Person.__table__.insert(), [
                {"id": index + 1, "name": f"Synthetic person {index + 1}",
                 "appearance_count": 1}
                for index in range(cls.PERSON_COUNT)
            ])
            connection.execute(Media.__table__.insert(), {
                "id": 1, "path": "/synthetic/profile.jpg", "filename": "profile.jpg", "size": 1,
            })
            connection.execute(Face.__table__.insert(), [
                {"id": index + 1, "media_id": 1,
                 "person_id": int(person_indices[index]) + 1 if index < assigned_count else None,
                 "bbox": [0, 0, 10, 10], "thumbnail_path": "synthetic.jpg",
                 "det_score": 0.95, "frontality": 0.8}
                for index in range(cls.FACE_COUNT)
            ])
            connection.exec_driver_sql(
                "INSERT INTO face_embeddings(face_id, person_id, embedding) VALUES (?, ?, ?)",
                [(index + 1,
                  int(person_indices[index]) + 1 if index < assigned_count else -1,
                  vector.tobytes()) for index, vector in enumerate(vectors)],
            )

    def test_profile_30000_faces_loads_prototypes_once_and_batches_scoring(self):
        with Session(self.engine) as session:
            profiler = cProfile.Profile()
            with (
                patch.object(face_api, "load_prototype_index", wraps=face_api.load_prototype_index) as load,
                patch.object(face_api, "score_faces", wraps=face_api.score_faces) as score,
            ):
                profiler.enable()
                result = face_api.get_orphan_face_suggestions(
                    session=session, cursor=None, limit=48, min_score=0.0,
                )
                profiler.disable()
            self.assertEqual(len(result.items), 48)
            self.assertIsNotNone(result.next_cursor)
            load.assert_called_once_with(session)
            score.assert_called_once()
            self.assertEqual(
                len(score.call_args.args[1]),
                self.FACE_COUNT - self.PERSON_COUNT * self.ASSIGNED_PER_PERSON,
            )
            self.assertEqual(
                [(item.score, item.face.id) for item in result.items],
                sorted(((item.score, item.face.id) for item in result.items),
                       key=lambda item: (-item[0], item[1])),
            )
            report = io.StringIO()
            pstats.Stats(profiler, stream=report).strip_dirs().sort_stats("cumulative").print_stats(16)
            print("\nOrphan suggestions synthetic profile: 30,000 faces, 100 people, 28,000 orphans")
            print(report.getvalue())


if __name__ == "__main__":
    unittest.main()
