"""Manual face review: synthetic vectors only; never touches the library."""
import time
import unittest
import numpy as np
import test_face_suggestions as fixtures
from app.api.face import FaceIgnoreRequest, ignore_faces, unignore_faces, get_ignored, get_orphan_count, get_orphans, accept_face_group, FaceGroupAccept
from app.services.face_review import build_groups, get_groups
from app.models import Face
from app.services.face_matching import load_unassigned_face_embeddings, _bulk_assign_faces_to_persons
from app.tasks.clustering_queries import fetch_faces_and_embeddings, get_face_total
from fastapi import HTTPException


class FaceReviewTests(unittest.TestCase):
    setUp = fixtures.FaceSuggestionsTests.setUp
    add_face = fixtures.FaceSuggestionsTests.add_face
    add_media = fixtures.FaceSuggestionsTests.add_media
    suggestions = fixtures.FaceSuggestionsTests.suggestions

    def test_ignore_receipt_exclusions_and_restore(self):
        fid = self.add_face(fixtures.embedding())
        receipt = ignore_faces(FaceIgnoreRequest(face_ids=[fid, 999999]), self.session)
        self.assertEqual(receipt.state, 'partial')
        self.assertEqual(receipt.applied, [f'face:{fid}'])
        self.assertEqual(receipt.skipped[0].key, 'face:999999')
        self.assertEqual(get_orphan_count(self.session), {'count': 0})
        self.assertEqual(get_orphans(self.session, None, 48).items, [])
        self.assertEqual(self.suggestions()['items'], [])
        self.assertEqual(load_unassigned_face_embeddings(self.session)[0], [])
        self.assertEqual(fetch_faces_and_embeddings(self.session)[0], [])
        self.assertEqual(get_face_total(self.session), 0)
        self.assertEqual(_bulk_assign_faces_to_persons(self.session, {fid: self.person_id}), {})
        self.assertEqual(get_ignored(self.session, None, 48).items[0].id, fid)
        restored = unignore_faces(FaceIgnoreRequest(select_all=True), self.session)
        self.assertEqual(restored.applied, [f'face:{fid}'])
        self.assertEqual(get_orphan_count(self.session), {'count': 1})

    def test_assigned_ignore_rejected_atomically(self):
        fid = self.add_face(fixtures.embedding())
        with self.assertRaises(HTTPException) as caught:
            ignore_faces(FaceIgnoreRequest(face_ids=[fid, self.seed]), self.session)
        self.assertEqual(caught.exception.status_code, 400)
        self.assertEqual(caught.exception.detail['receipt']['state'], 'failed')
        self.assertEqual({item['key'] for item in caught.exception.detail['receipt']['failed']}, {f'face:{fid}', f'face:{self.seed}'})
        self.session.expire_all()
        self.assertIsNone(self.session.get(Face, fid).ignored_at)

    def test_near_match_fields_and_distinct_person_margin(self):
        fid = self.add_face(fixtures.embedding(.9))
        self.add_face(fixtures.embedding(), person_id=self.person_id)
        item = self.suggestions()['items'][0]
        self.assertEqual(item['face']['id'], fid)
        self.assertEqual(item['best_matching_face_id'], self.seed)
        self.assertAlmostEqual(item['best_cos'], .9, places=5)
        self.assertAlmostEqual(item['margin'], item['best_cos'] - item['runner_up_person_cos'])

    def test_groups_cache_and_manual_accept(self):
        ids = [self.add_face(fixtures.embedding(), frontality=.01) for _ in range(4)]
        first = get_groups(self.session)
        self.assertEqual(first[0]['face_ids'], ids)
        self.assertEqual(first[0]['candidate']['person_id'], self.person_id)
        ignore_faces(FaceIgnoreRequest(face_ids=[ids[0]]), self.session)
        self.assertEqual(get_groups(self.session), [])
        unignore_faces(FaceIgnoreRequest(face_ids=[ids[0]]), self.session)
        receipt = accept_face_group(FaceGroupAccept(face_ids=ids, new_person_name='Reviewed'), self.session)
        self.assertEqual(receipt.state, 'succeeded')
        self.session.expire_all()
        for fid in ids:
            self.assertEqual(self.session.get(Face, fid).assignment_source, 'suggestion')
        self.assertEqual(get_groups(self.session), [])

    def test_receipt_conflict_rolls_back_and_durable_lookup(self):
        from app.services.mutation_operations import lookup_operation
        fid = self.add_face(fixtures.embedding())
        body = FaceIgnoreRequest(face_ids=[fid])
        receipt = ignore_faces(body, self.session)
        self.assertEqual(lookup_operation(self.session, receipt.operation_id), receipt)
        with self.assertRaises(HTTPException) as caught:
            unignore_faces(body, self.session)
        self.assertEqual(caught.exception.status_code, 409)
        self.session.expire_all()
        self.assertIsNotNone(self.session.get(Face, fid).ignored_at)

    def test_group_accept_rejects_owned_or_ignored_without_overwrite(self):
        fid = self.add_face(fixtures.embedding())
        for ids in ([fid, self.seed], [fid, 999999]):
            with self.assertRaises(HTTPException):
                accept_face_group(FaceGroupAccept(face_ids=ids, person_id=self.other_id), self.session)
            self.assertIsNone(self.session.get(Face, fid).person_id)
        ignore_faces(FaceIgnoreRequest(face_ids=[fid]), self.session)
        with self.assertRaises(HTTPException):
            accept_face_group(FaceGroupAccept(face_ids=[fid], person_id=self.other_id), self.session)
        self.assertIsNone(self.session.get(Face, fid).person_id)

    def test_ignored_pagination_and_select_all(self):
        ids = [self.add_face(fixtures.embedding()) for _ in range(3)]
        ignore_faces(FaceIgnoreRequest(select_all=True), self.session)
        first = get_ignored(self.session, None, 2)
        second = get_ignored(self.session, first.next_cursor, 2)
        self.assertEqual([f.id for f in first.items+second.items], ids[::-1])
        self.assertIsNone(second.next_cursor)
        self.assertTrue(all(f.ignored_at is not None for f in first.items))
        # An ignored face remains queryable on its media through the Face row.
        self.assertIsNotNone(self.session.get(Face, ids[0]))

    def test_groups_pagination_invalid_cursor_and_sync_worker_routes(self):
        import inspect
        from app.api.face import get_orphan_groups, router
        for _ in range(4):
            self.add_face(fixtures.embedding())
        for _ in range(4):
            self.add_face(fixtures.embedding(0,1))
        first = get_orphan_groups(self.session, None, 1)
        second = get_orphan_groups(self.session, first['next_cursor'], 1)
        self.assertEqual(len(first['items'])+len(second['items']), 2)
        self.assertIsNone(second['next_cursor'])
        with self.assertRaises(HTTPException):
            get_orphan_groups(self.session, 'LTE=', 1)
        for route in router.routes:
            if route.path in ('/ignore', '/unignore', '/ignored', '/orphans/groups', '/orphans/groups/accept', '/orphans/suggestions'):
                self.assertFalse(inspect.iscoroutinefunction(route.endpoint))

    def test_ignored_is_excluded_from_person_suggestions_and_prototypes(self):
        from app.api.person.appearances import suggest_faces
        from app.services.face_matching import load_prototype_index
        from app.schemas.face import AssignSuggestedFaces
        from app.api.face import _assign_suggested_faces
        fid = self.add_face(fixtures.embedding(.95))
        ignore_faces(FaceIgnoreRequest(face_ids=[fid]), self.session)
        result = suggest_faces(self.person_id, 100, self.session)
        self.assertNotIn(fid, [face.id for face in result])
        skipped = _assign_suggested_faces(AssignSuggestedFaces(assignments=[{'face_id':fid, 'person_id':self.person_id}]), self.session)
        self.assertEqual(skipped.skipped[0].reason, 'face_ignored')
        # Defensive selection also excludes invalid legacy assigned+ignored rows.
        self.session.get(Face, self.seed).ignored_at = self.session.get(Face, fid).ignored_at
        self.session.commit()
        self.assertNotIn(self.person_id, load_prototype_index(self.session).person_ids)

    def test_group_cache_hits_and_assigned_candidate_invalidation(self):
        from unittest.mock import patch
        import app.services.face_review as review
        for _ in range(4):
            self.add_face(fixtures.embedding())
        get_groups(self.session)
        with patch.object(review, 'build_groups', wraps=review.build_groups) as build:
            get_groups(self.session)
            self.assertEqual(build.call_count, 0)
            self.session.get(Face, self.seed).person_id = self.other_id
            self.session.commit()
            get_groups(self.session)
            self.assertEqual(build.call_count, 1)

    def test_near_match_floor_order_and_exact_margin(self):
        low = self.add_face(fixtures.embedding(.47))
        high = self.add_face(fixtures.embedding(.95))
        middle = self.add_face(fixtures.embedding(.8))
        items = self.suggestions()['items']
        self.assertEqual([item['face']['id'] for item in items], [high,middle])
        self.assertNotIn(low, [item['face']['id'] for item in items])
        self.assertAlmostEqual(items[0]['runner_up_person_cos'], 0., places=5)
        self.assertAlmostEqual(items[0]['margin'], .95, places=5)


class GroupVectorTests(unittest.TestCase):
    def test_two_tight_groups_noise_and_bridge(self):
        rng = np.random.default_rng(42)
        vectors = rng.normal(size=(30, 512)).astype('float32')
        vectors[:5] = np.eye(1, 512, 0) + .008 * vectors[:5]
        vectors[5:11] = np.eye(1, 512, 1) + .008 * vectors[5:11]
        groups = build_groups(vectors)
        self.assertEqual([g['indices'] for g in groups], [list(range(5,11)), list(range(5))])
        for group in groups:
            self.assertGreaterEqual(group['mean_cos'], .70)
        # A cosine-connected chain must not survive with incoherent members.
        angles = np.linspace(0, np.pi, 10)
        chain = np.zeros((10, 512), dtype='float32')
        chain[:, 0], chain[:, 1] = np.cos(angles), np.sin(angles)
        for group in build_groups(chain):
            members = chain[group['indices']]
            means = ((members @ members.T).sum(axis=1)-1)/(len(members)-1)
            self.assertTrue(np.all(means >= .70-1e-6))

    def test_twenty_thousand_vectors_under_three_seconds(self):
        rng = np.random.default_rng(24)
        vectors = rng.normal(size=(20000,512)).astype('float32')
        vectors[:4] = vectors[0]
        started = time.perf_counter()
        result = build_groups(vectors)
        elapsed = time.perf_counter()-started
        print(f'face groups 20000x512: {elapsed:.3f}s')
        self.assertLess(elapsed, 3.)
        self.assertEqual(result[0]['indices'], [0,1,2,3])

class IgnoreMigrationTests(unittest.TestCase):
    def test_upgrade_index_generation_and_downgrade(self):
        from alembic.config import Config
        from alembic.script import ScriptDirectory
        from alembic.operations import Operations
        from alembic.migration import MigrationContext
        from sqlalchemy import inspect
        from sqlmodel import create_engine
        script = ScriptDirectory.from_config(Config('alembic.ini'))
        self.assertEqual(script.get_heads(), ['b8c9d0e1f203'])
        revision = script.get_revision('f0611a9c2e74')
        self.assertEqual(revision.down_revision, '598e24827831')
        engine = create_engine('sqlite://')
        self.addCleanup(engine.dispose)
        with engine.begin() as connection:
            connection.exec_driver_sql('CREATE TABLE face (id INTEGER PRIMARY KEY, person_id INTEGER)')
            connection.exec_driver_sql('INSERT INTO face VALUES (1, NULL), (2, 7)')
            with Operations.context(MigrationContext.configure(connection)):
                revision.module.upgrade()
                self.assertIn('ix_face_ignored_at', {i['name'] for i in inspect(connection).get_indexes('face')})
                self.assertEqual(connection.exec_driver_sql('SELECT ignored_at FROM face').all(), [(None,), (None,)])
                for value in ("'2026-11-02'", 'NULL'):
                    connection.exec_driver_sql(f'UPDATE face SET ignored_at={value} WHERE id=1')
                self.assertEqual(connection.exec_driver_sql('SELECT ignored_generation FROM face_review_state').scalar(), 2)
                revision.module.downgrade()
                self.assertEqual(connection.exec_driver_sql('SELECT * FROM face').all(), [(1,None), (2,7)])
                self.assertEqual({c['name'] for c in inspect(connection).get_columns('face')}, {'id','person_id'})
