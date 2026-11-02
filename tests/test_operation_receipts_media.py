"""Media mutations have durable, replayable per-member outcomes."""
from datetime import datetime
from uuid import uuid4
from unittest.mock import patch
from fastapi import HTTPException
from round6_support import DatabaseCase
from app.api.media import extensions, core
from app.models import Media
from app.schemas.media import MediaBulkDeleteRequest, MediaBulkMoveRequest, FavoriteUpdate
from app.services.mutation_operations import lookup_operation


class MediaReceiptTests(DatabaseCase):
    def test_bulk_delete_replay_and_missing_id(self):
        media = self.media()
        mid = media.id
        operation_id = str(uuid4())
        request = MediaBulkDeleteRequest(media_ids=[mid, 999999], action='DELETE_RECORDS', operation_id=operation_id)
        first = extensions.bulk_delete_media(request, self.session)
        self.assertEqual(first['receipt']['applied'], [f'media:{mid}'])
        self.assertEqual(first['receipt']['skipped'], [{'key': 'media:999999', 'reason': 'not_selected'}])
        with patch('app.api._resolve.delete_record', side_effect=AssertionError('reapplied')):
            self.assertEqual(extensions.bulk_delete_media(request, self.session), first)
        self.assertIsNone(self.session.get(Media, mid))
        changed = request.model_copy(update={'action': 'DELETE_FILES'})
        with self.assertRaises(HTTPException) as error:
            extensions.bulk_delete_media(changed, self.session)
        self.assertEqual(error.exception.status_code, 409)

    def test_favorite_replay_does_not_overwrite_later_change(self):
        media = self.media()
        request = FavoriteUpdate(is_favorite=True, operation_id=str(uuid4()))
        first = core.update_favorite(media.id, request, self.session)
        self.assertEqual(first['receipt']['applied'], [f'media:{media.id}'])
        media.is_favorite = False
        self.session.add(media)
        self.session.commit()
        self.assertEqual(core.update_favorite(media.id, request, self.session), first)
        self.assertFalse(self.session.get(Media, media.id).is_favorite)

    def test_bulk_move_missing_preserved(self):
        media = self.media(missing_since=datetime.now())
        request = MediaBulkMoveRequest(media_ids=[media.id, 999999], destination_dir='x', operation_id=str(uuid4()))
        result = extensions.bulk_move_media(request, self.session)
        self.assertEqual(result['receipt']['applied'], [])
        self.assertEqual(len(result['receipt']['skipped']), 2)
        self.assertEqual(lookup_operation(self.session, str(request.operation_id)).state, 'failed')

    def test_bulk_delete_partial_failure_has_exact_member_receipt(self):
        first, second = self.media(), self.media()
        ids = [first.id, second.id]
        request = MediaBulkDeleteRequest(media_ids=ids, action='DELETE_RECORDS', operation_id=str(uuid4()))
        from app.records import delete_record
        def delete(mid, session):
            if mid == ids[1]:
                raise HTTPException(409, {'code': 'fixture_rejection', 'message': 'refused'})
            return delete_record(mid, session)
        with patch('app.api._resolve.delete_record', side_effect=delete):
            result = extensions.bulk_delete_media(request, self.session)
        self.assertEqual(result['receipt']['state'], 'partial')
        self.assertEqual(result['receipt']['applied'], [f'media:{ids[0]}'])
        self.assertEqual(result['receipt']['failed'][0]['key'], f'media:{ids[1]}')
        self.assertEqual(result['receipt']['failed'][0]['code'], 'fixture_rejection')
        self.assertEqual(result['receipt']['unknown'], [])

    def test_move_replay_never_moves_file_again(self):
        from app.schemas.media import MediaMoveRequest
        media = self.media()
        media_id = media.id
        target = self.root / 'moved.jpg'
        request = MediaMoveRequest(destination_dir='target', operation_id=str(uuid4()))
        with patch.object(extensions, '_move_media', return_value=target) as move:
            first = extensions.move_media(media_id, request, self.session)
            self.assertEqual(first['receipt']['applied'], [f'media:{media_id}'])
            second = extensions.move_media(media_id, request, self.session)
            self.assertEqual(first, second)
            self.assertEqual(move.call_count, 1)
        self.assertEqual(self.session.get(Media, media_id).path, str(target))

    def test_failed_catalog_commit_is_unknown_and_retry_does_not_apply(self):
        media = self.media()
        request = FavoriteUpdate(is_favorite=True, operation_id=str(uuid4()))
        with patch.object(core, 'safe_commit', side_effect=RuntimeError('fixture commit failure')):
            with self.assertRaises(HTTPException):
                core.update_favorite(media.id, request, self.session)
        receipt = lookup_operation(self.session, str(request.operation_id))
        self.assertEqual(receipt.state, 'unknown')
        self.assertEqual(receipt.unknown, [f'media:{media.id}'])
        self.assertFalse(self.session.get(Media, media.id).is_favorite)
        with patch.object(core, 'safe_commit', side_effect=AssertionError('reapplied')):
            with self.assertRaises(HTTPException):
                core.update_favorite(media.id, request, self.session)

    def test_media_tag_and_single_delete_receipts_and_replay(self):
        import importlib
        tags = importlib.import_module("app.api.tags")
        from app.models import Tag, MediaTagLink
        from fastapi import Response
        media = self.media()
        media_id = media.id
        tag = Tag(name='fixture')
        self.session.add(tag)
        self.session.commit()
        tag_id = tag.id
        operation_id = uuid4()
        response = Response()
        tags.add_tag_to_media(media_id, tag_id, self.session, operation_id, response)
        self.assertEqual(response.headers['X-Mutation-Operation-URL'], f'/api/media/operations/{operation_id}')
        self.assertEqual(lookup_operation(self.session, str(operation_id)).applied, [f'media:{media_id}'])
        tags.remove_tag_from_media(media_id, tag_id, self.session)
        tags.add_tag_to_media(media_id, tag_id, self.session, operation_id)
        self.assertIsNone(self.session.get(MediaTagLink, (media_id, tag_id)))
        tags.add_tag_to_media(media_id, tag_id, self.session)
        remove_id = uuid4()
        tags.remove_tag_from_media(media_id, tag_id, self.session, remove_id)
        self.assertEqual(lookup_operation(self.session, str(remove_id)).applied, [f'media:{media_id}'])
        tags.add_tag_to_media(media_id, tag_id, self.session)
        tags.remove_tag_from_media(media_id, tag_id, self.session, remove_id)
        self.assertIsNotNone(self.session.get(MediaTagLink, (media_id, tag_id)))
        delete_id = uuid4()
        core.delete_media_record(media_id, self.session, delete_id)
        core.delete_media_record(media_id, self.session, delete_id)
        self.assertEqual(lookup_operation(self.session, str(delete_id)).applied, [f'media:{media_id}'])

    def test_unknown_operation_lookup_is_404(self):
        with self.assertRaises(HTTPException) as error:
            extensions.get_mutation_operation(str(uuid4()), self.session)
        self.assertEqual(error.exception.status_code, 404)

    def test_move_commit_succeeded_but_transport_failed_does_not_reverse_file(self):
        from app.schemas.media import MediaMoveRequest
        media = self.media()
        media_id = media.id
        target = self.root / 'moved.jpg'
        request = MediaMoveRequest(destination_dir='target', operation_id=str(uuid4()))
        def commit_then_fail(session):
            session.commit()
            raise RuntimeError('lost commit acknowledgement')
        with patch.object(extensions, '_move_media', return_value=target), \
                patch.object(extensions, 'safe_commit', side_effect=commit_then_fail), \
                patch('app.services.media_files.rollback_media_moves') as rollback:
            with self.assertRaises(HTTPException):
                extensions.move_media(media_id, request, self.session)
            rollback.assert_not_called()
        self.assertEqual(self.session.get(Media, media_id).path, str(target))
        receipt = lookup_operation(self.session, str(request.operation_id))
        self.assertEqual(receipt.applied, [f'media:{media_id}'])
        self.assertEqual(receipt.unknown, [])
