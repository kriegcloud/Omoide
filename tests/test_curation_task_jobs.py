"""Curation operations as shared-system jobs: fencing, cancellation, recovery.

Every fixture here is synthetic (generated geometric pixels, a temporary SQLite
database, a task-owned source/store root). No live library, container or real
credential is touched, and no threshold is read or changed.
"""
import importlib.util
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine, select

import app.database as db
from app.api.curation import router as curation_router
from app.api.tasks import router as tasks_router
from app.config import settings
from app.curation_models import CurationEvent, CurationOperation, CurationSource
from app.database import get_session
from app.models import ProcessingTask
from app.schemas.curation import CaptionInput, ExportInput, MaterializeInput, ReviewInput
from app.services import curation_jobs as jobs
from app.services.curation_fixtures import create_fixture_dataset
from app.services.curation_plans import add_caption, materialize, review
from app.services.curation_policy import install_curation_guard
from app.services.frozen_exports import admit_export, execute_export
from app.tasks.resume import is_resumable, join_resumed_workers, resume_task


class SimulatedCrash(BaseException):
    """Not an Exception: the service failure journal must not treat it as a code."""


class CurationJobTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.sources = self.root / 'sources'
        self.store = self.root / 'store'
        self.sources.mkdir()
        self.store.mkdir()
        Image.new('RGB', (32, 24), '#2f6f4f').save(self.sources / 'still.png')
        self.url = 'sqlite:///' + str(self.root / 'jobs.sqlite')
        self.engine = create_engine(self.url, connect_args={'timeout': 20, 'check_same_thread': False})

        @event.listens_for(self.engine, 'connect')
        def foreign_keys(connection, _):
            connection.execute('PRAGMA foreign_keys=ON')

        SQLModel.metadata.create_all(self.engine)
        self.addCleanup(self.engine.dispose)
        self.env = patch.dict(os.environ, {'OMOIDE_CURATION_FIXTURES': '1'})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.presentation = patch.object(settings.general, 'presentation_mode', False)
        self.presentation.start()
        self.addCleanup(self.presentation.stop)
        with Session(self.engine) as session:
            seeded = create_fixture_dataset(session, source_root=self.sources, store_root=self.store,
                files=[{'relative_path': 'still.png', 'label': 'Geometric fixture', 'group_id': 'capture-one'}])
            self.dataset_id = seeded['dataset_id']
            self.agent = seeded['credentials']['agent']
            self.human = seeded['credentials']['fixture_human']
            self.source_id = session.exec(select(CurationSource)).one().id

        def session_override():
            with Session(self.engine) as session:
                yield session

        app = FastAPI()
        install_curation_guard(app)
        app.include_router(curation_router, prefix='/api/curation')
        app.include_router(tasks_router, prefix='/api/tasks')
        app.dependency_overrides[get_session] = session_override
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

        # Fixture mode deliberately freezes every legacy `/api` mutation, which
        # includes `/api/tasks/{id}/cancel`. Production mode denies only the
        # listed human-stamping routes, so the task API stays reachable there.
        # This unguarded client models that production routing.
        tasks_app = FastAPI()
        tasks_app.include_router(tasks_router, prefix='/api/tasks')
        tasks_app.dependency_overrides[get_session] = session_override
        self.tasks = TestClient(tasks_app)
        self.addCleanup(self.tasks.close)

    # ---- helpers -------------------------------------------------------

    def prepare(self):
        with Session(self.engine) as session:
            state = materialize(session, self.agent, self.dataset_id, MaterializeInput(
                source_id=self.source_id, expected_revision=0, idempotency_key='materialize-one'))
            item = state['items'][0]
            state = add_caption(session, self.agent, self.dataset_id, CaptionInput(
                artifact_id=item['artifact_id'], text='Green geometric fixture.\n',
                expected_revision=state['revision']))
            item = state['items'][0]
            return review(session, self.human, self.dataset_id, ReviewInput(
                artifact_id=item['artifact_id'], caption_id=item['caption']['id'],
                asset_sha256=item['sha256'], caption_sha256=item['caption']['sha256'],
                decision='accept', expected_revision=state['revision']))

    def admit(self, key='export-one'):
        state = self.prepare()
        with Session(self.engine) as session:
            return admit_export(session, self.agent, self.dataset_id,
                                ExportInput(expected_revision=state['revision'], idempotency_key=key))

    def operation(self, operation_id):
        with Session(self.engine) as session:
            return session.get(CurationOperation, operation_id)

    def task_for(self, operation_id):
        with Session(self.engine) as session:
            op = session.get(CurationOperation, operation_id)
            return session.get(ProcessingTask, op.task_id) if op.task_id else None

    def simulate_worker_restart(self, operation_id):
        """A crashed worker leaves an expired lease owned by a vanished process."""
        with Session(self.engine) as session:
            op = session.get(CurationOperation, operation_id)
            op.lease_worker = 'vanished-host/999999/' + 'd' * 32
            op.lease_expires_at = datetime.utcnow() - timedelta(seconds=30)
            session.add(op)
            session.commit()

    def expect_code(self, code, function, *args, **kwargs):
        with self.assertRaises(HTTPException) as raised:
            function(*args, **kwargs)
        self.assertEqual(raised.exception.detail['code'], code)

    def events(self, operation_id):
        with Session(self.engine) as session:
            return [row.event for row in session.exec(select(CurationEvent).where(
                CurationEvent.operation_id == operation_id).order_by(CurationEvent.created_at)).all()]

    # ---- 2. bounded admission creates a visible, linked task -----------

    def test_admitted_operations_appear_in_the_shared_task_feed(self):
        operation_id = self.admit()
        op = self.operation(operation_id)
        self.assertIsNotNone(op.task_id)
        feed = self.client.get('/api/tasks/').json()
        by_id = {entry['id']: entry for entry in feed}
        self.assertIn(op.task_id, by_id)
        export_entry = by_id[op.task_id]
        self.assertEqual(export_entry['task_type'], 'curation_export')
        self.assertEqual(export_entry['params'], {'operation_id': operation_id})
        self.assertTrue(export_entry['resumable'])
        materialize_entries = [e for e in feed if e['task_type'] == 'curation_materialize']
        self.assertEqual(len(materialize_entries), 1)
        self.assertEqual(materialize_entries[0]['status'], 'completed')
        with Session(self.engine) as session:
            execute_export(session, self.agent, operation_id)
        self.assertEqual(self.task_for(operation_id).status, 'completed')
        self.assertEqual(self.operation(operation_id).status, 'succeeded')

    def test_export_records_member_progress_on_its_task(self):
        operation_id = self.admit()
        with Session(self.engine) as session:
            execute_export(session, self.agent, operation_id)
        task = self.task_for(operation_id)
        op = self.operation(operation_id)
        self.assertEqual((task.total, task.processed), (op.item_count, op.item_count))
        self.assertEqual(op.progress_done, op.item_count)

    # ---- 5. worker fencing --------------------------------------------

    def test_live_lease_fences_a_second_worker_until_it_expires(self):
        operation_id = self.admit()
        with Session(self.engine) as session:
            op = session.get(CurationOperation, operation_id)
            op.lease_worker = 'other-host/4242/' + 'a' * 32
            op.lease_attempt = 'b' * 32
            op.lease_expires_at = datetime.utcnow() + timedelta(seconds=120)
            session.add(op)
            session.commit()
        with Session(self.engine) as session:
            self.expect_code('operation_lease_held', execute_export, session, self.agent, operation_id)
        self.assertFalse((self.store / 'exports' / operation_id).exists())
        self.assertEqual(self.operation(operation_id).status, 'admitted')

        self.simulate_worker_restart(operation_id)
        with Session(self.engine) as session:
            result = execute_export(session, self.agent, operation_id)
        self.assertEqual(result['status'], 'succeeded')

    def test_publisher_refuses_when_its_lease_was_taken_over(self):
        operation_id = self.admit()

        def steal(point):
            if point != 'before_publication':
                return
            with Session(self.engine) as other:
                op = other.get(CurationOperation, operation_id)
                op.lease_worker = 'other-host/4242/' + 'a' * 32
                op.lease_attempt = 'b' * 32
                op.lease_expires_at = datetime.utcnow() + timedelta(seconds=120)
                other.add(op)
                other.commit()

        with Session(self.engine) as session:
            self.expect_code('operation_lease_lost', execute_export, session, self.agent,
                             operation_id, checkpoint=steal)
        self.assertFalse((self.store / 'exports' / operation_id).exists())

    # ---- 4. cancellation ----------------------------------------------

    def test_cancelling_the_task_stops_an_unpublished_export(self):
        operation_id = self.admit()
        task_id = self.operation(operation_id).task_id

        def cancel(point):
            if point == 'after_running_commit':
                response = self.tasks.post(f'/api/tasks/{task_id}/cancel')
                self.assertEqual(response.status_code, 200)

        with Session(self.engine) as session:
            self.expect_code('operation_cancelled', execute_export, session, self.agent,
                             operation_id, checkpoint=cancel)
        self.assertEqual(self.operation(operation_id).status, 'cancelled')
        self.assertFalse((self.store / 'exports' / operation_id).exists())
        self.assertIn('cancelled', self.events(operation_id))
        with Session(self.engine) as session:
            self.expect_code('operation_cancelled', execute_export, session, self.agent, operation_id)

    def test_cancelling_an_admitted_operation_marks_it_before_any_source_read(self):
        operation_id = self.admit()
        task_id = self.operation(operation_id).task_id
        self.assertEqual(self.tasks.post(f'/api/tasks/{task_id}/cancel').status_code, 200)
        self.assertEqual(self.operation(operation_id).status, 'cancelled')
        with Session(self.engine) as session:
            self.expect_code('operation_cancelled', execute_export, session, self.agent, operation_id)
        self.assertFalse((self.store / 'exports' / operation_id).exists())

    def test_a_published_export_is_never_unpublished_by_cancellation(self):
        operation_id = self.admit()
        with Session(self.engine) as session:
            result = execute_export(session, self.agent, operation_id)
        self.assertEqual(result['status'], 'succeeded')
        task_id = self.operation(operation_id).task_id
        self.assertEqual(self.tasks.post(f'/api/tasks/{task_id}/cancel').status_code, 400)
        with Session(self.engine) as session:
            task = session.get(ProcessingTask, task_id)
            task.status = 'cancelled'
            session.add(task)
            session.commit()
            jobs.on_task_cancelled(session, task)
        self.assertEqual(self.operation(operation_id).status, 'succeeded')
        self.assertTrue((self.store / 'exports' / operation_id / 'manifest.json').exists())

    # ---- 6. reconcile and resume --------------------------------------

    def test_reconcile_marks_an_already_published_export_succeeded_without_rerunning(self):
        operation_id = self.admit()
        with Session(self.engine) as session:
            def crash(point):
                if point == 'after_publication':
                    raise SimulatedCrash()
            with self.assertRaises(SimulatedCrash):
                execute_export(session, self.agent, operation_id, checkpoint=crash)
        self.assertEqual(self.operation(operation_id).status, 'running')
        attempts = self.operation(operation_id).attempts
        self.simulate_worker_restart(operation_id)
        with Session(self.engine) as session:
            summary = jobs.reconcile_curation_operations(session)
        self.assertEqual(summary['succeeded'], 1)
        op = self.operation(operation_id)
        self.assertEqual(op.status, 'succeeded')
        self.assertEqual(op.attempts, attempts)
        self.assertTrue(op.manifest_sha256)
        self.assertEqual(self.task_for(operation_id).status, 'completed')
        self.assertIn('reconciled_published', self.events(operation_id))

    def test_reconcile_returns_an_unfinished_export_to_admitted_for_resume(self):
        operation_id = self.admit()
        with Session(self.engine) as session:
            def crash(point):
                if point == 'after_staging_directory':
                    raise SimulatedCrash()
            with self.assertRaises(SimulatedCrash):
                execute_export(session, self.agent, operation_id, checkpoint=crash)
        self.assertEqual(self.operation(operation_id).status, 'running')
        self.simulate_worker_restart(operation_id)
        with Session(self.engine) as session:
            summary = jobs.reconcile_curation_operations(session)
        self.assertEqual(summary['readmitted'], 1)
        op = self.operation(operation_id)
        self.assertEqual(op.status, 'admitted')
        self.assertIsNone(op.lease_expires_at)
        task = self.task_for(operation_id)
        self.assertEqual(task.status, 'interrupted')
        self.assertTrue(is_resumable(task))

        # The resumed worker opens its own session against the application
        # engine, so point that at this task-owned database for the resume.
        with patch.object(db, 'engine', self.engine):
            with Session(self.engine) as session:
                successor = resume_task(session, session.get(ProcessingTask, task.id))
                successor_id = successor.id
            self.assertEqual(join_resumed_workers(30.0), 0)
        with Session(self.engine) as session:
            successor = session.get(ProcessingTask, successor_id)
            self.assertEqual(successor.params['operation_id'], operation_id)
            self.assertEqual(successor.params['resumed_from'], task.id)
            self.assertEqual(str(successor.status), 'completed')
        op = self.operation(operation_id)
        self.assertEqual(op.status, 'succeeded')
        # The successor executed the same admitted operation, not a new plan.
        self.assertEqual(op.task_id, successor_id)
        directories = [p for p in (self.store / 'exports').iterdir() if p.is_dir()]
        self.assertEqual(len(directories), 1)
        self.assertEqual(directories[0].name, operation_id)

    def test_reconcile_skips_an_operation_a_live_worker_still_holds(self):
        operation_id = self.admit()
        with Session(self.engine) as session:
            op = session.get(CurationOperation, operation_id)
            op.status = 'running'
            op.lease_worker = 'other-host/4242/' + 'a' * 32
            op.lease_expires_at = datetime.utcnow() + timedelta(seconds=120)
            session.add(op)
            session.commit()
        with Session(self.engine) as session:
            summary = jobs.reconcile_curation_operations(session)
        self.assertEqual(summary['skipped'], 1)
        self.assertEqual(self.operation(operation_id).status, 'running')

    def test_reconcile_and_resume_never_create_a_duplicate_execution(self):
        operation_id = self.admit()
        with Session(self.engine) as session:
            op = session.get(CurationOperation, operation_id)
            op.status = 'running'
            session.add(op)
            session.commit()
        self.simulate_worker_restart(operation_id)
        with Session(self.engine) as session:
            jobs.reconcile_curation_operations(session)
            jobs.reconcile_curation_operations(session)
        with Session(self.engine) as session:
            tasks = session.exec(select(ProcessingTask).where(
                ProcessingTask.task_type == 'curation_export')).all()
            self.assertEqual([t.params['operation_id'] for t in tasks], [operation_id])

    def test_resume_serializes_one_operation_but_not_unrelated_ones(self):
        first = self.admit('export-one')
        with Session(self.engine) as session:
            revision = session.get(CurationOperation, first).snapshot_revision
            second = admit_export(session, self.agent, self.dataset_id,
                ExportInput(expected_revision=revision, idempotency_key='export-two'))
        self.assertNotEqual(first, second)
        first_task = self.operation(first).task_id
        second_task = self.operation(second).task_id

        # A live execution of the FIRST operation must not block a resume of the
        # second: they are independent admitted jobs of the same task type.
        with Session(self.engine) as session:
            running = session.get(ProcessingTask, first_task)
            running.status = 'running'
            interrupted = session.get(ProcessingTask, second_task)
            interrupted.status = 'interrupted'
            session.add_all([running, interrupted])
            session.commit()
        with patch.object(jobs, 'run_curation_export', lambda task_id: None):
            with Session(self.engine) as session:
                successor = resume_task(session, session.get(ProcessingTask, second_task))
                self.assertEqual(successor.params['operation_id'], second)
                successor_id = successor.id
            self.assertEqual(join_resumed_workers(10.0), 0)
            # A second live execution of the SAME operation is refused.
            with Session(self.engine) as session:
                session.get(ProcessingTask, successor_id).status = 'running'
                interrupted = session.get(ProcessingTask, second_task)
                interrupted.result = None
                session.add(interrupted)
                session.commit()
            with Session(self.engine) as session:
                with self.assertRaises(HTTPException) as raised:
                    resume_task(session, session.get(ProcessingTask, second_task))
                self.assertEqual(raised.exception.status_code, 409)

    # ---- resume registration ------------------------------------------

    def test_resume_factories_require_an_operation_id(self):
        self.assertTrue(is_resumable(ProcessingTask(task_type='curation_export',
                                                    params={'operation_id': 'x'})))
        self.assertTrue(is_resumable(ProcessingTask(task_type='curation_materialize',
                                                    params={'operation_id': 'x'})))
        self.assertFalse(is_resumable(ProcessingTask(task_type='curation_export', params={})))
        self.assertFalse(is_resumable(ProcessingTask(task_type='curation_export', params=None)))


class CurationJobMigrationTests(unittest.TestCase):
    def _module(self):
        directory = Path(__file__).resolve().parents[1] / 'alembic/versions'
        path = next(p for p in directory.iterdir() if p.name.endswith('_curation_operation_jobs.py'))
        spec = importlib.util.spec_from_file_location('curation_jobs_migration', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_single_head_chained_after_the_authority_migration(self):
        from alembic.config import Config
        from alembic.script import ScriptDirectory
        root = Path(__file__).resolve().parents[1]
        config = Config(str(root / 'alembic.ini'))
        config.set_main_option('script_location', str(root / 'alembic'))
        heads = ScriptDirectory.from_config(config).get_heads()
        module = self._module()
        self.assertEqual(module.down_revision, '60718293a4b5')
        self.assertEqual(list(heads), [module.revision])

    def test_additive_upgrade_downgrade_and_populated_refusal(self):
        from alembic.migration import MigrationContext
        from alembic.operations import Operations
        module = self._module()
        with tempfile.TemporaryDirectory() as temp:
            engine = create_engine('sqlite:///' + str(Path(temp) / 'migration.sqlite'))
            with engine.begin() as connection:
                connection.exec_driver_sql(
                    'CREATE TABLE curation_operation (id VARCHAR PRIMARY KEY, dataset_id VARCHAR, '
                    'grant_id VARCHAR, kind VARCHAR, idempotency_key VARCHAR, request_sha256 VARCHAR, '
                    'status VARCHAR, snapshot_revision INTEGER, snapshot JSON, manifest_sha256 VARCHAR, '
                    'error_code VARCHAR, item_count INTEGER, attempts INTEGER, created_at DATETIME)')
                context = MigrationContext.configure(connection)
                with Operations.context(context):
                    module.upgrade()
                    columns = {row[1] for row in connection.exec_driver_sql(
                        'PRAGMA table_info(curation_operation)').all()}
                    self.assertTrue({'task_id', 'lease_worker', 'lease_attempt',
                                     'lease_expires_at', 'progress_done'} <= columns)
                    self.assertEqual(connection.exec_driver_sql('PRAGMA integrity_check').scalar(), 'ok')
                    module.downgrade()
                    module.upgrade()
                    connection.exec_driver_sql(
                        "INSERT INTO curation_operation (id, kind, status, task_id) "
                        "VALUES ('x', 'export', 'admitted', 'task-1')")
                    with self.assertRaisesRegex(RuntimeError, 'Refusing downgrade'):
                        module.downgrade()
            engine.dispose()


if __name__ == '__main__':
    unittest.main()
