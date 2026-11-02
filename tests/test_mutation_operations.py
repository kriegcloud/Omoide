from uuid import uuid4
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import true
from sqlmodel import Session

from round6_support import DatabaseCase
from app.api._resolve import resolve_media_action


class MutationOperationTests(DatabaseCase):
    def test_interrupted_attempt_is_unknown_and_never_replayed(self):
        from app.services.mutation_operations import begin_operation, lookup_operation
        operation_id = str(uuid4())
        operation = begin_operation(self.session, operation_id, {'action': 'DELETE_FILES'}, [42])
        operation.start_member(42)
        # Simulate loss of the owning process after durable intent, before a checkpoint.
        self.session.close()
        with Session(self.engine) as reader:
            receipt = lookup_operation(reader, operation_id)
            self.assertEqual(receipt.state, 'unknown')
            self.assertEqual(receipt.unknown, ['media:42'])
            with self.assertRaises(HTTPException) as error:
                begin_operation(reader, operation_id, {'action': 'DELETE_FILES'}, [42])
            self.assertEqual(error.exception.status_code, 409)

    def test_review_receipt_tracks_actual_subset_and_missing_ids(self):
        from app.services.mutation_operations import lookup_operation
        first, second = self.media(), self.media()
        ids = [first.id, second.id]
        from app.utils import delete_record
        def delete(media_id, session):
            if media_id == ids[1]:
                raise HTTPException(409, {'code': 'annotation_active', 'message': 'fixture'})
            return delete_record(media_id, session)
        with patch('app.api._resolve.delete_record', side_effect=delete):
            result = resolve_media_action(self.session, action='DELETE_RECORDS', media_ids=[*ids, 9999],
                                          select_all=False, base_filter=true(), operation_id=str(uuid4()))
        self.assertEqual(result['processed_ids'], [ids[0]])
        self.assertEqual(result['receipt']['applied'], [f'media:{ids[0]}'])
        self.assertEqual(result['receipt']['state'], 'partial')
        self.assertEqual(result['receipt']['skipped'], [{'key': 'media:9999', 'reason': 'not_selected'}])
        self.assertEqual(lookup_operation(self.session, result['receipt']['operation_id']).model_dump(), result['receipt'])

    def test_checkpoint_is_atomic_with_catalog_commit(self):
        from app.services.mutation_operations import begin_operation, lookup_operation
        from app.utils import delete_record
        row = self.media()
        media_id = row.id
        operation_id = str(uuid4())
        operation = begin_operation(self.session, operation_id, {'action': 'DELETE_RECORDS'}, [media_id])
        operation.start_member(media_id)
        with operation.member(media_id):
            delete_record(media_id, self.session)
        # No terminal response needed to prove that this member committed.
        with Session(self.engine) as reader:
            receipt = lookup_operation(reader, operation_id)
            self.assertEqual(receipt.applied, [f'media:{media_id}'])
            self.assertEqual(receipt.unknown, [])

    def test_size_sorted_duplicates_use_one_group_aggregate(self):
        from sqlalchemy import event
        from app.api.duplicates import get_duplicates
        from app.models import DuplicateGroup, DuplicateMedia
        group = DuplicateGroup()
        self.session.add(group)
        first, second = self.media(size=100), self.media(size=200)
        self.session.add_all([DuplicateMedia(group_id=group.id, media_id=row.id) for row in (first, second)])
        self.session.commit()
        statements = []
        def capture(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)
        event.listen(self.engine, 'before_cursor_execute', capture)
        try:
            result = get_duplicates(self.session, cursor=None, limit=10, sort_by='size',
                                    media_type=None, min_count=2, folder=None)
        finally:
            event.remove(self.engine, 'before_cursor_execute', capture)
        self.assertEqual(len(result.items), 1)
        aggregate_sql = next(sql for sql in statements if 'GROUP BY' in sql)
        self.assertEqual(aggregate_sql.count('GROUP BY'), 1, aggregate_sql)

    def test_receipt_migration_upgrade_and_downgrade(self):
        import importlib.util
        from pathlib import Path
        from alembic.migration import MigrationContext
        from alembic.operations import Operations
        from sqlalchemy import inspect
        from sqlmodel import create_engine
        from app.models import MutationOperation
        path = next(Path('alembic/versions').glob('*_mutation_operation_receipts.py'))
        spec = importlib.util.spec_from_file_location('receipt_revision', path)
        revision = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(revision)
        engine = create_engine('sqlite://')
        try:
            with engine.begin() as connection:
                with patch.object(revision, 'op', Operations(MigrationContext.configure(connection))):
                    revision.upgrade()
                    actual = {column['name'] for column in inspect(connection).get_columns('mutationoperation')}
                    self.assertEqual(actual, set(MutationOperation.__table__.columns.keys()))
                    revision.downgrade()
                    self.assertNotIn('mutationoperation', inspect(connection).get_table_names())
        finally:
            engine.dispose()
