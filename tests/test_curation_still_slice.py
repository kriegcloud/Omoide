"""Rights-clear, CPU-only authority, immutability and recovery integration tests."""
import importlib.util
import io
import multiprocessing
import os
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from PIL import Image, PngImagePlugin
from sqlalchemy import event, text
from sqlmodel import Session, SQLModel, create_engine, select

from app.api.curation import router
from app.config import FaceRecognitionSettings, settings
from app.curation_models import (CurationArtifact, CurationCaption, CurationDataset,
    CurationGrant, CurationOperation, CurationReview, CurationSource)
from app.database import get_session
from app.schemas.curation import CaptionInput, ExportInput, MaterializeInput, ReviewInput
from app.services import curation_artifacts as files
from app.services.curation_fixtures import create_fixture_dataset
from app.services.curation_plans import add_caption, detail, materialize, review
from app.services.curation_policy import digest, install_curation_guard
from app.services.frozen_exports import admit_export, execute_export


def concurrent_export(database_url, token, dataset_id, request, barrier, result_queue):
    engine = create_engine(database_url, connect_args={'timeout': 20})
    try:
        barrier.wait(timeout=20)
        with Session(engine) as session:
            operation_id = admit_export(session, token, dataset_id, ExportInput(**request))
            result = execute_export(session, token, operation_id)
            result_queue.put(('ok', result['id']))
    except BaseException as exc:
        result_queue.put(('error', repr(exc)))
    finally:
        engine.dispose()


class StillSliceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.sources = self.root / 'sources'
        self.store = self.root / 'store'
        self.sources.mkdir()
        self.store.mkdir()
        Image.new('RGB', (32, 24), '#3366aa').save(self.sources / 'still.png')
        self.initial = (self.sources / 'still.png').read_bytes()
        self.url = 'sqlite:///' + str(self.root / 'fixture.sqlite')
        self.engine = create_engine(self.url, connect_args={'timeout': 20, 'check_same_thread': False})
        @event.listens_for(self.engine, 'connect')
        def foreign_keys(connection, _):
            connection.execute('PRAGMA foreign_keys=ON')
        SQLModel.metadata.create_all(self.engine)
        self.env = patch.dict(os.environ, {'OMOIDE_CURATION_FIXTURES': '1'})
        self.env.start()
        self.presentation = patch.object(settings.general, 'presentation_mode', False)
        self.presentation.start()
        with Session(self.engine) as session:
            seeded = create_fixture_dataset(session, source_root=self.sources, store_root=self.store,
                files=[{'relative_path': 'still.png', 'label': 'Geometric fixture', 'group_id': 'capture-one'}])
            self.dataset_id = seeded['dataset_id']
            self.agent = seeded['credentials']['agent']
            self.human = seeded['credentials']['fixture_human']
            self.source_id = session.exec(select(CurationSource)).one().id
        app = FastAPI()
        install_curation_guard(app)
        app.include_router(router, prefix='/api/curation')
        def session_override():
            with Session(self.engine) as session:
                yield session
        app.dependency_overrides[get_session] = session_override
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.presentation.stop()
        self.env.stop()
        self.engine.dispose()
        self.temp.cleanup()

    def headers(self, token=None):
        return {'Authorization': 'Bearer ' + (token or self.human)}

    def prepare(self, accept=True):
        with Session(self.engine) as session:
            state = materialize(session, self.agent, self.dataset_id, MaterializeInput(
                source_id=self.source_id, expected_revision=0, idempotency_key='materialize-one'))
            item = state['items'][0]
            state = add_caption(session, self.agent, self.dataset_id, CaptionInput(
                artifact_id=item['artifact_id'], text='Blue geometric fixture.\n', expected_revision=state['revision']))
            item = state['items'][0]
            if accept:
                state = review(session, self.human, self.dataset_id, self.review_input(state))
            return state

    def review_input(self, state, decision='accept'):
        item = state['items'][0]
        return ReviewInput(artifact_id=item['artifact_id'], caption_id=item['caption']['id'],
            asset_sha256=item['sha256'], caption_sha256=item['caption']['sha256'],
            decision=decision, expected_revision=state['revision'])

    def admit(self):
        state = self.prepare()
        with Session(self.engine) as session:
            operation_id = admit_export(session, self.agent, self.dataset_id,
                ExportInput(expected_revision=state['revision'], idempotency_key='export-one'))
        return operation_id

    def expect_code(self, code, function, *args, **kwargs):
        with self.assertRaises(HTTPException) as raised:
            function(*args, **kwargs)
        self.assertEqual(raised.exception.detail['code'], code)

    def test_disabled_by_default_and_spoofed_actor_rejected(self):
        with patch.dict(os.environ, {'OMOIDE_CURATION_FIXTURES': '0'}):
            self.assertFalse(self.client.get('/api/curation/status').json()['enabled'])
            self.assertEqual(self.client.get('/api/curation/datasets', headers=self.headers()).status_code, 404)
        self.assertEqual(self.client.get('/api/curation/datasets', headers=self.headers('fake')).status_code, 401)
        state = self.prepare(False)
        body = self.review_input(state).model_dump()
        body['actor_kind'] = 'fixture_human'
        response = self.client.post(f'/api/curation/datasets/{self.dataset_id}/reviews', headers=self.headers(self.agent), json=body)
        self.assertEqual(response.status_code, 422)
        del body['actor_kind']
        response = self.client.post(f'/api/curation/datasets/{self.dataset_id}/reviews', headers=self.headers(self.agent), json=body)
        self.assertEqual(response.status_code, 403)
        with Session(self.engine) as session:
            self.assertEqual(session.exec(select(CurationReview)).all(), [])

    def test_gets_do_not_mutate_and_agent_cannot_disclose_pixels(self):
        state = self.prepare()
        with Session(self.engine) as session:
            count = session.exec(text('SELECT COUNT(*) FROM curation_event')).one()[0]
        for _ in range(3):
            result = self.client.get(f'/api/curation/datasets/{self.dataset_id}', headers=self.headers())
            self.assertEqual(result.status_code, 200)
        artifact = state['items'][0]['artifact_id']
        self.assertEqual(self.client.get(f'/api/curation/artifacts/{artifact}/content', headers=self.headers(self.agent)).status_code, 403)
        image = self.client.get(f'/api/curation/artifacts/{artifact}/content', headers=self.headers())
        self.assertEqual(digest(image.content), state['items'][0]['sha256'])
        self.assertEqual(image.headers['cache-control'], 'no-store')
        with Session(self.engine) as session:
            self.assertEqual(session.exec(text('SELECT COUNT(*) FROM curation_event')).one()[0], count)

    def test_foreign_dataset_and_presentation_denied_in_direct_service(self):
        state = self.prepare(False)
        with Session(self.engine) as session:
            self.expect_code('not_found', detail, session, self.agent, 'inaccessible-dataset')
            with patch.object(settings.general, 'presentation_mode', True):
                self.expect_code('presentation_mode', review, session, self.human, self.dataset_id, self.review_input(state))

    def test_stale_revision_and_caption_invalidates_review(self):
        state = self.prepare()
        with Session(self.engine) as session:
            old_review = self.review_input(state)
            new = add_caption(session, self.agent, self.dataset_id, CaptionInput(artifact_id=state['items'][0]['artifact_id'], text='Corrected caption', expected_revision=state['revision']))
            self.assertFalse(new['items'][0]['eligible'])
            self.assertIsNone(new['items'][0]['review'])
            self.expect_code('revision_conflict', review, session, self.human, self.dataset_id, old_review)
            old_review.expected_revision = new['revision']
            self.expect_code('review_hash_conflict', review, session, self.human, self.dataset_id, old_review)
            self.expect_code('review_required', admit_export, session, self.agent, self.dataset_id,
                ExportInput(expected_revision=new['revision'], idempotency_key='blocked-export'))

    def test_caption_idempotency_key_replays_and_conflicts_durably(self):
        state = self.prepare(False)
        artifact_id = state['items'][0]['artifact_id']
        with Session(self.engine) as session:
            first = add_caption(session, self.agent, self.dataset_id, CaptionInput(artifact_id=artifact_id,
                text='Keyed caption', expected_revision=state['revision'], idempotency_key='caption-key-1'))
            replay = add_caption(session, self.agent, self.dataset_id, CaptionInput(artifact_id=artifact_id,
                text='Keyed caption', expected_revision=state['revision'], idempotency_key='caption-key-1'))
            self.assertEqual(replay['revision'], first['revision'], 'a replay creates no revision')
            self.assertEqual((first['replayed'], replay['replayed']), (False, True))
            self.assertEqual(session.exec(select(CurationCaption).where(CurationCaption.text == 'Keyed caption')).all().__len__(), 1)
            operation = session.exec(select(CurationOperation).where(CurationOperation.kind == 'caption')).one()
            self.assertEqual((operation.status, operation.idempotency_key, operation.snapshot['caption_sha256']),
                             ('succeeded', 'caption-key-1', digest(b'Keyed caption')))
            self.expect_code('idempotency_conflict', add_caption, session, self.agent, self.dataset_id,
                CaptionInput(artifact_id=artifact_id, text='Different text', expected_revision=first['revision'],
                             idempotency_key='caption-key-1'))
            # A stale expected revision under a fresh key is still a conflict; without a key, behaviour is unchanged.
            self.expect_code('revision_conflict', add_caption, session, self.agent, self.dataset_id,
                CaptionInput(artifact_id=artifact_id, text='Late', expected_revision=state['revision'],
                             idempotency_key='caption-key-2'))
            second = add_caption(session, self.agent, self.dataset_id, CaptionInput(artifact_id=artifact_id,
                text='Unkeyed caption', expected_revision=first['revision']))
            self.assertEqual(second['revision'], first['revision'] + 1)
            self.assertNotIn('replayed', second, 'unkeyed callers see the unchanged contract')
            self.assertEqual(len(session.exec(select(CurationOperation).where(CurationOperation.kind == 'caption')).all()), 1)

    def test_reject_and_defer_are_not_acceptance(self):
        state = self.prepare(False)
        with Session(self.engine) as session:
            for decision in ('reject', 'defer'):
                state = review(session, self.human, self.dataset_id, self.review_input(state, decision))
                self.assertFalse(state['items'][0]['eligible'])
                self.expect_code('review_' + decision, admit_export, session, self.agent, self.dataset_id,
                    ExportInput(expected_revision=state['revision'], idempotency_key='export-' + decision))

    def test_revocation_rechecked_between_admission_and_execution(self):
        operation_id = self.admit()
        with Session(self.engine) as session:
            grant = session.exec(select(CurationGrant).where(CurationGrant.token_sha256 == digest(self.agent.encode()))).one()
            grant.revoked = True
            session.add(grant)
            session.commit()
            self.expect_code('unauthorized', execute_export, session, self.agent, operation_id)
        self.assertFalse((self.store / 'exports' / operation_id).exists())

    def test_reviewer_revocation_blocks_frozen_admission(self):
        operation_id = self.admit()
        with Session(self.engine) as session:
            grant = session.exec(select(CurationGrant).where(CurationGrant.token_sha256 == digest(self.human.encode()))).one()
            grant.revoked = True
            session.add(grant)
            session.commit()
            self.expect_code('review_authority_revoked', execute_export, session, self.agent, operation_id)

    def test_transitive_generative_ancestry_cannot_be_accepted(self):
        state = self.prepare(False)
        with Session(self.engine) as session:
            child = session.get(CurationArtifact, state['items'][0]['artifact_id'])
            parent = CurationArtifact(dataset_id=self.dataset_id, source_id=self.source_id,
                sha256=child.sha256, pixel_sha256=child.pixel_sha256, cache_key='generative-parent',
                size=child.size, width=child.width, height=child.height, generative=True, provenance={})
            session.add(parent)
            session.flush()
            child.parent_id = parent.id
            session.add(child)
            session.commit()
            self.expect_code('generative_disabled', review, session, self.human, self.dataset_id, self.review_input(state))
            self.assertEqual(session.exec(select(CurationReview)).all(), [])

    def test_unknown_lineage_and_generator_source_disabled(self):
        with Session(self.engine) as session:
            source = session.get(CurationSource, self.source_id)
            source.lineage_known = False
            session.add(source)
            session.commit()
            request = MaterializeInput(source_id=source.id, expected_revision=0, idempotency_key='unknown-lineage')
            self.expect_code('unknown_lineage', materialize, session, self.agent, self.dataset_id, request)
            source.lineage_known = True
            source.generative = True
            session.add(source)
            session.commit()
            self.expect_code('generative_disabled', materialize, session, self.agent, self.dataset_id, request)

    def test_explicit_loose_preset_rejected_without_changing_settings(self):
        original = settings.face_recognition.model_dump()
        with Session(self.engine) as session:
            self.expect_code('threshold_policy_lowered', create_fixture_dataset, session,
                source_root=self.sources, store_root=self.store, files=[{'relative_path': 'still.png', 'group_id': 'one'}],
                face_settings=FaceRecognitionSettings(preset='loose'))
        self.assertEqual(settings.face_recognition.model_dump(), original)

    def test_caption_mutation_during_export_uses_admitted_bytes(self):
        operation_id = self.admit()
        def concurrent_edit(point):
            if point == 'before_publication':
                with Session(self.engine) as other:
                    state = detail(other, self.agent, self.dataset_id)
                    add_caption(other, self.agent, self.dataset_id, CaptionInput(
                        artifact_id=state['items'][0]['artifact_id'], text='A later unreviewed draft', expected_revision=state['revision']))
        with Session(self.engine) as session:
            result = execute_export(session, self.agent, operation_id, checkpoint=concurrent_edit)
            snapshot = session.get(CurationOperation, operation_id).snapshot
            member = snapshot['members'][0]
            self.assertEqual(result['status'], 'succeeded')
            exported = self.store / 'exports' / operation_id
            self.assertEqual((exported / member['caption_name']).read_bytes(), b'Blue geometric fixture.\n')
            artifact_path = self.store / 'artifacts' / (member['asset_sha256'] + '.png')
            self.assertEqual((exported / member['image_name']).read_bytes(), artifact_path.read_bytes())
            self.assertNotEqual((exported / member['image_name']).stat().st_ino, artifact_path.stat().st_ino)
        self.assertEqual((self.sources / 'still.png').read_bytes(), self.initial)

    def test_crash_recovery_after_staging_and_after_publication(self):
        operation_id = self.admit()
        class SimulatedCrash(BaseException):
            pass
        for phase in ('after_staging_directory', 'after_publication'):
            with Session(self.engine) as session:
                def crash(point):
                    if point == phase:
                        raise SimulatedCrash()
                with self.assertRaises(SimulatedCrash):
                    execute_export(session, self.agent, operation_id, checkpoint=crash)
            with Session(self.engine) as session:
                self.assertEqual(session.get(CurationOperation, operation_id).status, 'running')
        with Session(self.engine) as session:
            result = execute_export(session, self.agent, operation_id)
            self.assertEqual(result['status'], 'succeeded')
            self.assertEqual(result['attempts'], 3)
            again = execute_export(session, self.agent, operation_id)
            self.assertEqual(again['manifest_sha256'], result['manifest_sha256'])
        directories = [p for p in (self.store / 'exports').iterdir() if p.is_dir()]
        self.assertEqual(len(directories), 1)

    def test_changed_source_and_policy_block_export(self):
        operation_id = self.admit()
        (self.sources / 'still.png').write_bytes(b'changed')
        with Session(self.engine) as session:
            self.expect_code('hash_mismatch', execute_export, session, self.agent, operation_id)
        (self.sources / 'still.png').write_bytes(self.initial)
        with Session(self.engine) as session:
            dataset = session.get(CurationDataset, self.dataset_id)
            dataset.policy = {**dataset.policy, 'version': 'changed'}
            session.add(dataset)
            session.commit()
            self.expect_code('policy_stale', execute_export, session, self.agent, operation_id)

    def test_two_process_same_key_exactly_one_export(self):
        state = self.prepare()
        # Fork avoids importing a production application or launching any service.
        context = multiprocessing.get_context('fork')
        barrier = context.Barrier(2)
        queue = context.Queue()
        request = {'expected_revision': state['revision'], 'idempotency_key': 'race-export'}
        processes = [context.Process(target=concurrent_export, args=(self.url, self.agent, self.dataset_id, request, barrier, queue)) for _ in range(2)]
        for process in processes:
            process.start()
        for process in processes:
            process.join(30)
            self.assertFalse(process.is_alive())
            self.assertEqual(process.exitcode, 0)
        results = [queue.get(timeout=2) for _ in processes]
        self.assertTrue(all(result[0] == 'ok' for result in results), results)
        self.assertEqual(results[0][1], results[1][1])
        with Session(self.engine) as session:
            self.assertEqual(len(session.exec(select(CurationOperation).where(CurationOperation.kind == 'export')).all()), 1)
            self.expect_code('idempotency_conflict', admit_export, session, self.agent, self.dataset_id,
                ExportInput(expected_revision=state['revision'] + 1, idempotency_key='race-export'))

    def test_source_symlink_fifo_traversal_and_swap(self):
        with Session(self.engine) as session:
            dataset = session.get(CurationDataset, self.dataset_id)
            source = session.get(CurationSource, self.source_id)
            original = self.sources / 'still.png'
            other = self.root / 'other.png'
            other.write_bytes(self.initial)
            original.unlink()
            original.symlink_to(other)
            self.expect_code('source_unavailable', files.source_bytes, dataset, source)
            original.unlink()
            os.mkfifo(original)
            self.expect_code('nonregular_file', files.source_bytes, dataset, source)
            original.unlink()
            original.write_bytes(self.initial)
            source.relative_path = '../other.png'
            self.expect_code('unsafe_path', files.source_bytes, dataset, source)
            source.relative_path = 'still.png'
            def swap():
                original.rename(self.sources / 'old.png')
                other.replace(original)
            self.expect_code('source_changed', files.source_bytes, dataset, source, swap)

    def test_orientation_and_explicit_unsupported_formats(self):
        image = Image.new('RGB', (12, 8), '#336699')
        exif = Image.Exif()
        exif[274] = 6
        stream = io.BytesIO()
        image.save(stream, format='JPEG', exif=exif)
        output, _, width, height, provenance = files.normalized(stream.getvalue())
        self.assertEqual((width, height), (8, 12))
        self.assertEqual(Image.open(io.BytesIO(output)).getexif().get(274), None)
        self.assertEqual(provenance['source_exif_orientation'], 6)
        self.assertEqual(files.normalized(stream.getvalue())[0], output)
        for mode, format_, kwargs, code in [
            ('RGBA', 'PNG', {}, 'unsupported_color_or_alpha'),
            ('RGB', 'TIFF', {}, 'unsupported_format'),
            ('RGB', 'PNG', {'icc_profile': b'fake-profile'}, 'unsupported_icc'),
        ]:
            stream = io.BytesIO()
            Image.new(mode, (4, 4)).save(stream, format=format_, **kwargs)
            self.expect_code(code, files.normalized, stream.getvalue())
        self.expect_code('corrupt_or_unsupported_image', files.normalized, b'corrupt')

    def test_artifact_crash_before_promotion_retries_without_poisoning_cache(self):
        request = MaterializeInput(source_id=self.source_id, expected_revision=0, idempotency_key='crash-artifact')
        class SimulatedCrash(BaseException):
            pass
        with Session(self.engine) as session, patch.object(files.os, 'link', side_effect=SimulatedCrash):
            with self.assertRaises(SimulatedCrash):
                materialize(session, self.agent, self.dataset_id, request)
        self.assertEqual(list((self.store / 'artifacts').glob('*.png')), [])
        with Session(self.engine) as session:
            result = materialize(session, self.agent, self.dataset_id, request)
            self.assertEqual(len(result['items']), 1)
            self.assertEqual(result['revision'], 1)
        self.assertEqual((self.sources / 'still.png').read_bytes(), self.initial)

    def test_revocation_during_copy_prevents_publication(self):
        operation_id = self.admit()
        def revoke(point):
            if point == 'before_publication':
                with Session(self.engine) as other:
                    grant = other.exec(select(CurationGrant).where(CurationGrant.token_sha256 == digest(self.agent.encode()))).one()
                    grant.revoked = True
                    other.add(grant)
                    other.commit()
        with Session(self.engine) as session:
            self.expect_code('unauthorized', execute_export, session, self.agent, operation_id, checkpoint=revoke)
        self.assertFalse((self.store / 'exports' / operation_id).exists())

    def test_expiry_and_oversized_body(self):
        with Session(self.engine) as session:
            grant = session.exec(select(CurationGrant).where(CurationGrant.token_sha256 == digest(self.human.encode()))).one()
            grant.expires_at -= timedelta(days=1)
            session.add(grant)
            session.commit()
        self.assertEqual(self.client.get('/api/curation/datasets', headers=self.headers()).status_code, 401)
        response = self.client.post(f'/api/curation/datasets/{self.dataset_id}/captions',
            headers={**self.headers(self.agent), 'Content-Type': 'application/json'}, content=b'x' * 262145)
        self.assertEqual(response.status_code, 413)

    def test_preexisting_artifact_symlink_never_followed_or_overwritten(self):
        with Session(self.engine) as session:
            dataset = session.get(CurationDataset, self.dataset_id)
            encoded = files.normalized(self.initial)[0]
            (self.store / 'artifacts').mkdir()
            target = self.root / 'untouched.png'
            target.write_bytes(b'must stay unchanged')
            (self.store / 'artifacts' / (digest(encoded) + '.png')).symlink_to(target)
            self.expect_code('source_unavailable', files.publish_artifact, dataset, encoded)
            self.assertEqual(target.read_bytes(), b'must stay unchanged')

    def test_feature_status_human_presence_remains_false(self):
        result = self.client.get('/api/curation/status').json()
        self.assertFalse(result['human_presence_verified'])
        self.assertFalse(result['generative_enabled'])
        self.assertTrue(result['fixture_only'])


class AdditiveMigrationTests(unittest.TestCase):
    def test_migration_upgrade_downgrade_and_populated_refusal(self):
        from alembic.migration import MigrationContext
        from alembic.operations import Operations
        path = Path(__file__).resolve().parents[1] / 'alembic/versions/5f60718293a4_fixture_still_curation.py'
        spec = importlib.util.spec_from_file_location('curation_migration', path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        self.assertEqual(migration.down_revision, '4e5f60718293')
        with tempfile.TemporaryDirectory() as temp:
            engine = create_engine('sqlite:///' + str(Path(temp) / 'migration.sqlite'))
            with engine.begin() as connection:
                context = MigrationContext.configure(connection)
                with Operations.context(context):
                    migration.upgrade()
                    self.assertEqual(connection.exec_driver_sql('PRAGMA integrity_check').scalar(), 'ok')
                    self.assertEqual(connection.exec_driver_sql('PRAGMA foreign_key_check').all(), [])
                    tables = connection.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'curation_%'").all()
                    self.assertEqual(len(tables), 8)
                    migration.downgrade()
                    migration.upgrade()
                    connection.exec_driver_sql("INSERT INTO curation_dataset VALUES ('x','fixture',0,'v1','subject','/source',1,1,'/store',1,2,'{}')")
                    with self.assertRaisesRegex(RuntimeError, 'Refusing downgrade'):
                        migration.downgrade()
            engine.dispose()
