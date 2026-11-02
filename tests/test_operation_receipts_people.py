"""Durable people mutations use real SQLite and direct endpoint calls."""
import json
from uuid import uuid4
from unittest.mock import patch

from fastapi import HTTPException
from sqlmodel import Session
from round6_support import DatabaseCase
from app.models import Person
from app.schemas.person import PersonBulkHideRequest, PersonBulkDeleteRequest, MergePersonsRequest, PersonUpdate
from app.services.mutation_operations import lookup_operation


def body(result):
    return json.loads(result.body)


class PeopleOperationReceiptTests(DatabaseCase):
    def test_bulk_hide_exact_subset_and_replay_without_reapply(self):
        from app.api.person.extensions import hide_persons_bulk, unhide_person
        person = self.person()
        pid, oid = person.id, str(uuid4())
        request = PersonBulkHideRequest(person_ids=[pid, pid, 9999], operation_id=oid)
        first = body(hide_persons_bulk(request, self.session))
        self.assertEqual(first['receipt']['applied'], [f'person:{pid}'])
        self.assertEqual([s['key'] for s in first['receipt']['skipped']], ['person:9999'])
        unhide_person(pid, self.session)
        self.assertEqual(body(hide_persons_bulk(request, self.session)), first)
        self.assertIsNone(self.session.get(Person, pid).hidden_at)
        with self.assertRaises(HTTPException) as error:
            hide_persons_bulk(PersonBulkHideRequest(person_ids=[pid], operation_id=oid), self.session)
        self.assertEqual(error.exception.status_code, 409)

    def test_delete_receipt_survives_lost_response_and_preserves_provenance(self):
        from app.api.person.core import delete_person
        person = self.person()
        face = self.face(person=person)
        pid, fid, oid = person.id, face.id, str(uuid4())
        first = body(delete_person(pid, self.session, operation_id=oid))
        self.assertEqual(first['receipt']['applied'], [f'person:{pid}'])
        self.assertEqual(body(delete_person(pid, self.session, operation_id=oid)), first)
        self.session.refresh(face)
        self.assertIsNone(face.person_id)
        self.assertEqual(face.assignment_source, 'detach')
        self.assertIsNotNone(face.assigned_at)
        with Session(self.engine) as reader:
            self.assertEqual(lookup_operation(reader, oid).applied, [f'person:{pid}'])

    def test_profile_rejected_face_has_failed_receipt(self):
        from app.api.person.core import update_person
        person, other = self.person(), self.person()
        face = self.face(person=other)
        oid = str(uuid4())
        result = body(update_person(person.id, PersonUpdate(profile_face_id=face.id, operation_id=oid), self.session))
        self.assertEqual(result['receipt']['state'], 'failed')
        self.assertEqual(result['receipt']['applied'], [])
        self.assertEqual(result['receipt']['failed'][0]['key'], f'person:{person.id}')
        self.assertIsNone(self.session.get(Person, person.id).profile_face_id)

    def test_merge_retry_does_not_merge_new_source(self):
        from app.api.person.merging import merge_persons
        source, target = self.person(), self.person()
        sid, tid, oid = source.id, target.id, str(uuid4())
        request = MergePersonsRequest(source_id=sid, target_id=tid, operation_id=oid)
        first = body(merge_persons(request, self.session))
        self.assertEqual(first['receipt']['applied'], [f'person:{sid}'])
        self.assertEqual(body(merge_persons(request, self.session)), first)

    def test_bulk_delete_committed_prefix_is_exact_on_failure(self):
        from app.api.person.core import delete_persons_bulk
        from app.utils import remove_person
        first, second = self.person(), self.person()
        ids, oid = [first.id, second.id], str(uuid4())
        def remove(pid, session, **kwargs):
            if pid == ids[1]:
                raise HTTPException(409, 'blocked fixture')
            return remove_person(pid, session, **kwargs)
        with patch('app.api.person.core.remove_person', side_effect=remove):
            result = body(delete_persons_bulk(PersonBulkDeleteRequest(person_ids=ids, operation_id=oid), self.session))
        self.assertEqual(result['receipt']['applied'], [f'person:{ids[0]}'])
        self.assertEqual([s['key'] for s in result['receipt']['failed']], [f'person:{ids[1]}'])
        self.assertEqual(result['receipt']['state'], 'partial')

    def test_committed_hide_remains_applied_if_response_building_crashes(self):
        from app.api.person.extensions import hide_person
        person = self.person()
        pid, oid = person.id, str(uuid4())
        with patch('app.api.person.core.get_person', side_effect=RuntimeError('lost response')):
            with self.assertRaisesRegex(RuntimeError, 'lost response'):
                hide_person(pid, self.session, operation_id=oid)
        with Session(self.engine) as reader:
            self.assertEqual(lookup_operation(reader, oid).applied, [f'person:{pid}'])
            self.assertEqual(lookup_operation(reader, oid).unknown, [])
        with self.assertRaises(HTTPException) as error:
            hide_person(pid, self.session, operation_id=oid)
        self.assertEqual(error.exception.status_code, 409)

    def test_bulk_unhide_and_profile_changes_are_checkpointed(self):
        from app.api.person.extensions import hide_person, unhide_persons_bulk, set_profile_face
        person = self.person()
        face = self.face(person=person)
        pid, fid = person.id, face.id
        hide_person(pid, self.session)
        result = body(unhide_persons_bulk(PersonBulkHideRequest(person_ids=[pid], operation_id=str(uuid4())), self.session))
        self.assertEqual(result['receipt']['applied'], [f'person:{pid}'])
        result = body(set_profile_face(pid, fid, self.session, operation_id=str(uuid4())))
        self.assertEqual(result['receipt']['applied'], [f'person:{pid}'])
        self.assertEqual(result['profile_face_id'], fid)
        recovered = lookup_operation(self.session, result['receipt']['operation_id'])
        self.assertEqual(recovered.response['profile_face_id'], fid)

    def test_merge_multiple_actual_skipped_sources(self):
        from app.api.person.merging import merge_multiple_persons
        from app.schemas.person import MergePersonsBulkRequest
        source, target = self.person(), self.person()
        sid, tid = source.id, target.id
        result = body(merge_multiple_persons(tid, MergePersonsBulkRequest(
            source_ids=[sid, tid, 9999], operation_id=str(uuid4())), self.session))
        self.assertEqual(result['receipt']['applied'], [f'person:{sid}'])
        self.assertEqual([item['key'] for item in result['receipt']['skipped']], [f'person:{tid}', 'person:9999'])

    def test_lookup_returns_stored_terminal_receipt(self):
        from app.api.person.core import get_person_operation
        from app.api.person.extensions import hide_person
        person, oid = self.person(), str(uuid4())
        result = body(hide_person(person.id, self.session, operation_id=oid))
        self.assertEqual(get_person_operation(oid, self.session).model_dump(), result['receipt'])
        self.assertEqual(result['receipt']['response']['id'], person.id)

    def test_empty_selection_has_terminal_receipt(self):
        from app.api.person.extensions import hide_persons_bulk
        result = body(hide_persons_bulk(PersonBulkHideRequest(person_ids=[], operation_id=str(uuid4())), self.session))
        self.assertEqual(result['receipt']['state'], 'succeeded')
        self.assertEqual(result['receipt']['applied'], [])

    def test_bulk_delete_unexpected_member_failure_stays_unknown(self):
        from app.api.person.core import delete_persons_bulk
        person = self.person()
        pid = person.id
        with patch('app.api.person.core.remove_person', side_effect=RuntimeError('uncertain fixture')):
            result = body(delete_persons_bulk(PersonBulkDeleteRequest(
                person_ids=[pid], operation_id=str(uuid4())), self.session))
        self.assertEqual(result['receipt']['state'], 'unknown')
        self.assertEqual(result['receipt']['unknown'], [f'person:{pid}'])
        self.assertEqual(result['receipt']['skipped'], [])

    def test_favorite_uuid_body_returns_receipt_and_stored_response(self):
        from app.api.person.core import update_person_favorite
        from app.schemas.media import FavoriteUpdate
        person = self.person()
        request = FavoriteUpdate(is_favorite=True, operation_id=str(uuid4()))
        result = body(update_person_favorite(person.id, request, self.session))
        self.assertEqual(result['receipt']['applied'], [f'person:{person.id}'])
        self.assertTrue(result['is_favorite'])
        self.assertEqual(body(update_person_favorite(person.id, request, self.session)), result)

    def test_domain_and_checkpoint_roll_back_together(self):
        from sqlalchemy import event
        from app.api.person.extensions import hide_person
        person = self.person()
        pid, oid = person.id, str(uuid4())
        def fail_domain_flush(session, context):
            if any(isinstance(row, Person) for row in session.dirty):
                raise RuntimeError('catalog flush fixture')
        event.listen(self.session, 'after_flush', fail_domain_flush)
        try:
            with self.assertRaisesRegex(RuntimeError, 'catalog flush fixture'):
                hide_person(pid, self.session, operation_id=oid)
        finally:
            event.remove(self.session, 'after_flush', fail_domain_flush)
        with Session(self.engine) as reader:
            self.assertIsNone(reader.get(Person, pid).hidden_at)
            receipt = lookup_operation(reader, oid)
            self.assertEqual(receipt.applied, [])
            self.assertEqual(receipt.unknown, [f'person:{pid}'])

    def test_auto_merge_freezes_candidates_before_intent_and_never_reenumerates_on_retry(self):
        from app.api.person.merging import auto_merge_similar_persons
        from app.schemas.person import SimilarPerson
        source, target = self.person(), self.person()
        sid, tid, oid = source.id, target.id, str(uuid4())
        with patch('app.api.person.merging.get_similarities', return_value=[
            SimilarPerson(id=sid, name='source', similarity=100.0)
        ]) as candidates:
            result = body(auto_merge_similar_persons(tid, self.session, operation_id=oid))
            self.assertEqual(candidates.call_count, 1)
        self.assertEqual(result['receipt']['applied'], [f'person:{sid}'])
        with patch('app.api.person.merging.get_similarities', side_effect=AssertionError('must not rediscover')):
            self.assertEqual(body(auto_merge_similar_persons(tid, self.session, operation_id=oid)), result)

    def test_bulk_terminal_rejection_does_not_call_unattempted_members_failed(self):
        from app.api.person.core import delete_persons_bulk
        first, second = self.person(), self.person()
        ids = [first.id, second.id]
        with patch('app.api.person.core.remove_person', side_effect=HTTPException(403, 'fixture denial')):
            result = body(delete_persons_bulk(PersonBulkDeleteRequest(
                person_ids=ids, operation_id=str(uuid4())), self.session))
        self.assertEqual([item['key'] for item in result['receipt']['failed']], [f'person:{ids[0]}'])
        self.assertEqual(result['receipt']['skipped'], [{'key': f'person:{ids[1]}', 'reason': 'not_attempted'}])
