"""Durable synchronous duplicate protocol, using disposable SQLite fixtures."""
from datetime import datetime
from pathlib import Path
from uuid import uuid4
from unittest.mock import patch

from fastapi import HTTPException
from sqlmodel import Session

from round6_support import DatabaseCase
import importlib
duplicates = importlib.import_module('app.api.duplicates')
from app.models import AnnotationAttempt, AnnotationAttemptStatus, AnnotationKind, DuplicateGroup, DuplicateMedia, Media
from app.schemas.duplicates import ResolveDuplicatesRequest


class DuplicateReceiptTests(DatabaseCase):
    def group(self, count=3, missing=False):
        rows = [self.media() for _ in range(count)]
        if missing:
            rows[-1].missing_since = datetime.now()
        group = DuplicateGroup()
        self.session.add(group)
        self.session.commit()
        ids = [row.id for row in rows]
        self.session.add_all([DuplicateMedia(group_id=group.id, media_id=i) for i in ids])
        self.session.commit()
        return group.id, ids

    def request(self, group_id, ids, action="DELETE_RECORDS", operation_id=None):
        return ResolveDuplicatesRequest(group_id=group_id, master_media_id=ids[0], action=action,
                                        operation_id=operation_id or str(uuid4()))

    def lookup(self, request):
        with Session(self.engine) as reader:
            return duplicates.get_mutation_operation(str(request.operation_id), reader).model_dump()

    def test_partial_prefix_commit_and_annotation_rejection(self):
        group, ids = self.group()
        self.session.add(AnnotationAttempt(media_id=ids[2], kind=AnnotationKind.TAGS, profile_id="fixture", external_prompt_id=str(uuid4()),
                                           status=AnnotationAttemptStatus.RUNNING, active_slot=1))
        self.session.commit()
        request = self.request(group, ids)
        with self.assertRaises(HTTPException) as error:
            duplicates.resolve_duplicate_group(request, self.session)
        receipt = self.lookup(request)
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(error.exception.headers['X-Mutation-Operation-ID'], str(request.operation_id))
        self.assertEqual(receipt['state'], 'partial')
        self.assertEqual(receipt['applied'], [f'media:{ids[1]}'])
        self.assertEqual(receipt['failed'][0]['key'], f'media:{ids[2]}')
        self.assertEqual(receipt['failed'][0]['code'], 'annotation_active')
        self.assertIsNone(self.session.get(Media, ids[1]))
        self.assertIsNotNone(self.session.get(Media, ids[2]))

    def test_read_only_member_partial_receipt(self):
        group, ids = self.group()
        request = self.request(group, ids, 'DELETE_FILES')
        from app.utils import delete_file
        def delete(session, media_id):
            if media_id == ids[2]:
                raise HTTPException(403, 'read-only fixture')
            return delete_file(session, media_id)
        with patch('app.config.GeneralSettings.ensure_media_path_writable'), patch.object(duplicates, 'delete_file', side_effect=delete):
            with self.assertRaises(HTTPException) as error:
                duplicates.resolve_duplicate_group(request, self.session)
        receipt = self.lookup(request)
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(receipt['applied'], [f'media:{ids[1]}'])
        self.assertEqual(receipt['failed'][0]['key'], f'media:{ids[2]}')

    def test_missing_member_is_preserved_and_reported(self):
        group, ids = self.group(missing=True)
        request = self.request(group, ids)
        result = duplicates.resolve_duplicate_group(request, self.session)
        receipt = result['receipt']
        self.assertEqual(receipt['skipped'], [{'key': f'media:{ids[2]}', 'reason': 'missing'}])
        self.assertEqual(receipt['applied'], [f'media:{ids[1]}'])
        self.assertIsNotNone(self.session.get(Media, ids[2]))

    def test_lost_response_replay_does_not_delete_again(self):
        group, ids = self.group(2)
        request = self.request(group, ids)
        first = duplicates.resolve_duplicate_group(request, self.session)
        with patch.object(duplicates, 'delete_record', side_effect=AssertionError('must not replay')):
            second = duplicates.resolve_duplicate_group(request, self.session)
        self.assertEqual(first, second)
        self.assertEqual(self.lookup(request), first['receipt'])
        changed = self.request(group, ids, 'DELETE_FILES', request.operation_id)
        with self.assertRaises(HTTPException) as error:
            duplicates.resolve_duplicate_group(changed, self.session)
        self.assertEqual(error.exception.status_code, 409)

    def test_unlink_failure_is_definitive_and_preserves_record(self):
        group, ids = self.group(2)
        original = Path(self.session.get(Media, ids[1]).path)
        original.touch()
        request = self.request(group, ids, 'DELETE_FILES')
        with patch('app.config.GeneralSettings.ensure_media_path_writable'), patch.object(Path, 'unlink', side_effect=OSError('fixture unlink denied')):
            with self.assertRaises(HTTPException):
                duplicates.resolve_duplicate_group(request, self.session)
        receipt = self.lookup(request)
        self.assertEqual(receipt['state'], 'failed')
        self.assertEqual(receipt['unknown'], [])
        self.assertTrue(original.exists())
        self.assertIsNotNone(self.session.get(Media, ids[1]))

    def test_catalog_commit_failure_after_unlink_is_unknown(self):
        group, ids = self.group(2)
        original = Path(self.session.get(Media, ids[1]).path)
        original.touch()
        request = self.request(group, ids, 'DELETE_FILES')
        with patch('app.config.GeneralSettings.ensure_media_path_writable'), patch('app.utils.safe_commit', side_effect=RuntimeError('fixture commit failed')):
            with self.assertRaises(HTTPException) as error:
                duplicates.resolve_duplicate_group(request, self.session)
        receipt = self.lookup(request)
        self.assertEqual(error.exception.status_code, 500)
        self.assertEqual(receipt['state'], 'unknown')
        self.assertEqual(receipt['unknown'], [f'media:{ids[1]}'])
        self.assertEqual(receipt['applied'], [])
        self.assertFalse(original.exists())
        self.assertIsNotNone(self.session.get(Media, ids[1]))

    def test_membership_change_after_admission_is_rejected_without_deleting_new_member(self):
        group, ids = self.group(2)
        request = self.request(group, ids)
        original = duplicates.begin_operation
        new_id = None
        def admit(*args, **kwargs):
            nonlocal new_id
            operation = original(*args, **kwargs)
            new_id = self.media().id
            self.session.add(DuplicateMedia(group_id=group, media_id=new_id))
            self.session.commit()
            return operation
        with patch.object(duplicates, 'begin_operation', side_effect=admit):
            with self.assertRaises(HTTPException) as error:
                duplicates.resolve_duplicate_group(request, self.session)
        self.assertEqual(error.exception.status_code, 409)
        self.assertIsNotNone(self.session.get(Media, new_id))
        self.assertIsNotNone(self.session.get(Media, ids[1]))

    def test_unattempted_suffix_is_reported_separately_from_failed_member(self):
        group, ids = self.group(4)
        request = self.request(group, ids)
        with patch.object(duplicates, 'delete_record', side_effect=HTTPException(409, 'fixture refusal')):
            with self.assertRaises(HTTPException):
                duplicates.resolve_duplicate_group(request, self.session)
        receipt = self.lookup(request)
        self.assertEqual(receipt['failed'][0]['key'], f'media:{ids[1]}')
        self.assertEqual(receipt['skipped'], [
            {'key': f'media:{media_id}', 'reason': 'not_attempted'} for media_id in ids[2:]])
