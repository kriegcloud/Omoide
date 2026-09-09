import asyncio
import base64
import os
import tempfile
import unittest
from contextlib import ExitStack, nullcontext
from pathlib import Path
from unittest.mock import patch

import numpy as np
from fastapi import HTTPException
from sqlmodel import Session, SQLModel, create_engine, select, text


_CONFIG_HOME = tempfile.TemporaryDirectory()
os.environ["XDG_CONFIG_HOME"] = _CONFIG_HOME.name

import app.api  # noqa: E402, F401  # Bootstrap tasks in application import order.
from app.api.face import (  # noqa: E402
    assign_faces,
    assign_suggested_faces,
    get_orphan_face_suggestions,
    router,
)
from app.config import (  # noqa: E402
    FACE_RECOGNITION_PRESETS,
    FaceRecognitionSettings,
    settings,
)
from app.database import _attach_engine_listeners  # noqa: E402
from app.models import Face, Media, Person, PersonMediaLink, ProcessingTask  # noqa: E402
from app.schemas.face import AssignSuggestedFaces, FaceAssign  # noqa: E402
from app.services.face_matching import (  # noqa: E402
    PrototypeIndex,
    load_prototype_index,
    match_faces_to_persons,
    score_faces,
)
from app.services.task_summary import summarize_task  # noqa: E402
from app.tasks import media_processing  # noqa: E402
from app.utils import recalculate_person_appearance_counts, vector_to_blob  # noqa: E402


def embedding(first=1.0, second=0.0):
    vector = np.zeros(512, dtype=np.float32)
    vector[:3] = [first, second, np.sqrt(max(0.0, 1 - first**2 - second**2))]
    return vector


class FaceScoringTests(unittest.TestCase):
    def test_margin_ignores_other_prototypes_of_the_winning_person(self):
        index = PrototypeIndex(
            np.array([1, 1, 2]),
            np.vstack([embedding(), embedding(0.99), embedding(0.6)]),
            {},
        )
        match = score_faces(index, [9], embedding().reshape(1, -1), [0.1])[0]
        self.assertEqual((match.face_id, match.person_id, match.pose_bin), (9, 1, "profile"))
        self.assertAlmostEqual(match.score, 1.0)
        self.assertAlmostEqual(match.margin, 0.4)

    def test_pose_boundaries_and_single_person_margin(self):
        index = PrototypeIndex(np.array([1]), embedding().reshape(1, -1), {})
        matches = score_faces(
            index, list(range(6)), np.tile(embedding() * 2, (6, 1)),
            [0.5, 0.25, 0.24, None, float("nan"), 0.8],
        )
        self.assertEqual(
            [match.pose_bin for match in matches],
            ["frontal", "quarter", "profile", "unknown", "unknown", "frontal"],
        )
        self.assertTrue(all(match.margin == 2.0 for match in matches))

    def test_invalid_vectors_and_dimension_mismatch_are_not_scored(self):
        index = PrototypeIndex(np.array([1]), embedding().reshape(1, -1), {})
        vectors = np.vstack([np.zeros(512), np.full(512, np.nan), embedding()])
        original = vectors.copy()
        matches = score_faces(index, [1, 2, 3], vectors, [None] * 3)
        self.assertEqual([match.face_id for match in matches], [3])
        np.testing.assert_array_equal(vectors, original)
        self.assertEqual(score_faces(index, [1], np.ones((1, 3)), [None]), [])
        empty = PrototypeIndex(np.array([]), np.empty((0, 0)), {})
        self.assertEqual(score_faces(empty, [1], embedding()[None, :], [None]), [])

    def test_scoring_crosses_chunk_boundary(self):
        index = PrototypeIndex(np.array([1]), embedding().reshape(1, -1), {})
        matches = score_faces(index, list(range(513)), np.tile(embedding(), (513, 1)), [None] * 513)
        self.assertEqual([match.face_id for match in matches], list(range(513)))


class FaceSuggestionsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.engine = create_engine("sqlite://")
        _attach_engine_listeners(self.engine)
        SQLModel.metadata.create_all(self.engine)
        with self.engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE VIRTUAL TABLE face_embeddings USING vec0("
                "face_id integer primary key, person_id integer, embedding float[512])"
            )
            connection.exec_driver_sql(
                "CREATE VIRTUAL TABLE person_embeddings USING vec0("
                "person_id integer, embedding float[512])"
            )
        self.addCleanup(self.engine.dispose)
        self.session = Session(self.engine)
        self.addCleanup(self.session.close)
        self.person = Person(name="Alice", appearance_count=0)
        self.other = Person(name="Bob", appearance_count=0)
        self.session.add_all([self.person, self.other])
        self.session.flush()
        self.person_id, self.other_id = self.person.id, self.other.id
        self.seed = self.add_face(embedding(), person_id=self.person_id)
        self.other_seed = self.add_face(embedding(0, 1), person_id=self.other_id)
        recalculate_person_appearance_counts(self.session, [self.person_id, self.other_id])
        self.session.commit()

        self.enterContext(patch.object(settings.general, "presentation_mode", False))

    def add_media(self, *, ready=True):
        count = len(list(Path(self.temp.name).iterdir()))
        path = Path(self.temp.name) / f"media-{count}.jpg"
        path.touch()
        media = Media(
            path=str(path), filename=path.name, size=1, extracted_scenes=ready,
            faces_extracted=ready, ran_auto_tagging=ready, embeddings_created=ready,
        )
        self.session.add(media)
        self.session.flush()
        return media.id

    def add_face(self, vector, *, person_id=None, frontality=0.8, media_id=None):
        face = Face(
            media_id=media_id if media_id is not None else self.add_media(),
            person_id=person_id, bbox=[0, 0, 10, 10], thumbnail_path="test-face.jpg",
            frontality=frontality, det_score=0.95,
        )
        self.session.add(face)
        self.session.flush()
        self.session.exec(text(
            "INSERT INTO face_embeddings(face_id, person_id, embedding) VALUES (:id, :pid, :vec)"
        ).bindparams(id=face.id, pid=person_id or -1, vec=vector_to_blob(vector)))
        self.session.commit()
        return face.id

    def suggestions(self, *, cursor=None, limit=48, min_score=0):
        return get_orphan_face_suggestions(
            session=self.session, cursor=cursor, limit=limit, min_score=min_score,
        ).model_dump()

    def assign(self, assignments):
        return asyncio.run(assign_suggested_faces(
            AssignSuggestedFaces.model_validate({"assignments": assignments}),
            session=self.session,
        )).model_dump()

    def test_ranking_score_filter_and_pose(self):
        low = self.add_face(embedding(0.7), frontality=None)
        high = self.add_face(embedding(0.96), frontality=0.1)
        middle = self.add_face(embedding(0, 0.9), frontality=0.3)
        page = self.suggestions()
        self.assertEqual([item["face"]["id"] for item in page["items"]], [high, middle, low])
        self.assertEqual([item["pose_bin"] for item in page["items"]], ["profile", "quarter", "unknown"])
        self.assertEqual([item["person_name"] for item in page["items"]], ["Alice", "Bob", "Alice"])
        self.assertEqual([item["person_id"] for item in page["items"]], [self.person_id, self.other_id, self.person_id])
        self.assertEqual([item["face"]["id"] for item in self.suggestions(min_score=0.8)["items"]], [high, middle])
        self.assertEqual(self.suggestions(min_score=1)["items"], [])
        self.assertIsNone(page["next_cursor"])

    def test_cursor_pages_ties_without_duplicates_and_has_exact_end(self):
        ids = [self.add_face(embedding(0.9)) for _ in range(4)]
        first = self.suggestions(limit=2)
        self.assertEqual(base64.urlsafe_b64decode(first["next_cursor"]), b"2")
        second = self.suggestions(limit=2, cursor=first["next_cursor"])
        self.assertEqual([item["face"]["id"] for item in first["items"] + second["items"]], ids)
        self.assertIsNone(second["next_cursor"])
        self.assertEqual(self.suggestions(cursor="OTk5")["items"], [])

    def test_invalid_cursors_and_route_order(self):
        for cursor in ("!bad", "LTE=", "YWJj", "////"):
            with self.subTest(cursor=cursor), self.assertRaises(HTTPException) as caught:
                self.suggestions(cursor=cursor)
            self.assertEqual(caught.exception.status_code, 400)
        paths = [route.path for route in router.routes]
        self.assertIn("/assign-suggested", paths)
        for path in paths:
            if "{face_id}" in path:
                self.assertLess(paths.index("/orphans/suggestions"), paths.index(path))

    def test_empty_index_and_unnamed_person(self):
        self.person.name = None
        self.session.add(self.person)
        self.session.commit()
        self.add_face(embedding())
        self.assertEqual(self.suggestions()["items"][0]["person_name"], f"Person {self.person_id}")
        self.session.exec(text("DELETE FROM face_embeddings WHERE person_id > 0"))
        self.session.commit()
        self.assertEqual(self.suggestions(), {"items": [], "next_cursor": None})

    def test_assignment_updates_both_tables_centroids_and_distinct_appearance_counts(self):
        media_id = self.add_media()
        faces = [self.add_face(embedding(0.9), media_id=media_id) for _ in range(2)]
        self.session.add(PersonMediaLink(person_id=self.person_id, media_id=media_id))
        self.session.commit()
        response = self.assign([
            {"face_id": face_id, "person_id": self.person_id} for face_id in faces
        ])
        self.assertEqual(response, {"assigned": 2, "skipped": []})
        self.session.expire_all()
        for face_id in faces:
            self.assertEqual(self.session.get(Face, face_id).person_id, self.person_id)
            self.assertEqual(self.session.exec(text(
                "SELECT person_id FROM face_embeddings WHERE face_id = :id"
            ).bindparams(id=face_id)).one()[0], self.person_id)
        self.assertEqual(self.session.get(Person, self.person_id).appearance_count, 2)
        centroid = self.session.exec(text(
            "SELECT embedding FROM person_embeddings WHERE person_id = :pid"
        ).bindparams(pid=self.person_id)).one()[0]
        self.assertGreater(np.frombuffer(centroid, dtype=np.float32)[2], 0)
        self.assertEqual(self.suggestions()["items"], [])

    def test_skipped_reasons_and_repeated_assignment_do_not_reassign(self):
        face_id = self.add_face(embedding())
        untouched = self.add_face(embedding())
        response = self.assign([
            {"face_id": 99999, "person_id": self.person_id},
            {"face_id": self.seed, "person_id": self.other_id},
            {"face_id": untouched, "person_id": 99999},
            {"face_id": face_id, "person_id": self.person_id},
            {"face_id": face_id, "person_id": self.other_id},
        ])
        self.assertEqual(response, {"assigned": 1, "skipped": [
            {"face_id": 99999, "reason": "unknown_face"},
            {"face_id": self.seed, "reason": "face_already_assigned"},
            {"face_id": untouched, "reason": "unknown_person"},
            {"face_id": face_id, "reason": "face_already_assigned"},
        ]})
        self.session.expire_all()
        self.assertIsNone(self.session.get(Face, untouched).person_id)
        self.assertEqual(self.session.get(Face, self.seed).person_id, self.person_id)
        retry = self.assign([
            {"face_id": face_id, "person_id": self.person_id},
        ])
        self.assertEqual(retry["assigned"], 0)

    def test_assign_path_still_supports_manual_reassignment(self):
        face_id = self.add_face(embedding(), person_id=self.person_id)
        response = asyncio.run(assign_faces(
            FaceAssign(person_id=self.other_id, face_ids=[face_id]), session=self.session,
        ))
        self.assertEqual(response, {"message": "Faces assigned successfully"})
        self.session.expire_all()
        self.assertEqual(self.session.get(Face, face_id).person_id, self.other_id)
        self.assertEqual(self.session.get(Person, self.person_id).appearance_count, 1)
        self.assertEqual(self.session.get(Person, self.other_id).appearance_count, 2)

    def test_presentation_mode_rejects_assignment(self):
        face_id = self.add_face(embedding())
        with (
            patch.object(settings.general, "presentation_mode", True),
            self.assertRaises(HTTPException) as caught,
        ):
            self.assign([{"face_id": face_id, "person_id": self.person_id}])
        self.assertEqual(caught.exception.status_code, 403)
        self.assertIsNone(self.session.get(Face, face_id).person_id)

    def test_index_is_rebuilt_after_assignment(self):
        face_id = self.add_face(embedding(0.6))
        before = load_prototype_index(self.session).matrix.copy()
        response = self.assign([
            {"face_id": face_id, "person_id": self.person_id},
        ])
        self.assertEqual(response["assigned"], 1)
        after = load_prototype_index(self.session).matrix
        self.assertFalse(np.array_equal(before, after))

    def test_service_respects_threshold_margin_and_only_requested_orphans(self):
        good = self.add_face(embedding(0.95))
        ambiguous = self.add_face(embedding(0.7, 0.7))
        low = self.add_face(embedding(0.4))
        older = self.add_face(embedding())
        result = match_faces_to_persons(
            self.session, [good, ambiguous, low, self.seed, 99999], threshold=0.6, min_margin=0.05,
        )
        self.assertEqual(result, {good: self.person_id})
        self.assertEqual(self.session.get(Person, self.person_id).appearance_count, 2)
        for face_id in (ambiguous, low, older):
            self.assertIsNone(self.session.get(Face, face_id).person_id)

    def run_index_task(self, *, selected=False, enabled=True, chained=False, reuse_id=False, cancelled=False):
        older = self.add_face(embedding())
        media_id = self.add_media(ready=False)
        replaced = self.add_face(embedding(), media_id=media_id) if reuse_id else None
        task = ProcessingTask(
            task_type="run_processor_for_media" if selected else "process_media",
            result={"existing": "preserved"},
        )
        self.session.add(task)
        self.session.commit()
        task_id = task.id
        created = []

        class FakeFaceProcessor:
            name = "faces"
            active = False
            handles_empty_scenes = False

            def load_model(self):
                self.active = True

            def unload(self):
                pass

            def reset_for_media(self, media, session):
                for face in session.exec(select(Face).where(Face.media_id == media.id, Face.person_id.is_(None))).all():
                    session.exec(text("DELETE FROM face_embeddings WHERE face_id = :id").bindparams(id=face.id))
                    session.delete(face)
                session.flush()

            def process(self, media, session, *, scenes):
                face = Face(media_id=media.id, bbox=[0, 0, 10, 10], thumbnail_path="new.jpg", frontality=0.8)
                session.add(face)
                session.flush()
                session.exec(text(
                    "INSERT INTO face_embeddings(face_id, person_id, embedding) VALUES (:id, -1, :vec)"
                ).bindparams(id=face.id, vec=vector_to_blob(embedding())))
                created.append(face.id)
                media.faces_extracted = True
                media.extracted_scenes = True
                session.add(media)
                return True

        with ExitStack() as stack:
            stack.enter_context(patch.object(media_processing.db, "engine", self.engine))
            stack.enter_context(patch.object(media_processing, "processors", [FakeFaceProcessor()]))
            stack.enter_context(patch.object(media_processing, "heavy_writer", return_value=nullcontext(True)))
            stack.enter_context(patch.object(media_processing, "_get_or_extract_scenes", return_value=[object()]))
            stack.enter_context(patch.object(media_processing, "_is_task_cancelled", side_effect=lambda _: cancelled and bool(created)))
            stack.enter_context(patch.object(media_processing, "set_task_progress"))
            stack.enter_context(patch.object(media_processing, "clear_task_progress"))
            stack.enter_context(patch.object(settings.face_recognition, "match_new_faces_on_index", enabled))
            stack.enter_context(patch.object(settings.face_recognition, "existing_person_cosine_threshold", 0.62))
            stack.enter_context(patch.object(settings.face_recognition, "existing_person_min_cosine_margin", 0.05))
            if selected:
                media_processing.run_processors_for_media(task_id, ["faces"], [media_id])
            else:
                media_processing._run_media_processing(task_id, clustering_chained=chained)
        self.session.expire_all()
        task = self.session.get(ProcessingTask, task_id)
        self.assertEqual(task.result["existing"], "preserved")
        self.assertIsNone(self.session.get(Face, older).person_id)
        should_match = enabled and not chained and not cancelled
        self.assertEqual(task.result["faces_matched"], int(should_match))
        self.assertEqual(len(created), 1)
        self.assertEqual(self.session.get(Face, created[0]).person_id, self.person_id if should_match else None)
        if should_match:
            self.assertIn("1 face matched to people", summarize_task(task))
            self.assertEqual(self.session.get(Person, self.person_id).appearance_count, 2)
        if reuse_id:
            self.assertEqual(created[0], replaced)

    def test_non_chained_scan_matches_new_faces_and_records_result(self):
        self.run_index_task()

    def test_selected_processor_matches_recreated_face_with_reused_id(self):
        self.run_index_task(selected=True, reuse_id=True)

    def test_disabled_config_does_not_match_on_either_path(self):
        for selected in (False, True):
            with self.subTest(selected=selected):
                self.run_index_task(selected=selected, enabled=False)

    def test_chained_processing_does_not_match_before_clustering(self):
        self.run_index_task(chained=True)

    def test_cancelled_selected_processing_does_not_match(self):
        self.run_index_task(selected=True, cancelled=True)

    def test_chain_wrapper_passes_explicit_skip_flag(self):
        with (
            patch.object(media_processing.db, "engine", self.engine),
            patch.object(settings.general, "enable_people", True),
            patch.object(settings.scan, "auto_cluster_on_scan", True),
            patch.object(media_processing, "run_media_processing") as process,
            patch("app.tasks.person_clustering.run_person_clustering") as cluster,
        ):
            media_processing.run_media_processing_and_chain("test-chain")
        process.assert_called_once_with("test-chain", clustering_chained=True)
        cluster.assert_called_once()

    def test_match_on_index_defaults_and_presets(self):
        self.assertTrue(FaceRecognitionSettings().match_new_faces_on_index)
        for preset in FACE_RECOGNITION_PRESETS:
            self.assertTrue(FaceRecognitionSettings(preset=preset).match_new_faces_on_index)
        self.assertFalse(FaceRecognitionSettings(match_new_faces_on_index=False).match_new_faces_on_index)

    def test_task_summary_pluralizes_matches(self):
        task = ProcessingTask(task_type="process_media", status="completed", processed=1, total=1, result={"faces_matched": 2})
        self.assertEqual(summarize_task(task), "1 file processed, 2 faces matched to people")


if __name__ == "__main__":
    unittest.main()
