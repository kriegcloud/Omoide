"""Ordinary face operations retain exact outcomes across lost responses."""
import importlib
from uuid import uuid4
from unittest.mock import patch

from fastapi import HTTPException
from sqlmodel import select

from round6_support import DatabaseCase
from app.models import Face, MutationOperation, Person
from app.schemas.face import FaceAssign

api = importlib.import_module('app.api.face')


class FaceOperationReceiptTests(DatabaseCase):
    def test_assign_exact_members_prior_ownership_and_replay(self):
        previous, target = self.person(), self.person()
        face, unchanged = self.face(person=previous), self.face(person=target)
        fid, same, target_id, previous_id = face.id, unchanged.id, target.id, previous.id
        request = FaceAssign(person_id=target_id, face_ids=[fid, same, 999, fid], operation_id=str(uuid4()))
        first = api._assign_faces(request, self.session)
        self.assertEqual(first['receipt']['applied'], [f'face:{fid}'])
        self.assertEqual(first['receipt']['previous_ownership'], {f'face:{fid}': previous_id})
        self.assertEqual(first['receipt']['skipped'], [
            {'key': f'face:{same}', 'reason': 'already_assigned'}, {'key': 'face:999', 'reason': 'missing'}])
        with patch.object(api, '_assign_face', side_effect=AssertionError('reapplied')):
            self.assertEqual(api._assign_faces(request, self.session), first)
        request.person_id = 999
        with self.assertRaises(HTTPException) as error:
            api._assign_faces(request, self.session)
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(api.get_face_review_operation(request.operation_id, self.session).model_dump(), first['receipt'])

    def test_detach_reports_actual_changes_and_replays(self):
        owned, orphan = self.face(person=self.person()), self.face()
        fid, orphan_id, prior = owned.id, orphan.id, owned.person_id
        op = str(uuid4())
        result = api._detach_faces([fid, orphan_id, 999, fid], self.session, operation_id=op)
        self.assertEqual(result['receipt']['applied'], [f'face:{fid}'])
        self.assertEqual(result['receipt']['previous_ownership'], {f'face:{fid}': prior})
        self.assertEqual([s['reason'] for s in result['receipt']['skipped']], ['already_detached', 'missing'])
        self.assertEqual(api._detach_faces([fid, orphan_id, 999, fid], self.session, operation_id=op), result)
        self.assertEqual(self.session.get(Face, fid).assignment_source, 'detach')

    def test_delete_replay_reports_deleted_members_after_rows_are_gone(self):
        face = self.face(person=self.person())
        fid, prior, op = face.id, face.person_id, str(uuid4())
        result = api.delete_faces([fid, 999, fid], self.session, operation_id=op)
        self.assertEqual(result['receipt']['applied'], [f'face:{fid}'])
        self.assertEqual(result['receipt']['previous_ownership'], {f'face:{fid}': prior})
        self.assertIsNone(self.session.get(Face, fid))
        with patch.object(api, '_delete_face_batch', side_effect=AssertionError('redeleted')):
            self.assertEqual(api.delete_faces([fid, 999, fid], self.session, operation_id=op), result)

    def test_create_person_replay_does_not_create_another_person(self):
        face = self.face()
        fid, op = face.id, str(uuid4())
        first = api._create_person_from_faces([fid, fid, 999], 'New', self.session, operation_id=op)
        self.assertEqual(first['receipt']['applied'], [f'face:{fid}'])
        self.assertEqual(first['receipt']['previous_ownership'], {f'face:{fid}': None})
        second = api._create_person_from_faces([fid, fid, 999], 'New', self.session, operation_id=op)
        self.assertEqual(second, first)
        self.assertEqual(len(self.session.exec(select(Person)).all()), 1)
        self.assertEqual(self.session.get(Face, fid).assignment_source, 'manual')

    def test_failure_after_first_assignment_rolls_back_and_retains_lookup(self):
        old, target = self.person(), self.person()
        first, second = self.face(person=old), self.face(person=old)
        ids, old_id, target_id = [first.id, second.id], old.id, target.id
        op = str(uuid4())
        real_assign = api._assign_face
        def fail_second(session, face, *args):
            # Receipt must already be durable before the first mutation.
            self.assertIsNotNone(session.get(MutationOperation, op))
            if face.id == ids[1]:
                raise RuntimeError('interrupted')
            return real_assign(session, face, *args)
        with patch.object(api, '_assign_face', side_effect=fail_second):
            with self.assertRaises(RuntimeError):
                api._assign_faces(FaceAssign(person_id=target_id, face_ids=ids, operation_id=op), self.session)
        self.assertEqual([self.session.get(Face, i).person_id for i in ids], [old_id, old_id])
        receipt = api.get_face_review_operation(op, self.session)
        self.assertEqual(receipt.applied, [])
        self.assertEqual(receipt.state, 'unknown')
        with self.assertRaises(HTTPException) as error:
            api._assign_faces(FaceAssign(person_id=target_id, face_ids=ids, operation_id=op), self.session)
        self.assertEqual(error.exception.status_code, 409)

    def test_commit_failure_keeps_unknown_without_catalog_changes_or_thumbnail_cleanup(self):
        face = self.face(person=self.person(), thumbnail_path='kept.jpg')
        thumbnail = self.root / 'kept.jpg'
        thumbnail.write_bytes(b'fixture')
        fid, prior, op = face.id, face.person_id, str(uuid4())
        real_commit = api.safe_commit
        calls = 0
        def fail_final(session):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError('commit failed')
            return real_commit(session)
        with patch.object(api, 'safe_commit', side_effect=fail_final):
            with self.assertRaises(RuntimeError):
                api.delete_faces([fid], self.session, operation_id=op)
        self.assertEqual(self.session.get(Face, fid).person_id, prior)
        self.assertTrue(thumbnail.exists())
        receipt = api.get_face_review_operation(op, self.session)
        self.assertEqual(receipt.state, 'unknown')
        self.assertEqual(receipt.applied, [])

    def test_admission_failure_does_not_mutate_faces(self):
        face, target = self.face(), self.person()
        fid, target_id = face.id, target.id
        with patch.object(api, 'safe_commit', side_effect=RuntimeError('admission failed')):
            with patch.object(api, '_assign_face', side_effect=AssertionError('mutation before admission')):
                with self.assertRaises(RuntimeError):
                    api._assign_faces(FaceAssign(person_id=target_id, face_ids=[fid], operation_id=str(uuid4())), self.session)
        self.session.rollback()
        self.assertIsNone(self.session.get(Face, fid).person_id)

    def test_invalid_target_has_definitive_durable_failure(self):
        fid, op = self.face().id, str(uuid4())
        with self.assertRaises(HTTPException) as error:
            api._assign_faces(FaceAssign(person_id=999, face_ids=[fid], operation_id=op), self.session)
        self.assertEqual(error.exception.status_code, 404)
        receipt = api.get_face_review_operation(op, self.session)
        self.assertEqual(receipt.state, 'failed')
        self.assertEqual(receipt.unknown, [])
        self.assertEqual(receipt.failed[0].key, f'face:{fid}')

    def test_empty_person_cleanup_audit_is_preserved_once(self):
        old, target = self.person(), self.person()
        fid, old_id = self.face(person=old).id, old.id
        request = FaceAssign(person_id=target.id, face_ids=[fid], operation_id=str(uuid4()))
        with patch.object(api, 'log_person_deleted') as audit:
            api._assign_faces(request, self.session)
            api._assign_faces(request, self.session)
        self.assertIsNone(self.session.get(Person, old_id))
        audit.assert_called_once()
        self.assertEqual(audit.call_args.kwargs['reason'], 'empty-after-faces-assign')

    def test_changed_ownership_after_admission_is_not_overwritten(self):
        old, target = self.person(), self.person()
        fid, op = self.face(person=old).id, str(uuid4())
        real_commit = api.safe_commit
        calls = 0
        def concurrent_change(session):
            nonlocal calls
            calls += 1
            real_commit(session)
            if calls == 1:
                row = session.get(Face, fid)
                row.person_id = None
                session.add(row)
                real_commit(session)
        with patch.object(api, 'safe_commit', side_effect=concurrent_change):
            with self.assertRaises(HTTPException) as error:
                api._assign_faces(FaceAssign(person_id=target.id, face_ids=[fid], operation_id=op), self.session)
        self.assertEqual(error.exception.status_code, 409)
        self.assertIsNone(self.session.get(Face, fid).person_id)
        receipt = api.get_face_review_operation(op, self.session)
        self.assertEqual(receipt.state, 'failed')
        self.assertEqual(receipt.applied, [])

    def test_lost_final_response_keeps_success_and_replay_does_not_delete_again(self):
        fid, op = self.face(thumbnail_path='removed.jpg').id, str(uuid4())
        thumbnail = self.root / 'removed.jpg'
        thumbnail.write_bytes(b'fixture')
        real_commit = api.safe_commit
        calls = 0
        def lose_response(session):
            nonlocal calls
            calls += 1
            real_commit(session)
            if calls == 2:
                raise RuntimeError('response lost after commit')
        with patch.object(api, 'safe_commit', side_effect=lose_response):
            with self.assertRaises(RuntimeError):
                api.delete_faces([fid], self.session, operation_id=op)
        self.assertIsNone(self.session.get(Face, fid))
        self.assertFalse(thumbnail.exists())
        self.assertEqual(api.get_face_review_operation(op, self.session).state, 'succeeded')
        with patch.object(api, '_delete_face_batch', side_effect=AssertionError('reapplied deletion')):
            result = api.delete_faces([fid], self.session, operation_id=op)
        self.assertEqual(result['receipt']['applied'], [f'face:{fid}'])

    def test_shared_begin_operation_precedes_mutation_and_failed_response_replays(self):
        fid, op = self.face().id, str(uuid4())
        request = FaceAssign(person_id=999, face_ids=[fid], operation_id=op)
        with patch.object(api, 'begin_operation', wraps=api.begin_operation) as begin:
            with self.assertRaises(HTTPException) as first:
                api._assign_faces(request, self.session)
        begin.assert_called_once()
        stored = self.session.get(MutationOperation, op)
        self.assertEqual(stored.status_code, 404)
        self.assertEqual(stored.response, first.exception.detail)
        with patch.object(api, 'begin_operation', side_effect=AssertionError('readmission')):
            with patch.object(api, '_load_faces', side_effect=AssertionError('mutation replay')):
                with self.assertRaises(HTTPException) as second:
                    api._assign_faces(request, self.session)
        self.assertEqual(second.exception.status_code, 404)
        self.assertEqual(second.exception.detail, first.exception.detail)

    def test_all_skipped_assign_and_detach_are_failed_receipts(self):
        target = self.person()
        assigned, orphan = self.face(person=target), self.face()
        cases = [
            lambda: api._assign_faces(FaceAssign(person_id=target.id, face_ids=[assigned.id, 999], operation_id=str(uuid4())), self.session),
            lambda: api._detach_faces([orphan.id], self.session, operation_id=str(uuid4())),
        ]
        for invoke in cases:
            with self.subTest(invoke=invoke):
                result = invoke()
                self.assertEqual(result['receipt']['applied'], [])
                self.assertTrue(result['receipt']['skipped'])
                self.assertEqual(result['receipt']['state'], 'failed')
