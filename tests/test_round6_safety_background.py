"""Regression cases for background safety audit findings (real temporary SQLite)."""
import importlib
import os
import tempfile
import unittest
from contextlib import nullcontext
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import numpy as np
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlmodel import Session, SQLModel, create_engine, select

_CONFIG_HOME = tempfile.TemporaryDirectory()
os.environ.setdefault("XDG_CONFIG_HOME", _CONFIG_HOME.name)
importlib.import_module("app.api.media")
from app import concurrency, database
from app.config import DuplicateHandlingRule, settings
from app.models import (Blacklist, DuplicateIgnore, DuplicateMedia, Event,
                        EventMediaLink, Face, FaceAssignmentSource, Media, Person,
                        ProcessingTask, Scene)
from app.processors.duplicates import DuplicateProcessor
from app.processors.embedding_extractor import EmbeddingExtractor
from app.processors.faces import FaceProcessor
from app.services.face_matching import _bulk_assign_faces_to_persons
from app.services.face_provenance import stamp_face_assignment
from app.tasks import duplicates, organize, person_clustering, scan


class BackgroundSafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.engine = create_engine(f"sqlite:///{self.root / 'test.sqlite'}")
        database._attach_engine_listeners(self.engine)
        SQLModel.metadata.create_all(self.engine)
        with self.engine.begin() as c:
            c.exec_driver_sql("CREATE VIRTUAL TABLE face_embeddings USING vec0(face_id integer primary key, person_id integer, embedding float[512])")
            c.exec_driver_sql("CREATE VIRTUAL TABLE person_embeddings USING vec0(person_id integer primary key, embedding float[512])")
        self.addCleanup(self.engine.dispose)
        self.session = Session(self.engine)
        self.addCleanup(self.session.close)
        self.enterContext(patch.object(database, "engine", self.engine))
        self.enterContext(patch.object(settings.general, "presentation_mode", False))
        self.vector = np.zeros(512, dtype=np.float32)
        self.vector[0] = 1
        self.counter = 0

    def media(self, **kwargs):
        self.counter += 1
        path = self.root / f"photo-{self.counter}.jpg"
        path.touch()
        row = Media(path=str(path), filename=path.name, size=10, **kwargs)
        self.session.add(row)
        self.session.commit()
        return row

    def person(self, **kwargs):
        row = Person(appearance_count=kwargs.pop("appearance_count", 0), **kwargs)
        self.session.add(row)
        self.session.commit()
        return row

    def face(self, person_id=None):
        media = self.media()
        face = Face(media_id=media.id, bbox=[0, 0, 10, 10], person_id=person_id)
        if person_id is not None:
            face.person_id = None
            stamp_face_assignment(face, person_id, FaceAssignmentSource.MANUAL)
        self.session.add(face)
        self.session.flush()
        self.session.exec(text("INSERT INTO face_embeddings(face_id,person_id,embedding) VALUES (:fid,:pid,:vec)").bindparams(fid=face.id, pid=person_id or -1, vec=self.vector.tobytes()))
        self.session.commit()
        return face

    def task(self, kind, status="pending"):
        row = ProcessingTask(task_type=kind, status=status)
        self.session.add(row)
        self.session.commit()
        return row.id

    def status(self, task_id):
        self.session.expire_all()
        return self.session.get(ProcessingTask, task_id).status

    def test_locked_vec_statement_does_not_retry_after_discarding_face_assignment(self):
        target = self.person()
        face = self.face()
        fid, pid = face.id, target.id
        stamp_face_assignment(face, pid, FaceAssignmentSource.AUTO_MATCH)
        self.session.flush()
        real_exec = self.session.exec
        attempts = 0
        def locked_once(statement, *args, **kwargs):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise OperationalError("UPDATE face_embeddings", {}, Exception("database is locked"))
            return real_exec(statement, *args, **kwargs)
        with patch.object(self.session, "exec", side_effect=locked_once):
            with self.assertRaises(OperationalError):
                database.safe_execute(self.session, text("UPDATE face_embeddings SET person_id=:pid WHERE face_id=:fid").bindparams(pid=pid, fid=fid), delay=0)
        self.session.commit()
        self.assertEqual(attempts, 1)
        self.session.expire_all()
        self.assertIsNone(self.session.get(Face, fid).person_id)
        self.assertEqual(real_exec(text("SELECT person_id FROM face_embeddings WHERE face_id=:fid").bindparams(fid=fid)).one()[0], -1)

    def test_auto_match_only_claims_orphans_and_reports_actual_claims(self):
        manual, target = self.person(), self.person()
        assigned, orphan = self.face(manual.id), self.face()
        assigned_id, orphan_id, manual_id, target_id = assigned.id, orphan.id, manual.id, target.id
        before = assigned.assigned_at
        claimed = _bulk_assign_faces_to_persons(self.session, {assigned_id: target_id, orphan_id: target_id})
        self.session.commit()
        self.session.refresh(assigned)
        self.assertEqual((assigned.person_id, assigned.assignment_source, assigned.assigned_at), (manual_id, "manual", before))
        self.assertEqual(claimed, {orphan_id: target_id})
        self.assertEqual(self.session.exec(text("SELECT person_id FROM face_embeddings WHERE face_id=:fid").bindparams(fid=assigned_id)).one()[0], manual_id)

    def test_matching_does_not_count_new_manual_assignment_as_auto_match(self):
        manual, target = self.person(), self.person()
        face = self.face(manual.id)
        remaining = person_clustering._match_unassigned_to_existing(
            self.session, [face.id], self.vector.reshape(1, -1), "no-task",
            prototypes=(np.array([target.id]), self.vector.reshape(1, -1)),
        )
        self.assertEqual(remaining, [face.id])
        self.assertEqual(self.session.get(Face, face.id).person_id, manual.id)

    def test_cluster_excludes_new_manual_assignment_from_count_and_centroid(self):
        manual = self.person()
        assigned = self.face(manual.id)
        manual_id, assigned_id = manual.id, assigned.id
        count = max(settings.face_recognition.person_min_face_count, settings.face_recognition.person_min_media_count)
        orphans = [self.face().id for _ in range(count)]
        self.session.close()
        manual_embedding = self.vector.copy()
        manual_embedding[:2] = [0.98, np.sqrt(1 - 0.98 ** 2)]
        created, pids = person_clustering._assign_faces_to_clusters({0: ([assigned_id, *orphans], [manual_embedding] + [self.vector.copy() for _ in range(count)])}, "no-task", update_progress=False)
        self.assertEqual(created, 1)
        self.assertEqual(self.session.get(Face, assigned_id).person_id, manual_id)
        self.assertEqual(self.session.get(Person, pids[0]).appearance_count, count)
        self.assertNotEqual(self.session.get(Person, pids[0]).profile_face_id, assigned_id)
        centroid = self.session.exec(text("SELECT embedding FROM person_embeddings WHERE person_id=:pid").bindparams(pid=pids[0])).one()[0]
        np.testing.assert_array_equal(np.frombuffer(centroid, dtype=np.float32), self.vector)

    def test_cluster_rolls_back_person_if_claims_fall_below_minimum(self):
        manual = self.person()
        assigned = self.face(manual.id)
        count = max(settings.face_recognition.person_min_face_count, settings.face_recognition.person_min_media_count)
        ids = [assigned.id] + [self.face().id for _ in range(count - 1)]
        self.session.close()
        created, pids = person_clustering._assign_faces_to_clusters({0: (ids, [self.vector.copy() for _ in ids])}, "no-task", update_progress=False)
        self.assertEqual((created, pids), (0, []))
        self.assertEqual(len(self.session.exec(select(Person)).all()), 1)
        self.assertTrue(all(self.session.get(Face, fid).person_id is None for fid in ids[1:]))

    def test_drop_keep_merge_preserves_faces_manually_moved_before_merge(self):
        keep = self.person(appearance_count=2)
        drop = self.person(appearance_count=1)
        manual = self.person()
        keep_id, drop_id, manual_id = keep.id, drop.id, manual.id
        moved = self.face(drop_id)
        self.face(keep_id)
        protected = self.face(drop_id)
        stamp_face_assignment(protected, manual_id, FaceAssignmentSource.MANUAL)
        self.session.exec(text("UPDATE face_embeddings SET person_id=:pid WHERE face_id=:fid").bindparams(pid=manual_id, fid=protected.id))
        self.session.commit()
        protected_id, moved_id = protected.id, moved.id
        before = protected.assigned_at
        self.assertEqual(person_clustering._merge_person_pair(self.session, keep_id, drop_id), (keep_id, drop_id))
        self.session.expire_all()
        protected = self.session.get(Face, protected_id)
        self.assertEqual((protected.person_id, protected.assignment_source, protected.assigned_at), (manual_id, "manual", before))
        self.assertEqual(self.session.exec(text("SELECT person_id FROM face_embeddings WHERE face_id=:fid").bindparams(fid=protected_id)).one()[0], manual_id)
        self.assertEqual(self.session.get(Face, moved_id).person_id, keep_id)

    def test_clustering_cancel_query_recognizes_sqlalchemy_rows(self):
        for status in ("cancelled", "interrupted"):
            task_id = self.task("cluster_persons", status)
            self.assertTrue(person_clustering._is_task_cancelled(self.session, task_id))

    def test_pre_cancelled_clustering_task_never_restarts(self):
        task_id = self.task("cluster_persons", "cancelled")
        with patch.object(person_clustering, "_fetch_faces_and_embeddings", return_value=([], np.empty((0, 512)))) as fetch, patch.object(person_clustering, "merge_similar_persons", return_value=0), patch.object(person_clustering, "_match_remaining_single_faces", return_value=(0, 0, False)), patch.object(person_clustering, "rebuild_person_relationships"):
            person_clustering.run_person_clustering(task_id)
        fetch.assert_not_called()
        self.assertEqual(self.status(task_id), "cancelled")

    def test_clustering_preserves_interruption_during_final_matching(self):
        task_id = self.task("cluster_persons")
        def interrupt(*_, **kwargs):
            with Session(self.engine) as session:
                task = session.get(ProcessingTask, task_id)
                task.status = "interrupted"
                session.commit()
            return 0, 0, False
        with patch.object(person_clustering, "_fetch_faces_and_embeddings", return_value=([], np.empty((0, 512)))), patch.object(person_clustering, "merge_similar_persons", return_value=0), patch.object(person_clustering, "_match_remaining_single_faces", side_effect=interrupt), patch.object(person_clustering, "rebuild_person_relationships") as rebuild:
            person_clustering.run_person_clustering(task_id)
        self.assertEqual(self.status(task_id), "interrupted")
        rebuild.assert_not_called()

    def test_clustering_denied_writer_preserves_interrupted_status(self):
        task_id = self.task("cluster_persons")
        def deny(**kwargs):
            with Session(self.engine) as session:
                task = session.get(ProcessingTask, task_id)
                task.status = "interrupted"
                session.commit()
            return nullcontext(False)
        with patch.object(person_clustering, "heavy_writer", side_effect=deny):
            person_clustering.run_person_clustering(task_id)
        self.assertEqual(self.status(task_id), "interrupted")

    def test_face_read_error_retains_media_and_records_processing_error(self):
        media = self.media()
        with patch("app.processors.faces.Image.open", side_effect=OSError("temporary read failure")), patch("app.processors.faces.delete_media_record", create=True) as delete:
            result = FaceProcessor().process(media, self.session, [Scene(media_id=media.id, thumbnail_path="bad.jpg", start_time=0, end_time=1)])
        self.assertFalse(result)
        delete.assert_not_called()
        self.session.refresh(media)
        self.assertIn("read", media.processing_error.lower())
        self.assertFalse(media.faces_extracted)

    def test_embedding_read_error_retains_media_and_records_processing_error(self):
        media = self.media()
        processor = EmbeddingExtractor()
        with patch.object(processor, "_get_embeddings_batch", return_value=[None]), patch("app.processors.embedding_extractor.delete_media_record", create=True) as delete:
            result = processor.process(media, self.session, [(Scene(media_id=media.id, start_time=0, end_time=1), np.zeros((1, 1, 3)))])
        self.assertFalse(result)
        delete.assert_not_called()
        self.session.refresh(media)
        self.assertTrue(media.processing_error)
        self.assertFalse(media.embeddings_created)

    def test_duplicate_ignore_cannot_be_bridged_by_third_identical_item(self):
        groups = DuplicateProcessor("none")._partition_non_ignored_groups([1, 2, 3], {(1, 2)})
        self.assertTrue(groups)
        self.assertTrue(all(not {1, 2}.issubset(group) for group in groups), groups)

    def test_near_duplicate_ignore_cannot_be_bridged(self):
        a, b, c = [self.media(duration=1, phash=h) for h in ("0000000000000000", "0000000000000003", "0000000000000001")]
        aid, bid = a.id, b.id
        self.session.add(DuplicateIgnore(media_id_a=aid, media_id_b=bid))
        self.session.commit()
        task_id = self.task("find_duplicates")
        with patch.object(settings.duplicates, "duplicate_auto_handling", DuplicateHandlingRule.KEEP):
            DuplicateProcessor(task_id, 1).process()
        self.session.expire_all()
        groups = {}
        for row in self.session.exec(select(DuplicateMedia)).all():
            groups.setdefault(row.group_id, set()).add(row.media_id)
        self.assertTrue(all(not {aid, bid}.issubset(group) for group in groups.values()), groups)

    def test_existing_duplicate_groups_cannot_bridge_ignored_pair(self):
        a, b, c = self.media(), self.media(), self.media()
        processor = DuplicateProcessor("none")
        processor._create_or_update_group(self.session, [a.id, c.id])
        self.session.add(DuplicateIgnore(media_id_a=a.id, media_id_b=b.id))
        self.session.commit()
        processor._create_or_update_group(self.session, [b.id, c.id])
        groups = {}
        for row in self.session.exec(select(DuplicateMedia)).all():
            groups.setdefault(row.group_id, set()).add(row.media_id)
        self.assertTrue(all(not {a.id, b.id}.issubset(group) for group in groups.values()), groups)

    def test_auto_resolution_rechecks_saved_ignored_decisions(self):
        a, b = self.media(), self.media()
        self.session.add(DuplicateIgnore(media_id_a=a.id, media_id_b=b.id))
        self.session.commit()
        processor = DuplicateProcessor("no-task")
        with patch.object(settings.duplicates, "duplicate_auto_handling", DuplicateHandlingRule.DELETE), patch.object(processor, "_apply_duplicate_action") as action:
            processor._auto_resolve_media_items(self.session, [a, b])
        action.assert_not_called()

    def test_cancelled_hashing_does_not_start_duplicate_processing(self):
        task_id = self.task("find_duplicates")
        def cancel(_):
            with Session(self.engine) as s:
                task = s.get(ProcessingTask, task_id)
                task.status = "cancelled"
                s.commit()
        with patch.object(duplicates, "generate_hashes", side_effect=cancel), patch.object(duplicates, "DuplicateProcessor") as processor:
            duplicates.run_duplicate_detection(task_id, 1)
        processor.assert_not_called()
        self.assertEqual(self.status(task_id), "cancelled")

    def test_pre_cancelled_duplicate_task_never_restarts(self):
        task_id = self.task("find_duplicates", "cancelled")
        with patch.object(duplicates, "generate_hashes") as hashes, patch.object(duplicates, "DuplicateProcessor"):
            duplicates.run_duplicate_detection(task_id, 1)
        hashes.assert_not_called()
        self.assertEqual(self.status(task_id), "cancelled")

    def scan_setup(self):
        self.enterContext(patch.object(type(settings.general), "resolved_media_dirs", return_value=[(self.root, False)]))
        self.enterContext(patch.object(scan, "generate_thumbnail", return_value=("thumb.jpg", None)))

    def test_scan_skips_normalized_blacklist_during_discovery(self):
        self.scan_setup()
        path = self.root / "#_% 🧑‍💻.jpg"
        path.touch()
        self.session.add(Blacklist(path=str(self.root / "sub" / ".." / path.name)))
        self.session.commit()
        task_id = self.task("scan")
        with patch.object(scan, "process_file", return_value=(None, None, "unexpected")) as process:
            scan.run_scan(task_id)
        process.assert_not_called()

    def test_scan_rechecks_blacklist_before_insert(self):
        self.scan_setup()
        path = self.root / "new.jpg"
        path.touch()
        task_id = self.task("scan")
        def process(filepath):
            with Session(self.engine) as s:
                s.add(Blacklist(path=str(filepath)))
                s.commit()
            return Media(path=str(filepath), filename=filepath.name, size=1), None, None
        with patch.object(scan, "process_file", side_effect=process):
            scan.run_scan(task_id)
        self.session.expire_all()
        self.assertEqual(self.session.exec(select(Media)).all(), [])

    def test_scan_does_not_complete_interrupted_task_after_processing(self):
        self.scan_setup()
        (self.root / "new.jpg").touch()
        task_id = self.task("scan")
        def process(filepath):
            with Session(self.engine) as s:
                task = s.get(ProcessingTask, task_id)
                task.status = "interrupted"
                s.commit()
            return Media(path=str(filepath), filename=filepath.name, size=1), None, None
        with patch.object(scan, "process_file", side_effect=process):
            scan.run_scan(task_id)
        self.assertEqual(self.status(task_id), "interrupted")

    def test_scan_empty_discovery_preserves_interrupted_status(self):
        self.scan_setup()
        task_id = self.task("scan")
        def interrupt(*args, **kwargs):
            with Session(self.engine) as session:
                task = session.get(ProcessingTask, task_id)
                task.status = "interrupted"
                session.commit()
            return iter(())
        with patch.object(scan, "_walk_media_candidates", side_effect=interrupt):
            scan.run_scan(task_id)
        self.assertEqual(self.status(task_id), "interrupted")

    def seed_events(self, count=3):
        self.enterContext(patch.object(settings.events, "event_min_media", 1))
        self.enterContext(patch.object(settings.events, "preserve_renamed_on_rebuild", True))
        media = [self.media(created_at=datetime(2020, 1, 1) + timedelta(days=i * 30)) for i in range(count)]
        event = Event(title="My custom memory", title_is_custom=True, start_at=media[0].created_at, end_at=media[-1].created_at, media_count=count, cover_media_id=media[0].id)
        self.session.add(event)
        self.session.flush()
        self.session.add_all([EventMediaLink(event_id=event.id, media_id=m.id) for m in media])
        self.session.commit()
        return event.id

    def test_cancelled_event_rebuild_preserves_previous_generation(self):
        event_id = self.seed_events()
        task_id = self.task("build_events")
        with patch.object(organize, "heavy_writer", return_value=nullcontext(True)), patch.object(organize, "_is_cancelled", return_value=True):
            organize.run_build_events(task_id)
        self.session.expire_all()
        self.assertEqual([(e.id, e.title) for e in self.session.exec(select(Event)).all()], [(event_id, "My custom memory")])
        self.assertEqual(len(self.session.exec(select(EventMediaLink)).all()), 3)

    def test_failed_event_rebuild_preserves_previous_generation_after_fifty_clusters(self):
        event_id = self.seed_events(52)
        task_id = self.task("build_events")
        calls = 0
        def title(*_):
            nonlocal calls
            calls += 1
            if calls == 52:
                raise RuntimeError("interrupted generation")
            return None
        with patch.object(organize, "_event_title", side_effect=title):
            try:
                organize.run_build_events(task_id)
            except RuntimeError:
                pass
        self.session.expire_all()
        self.assertEqual([(e.id, e.title) for e in self.session.exec(select(Event)).all()], [(event_id, "My custom memory")])
        self.assertEqual(len(self.session.exec(select(EventMediaLink)).all()), 52)

    def test_successful_event_rebuild_preserves_custom_titles_and_memberships(self):
        self.seed_events()
        task_id = self.task("build_events")
        organize.run_build_events(task_id)
        self.session.expire_all()
        events = self.session.exec(select(Event)).all()
        self.assertEqual(len(events), 3)
        self.assertTrue(all(e.title == "My custom memory" and e.title_is_custom for e in events))
        self.assertEqual(len(self.session.exec(select(EventMediaLink)).all()), 3)
        self.assertEqual(self.status(task_id), "completed")

    def test_interruption_during_event_generation_preserves_original(self):
        event_id = self.seed_events(52)
        task_id = self.task("build_events")
        calls = 0
        def interrupt_after_fifty(*_):
            nonlocal calls
            calls += 1
            if calls == 51:
                with Session(self.engine) as session:
                    task = session.get(ProcessingTask, task_id)
                    task.status = "interrupted"
                    session.commit()
            return None
        with patch.object(organize, "_event_title", side_effect=interrupt_after_fifty):
            organize.run_build_events(task_id)
        self.session.expire_all()
        self.assertEqual([(e.id, e.title) for e in self.session.exec(select(Event)).all()], [(event_id, "My custom memory")])
        self.assertEqual(len(self.session.exec(select(EventMediaLink)).all()), 52)
        self.assertEqual(self.status(task_id), "interrupted")

    def test_geocode_worker_cannot_run_without_writer_admission(self):
        from types import SimpleNamespace
        task_id = self.task("geocode_places", "cancelled")
        (self.root / "rg_cities1000.csv").touch()
        with patch.dict("sys.modules", {"reverse_geocoder": SimpleNamespace(__file__=str(self.root / "rg.py"))}), patch.object(organize, "heavy_writer", return_value=nullcontext(False)), patch.object(organize, "_start_task") as start:
            organize.run_geocode_places(task_id)
        start.assert_not_called()
        self.assertEqual(self.status(task_id), "cancelled")

    def test_event_worker_cannot_run_without_writer_admission(self):
        self.seed_events()
        task_id = self.task("build_events", "cancelled")
        with patch.object(organize, "heavy_writer", return_value=nullcontext(False)), patch.object(organize, "_event_title") as title:
            organize.run_build_events(task_id)
        title.assert_not_called()
        self.assertEqual(self.status(task_id), "cancelled")

    def test_lock_released_if_post_acquire_cancellation_check_errors(self):
        from unittest.mock import Mock
        lock = Mock()
        lock.acquire.return_value = True
        cancelled = Mock(side_effect=[False, RuntimeError("status unavailable")])
        with patch.object(concurrency, "_heavy_write_lock", lock):
            with self.assertRaisesRegex(RuntimeError, "status unavailable"):
                concurrency.acquire_heavy_write_lock(cancelled=cancelled)
        lock.release.assert_called_once_with()

    def test_cancelled_lock_waiter_cannot_acquire_free_lock(self):
        acquired = concurrency.acquire_heavy_write_lock(cancelled=lambda: True, poll_seconds=0)
        if acquired:
            concurrency._heavy_write_lock.release()
        self.assertFalse(acquired)


if __name__ == "__main__":
    unittest.main()
