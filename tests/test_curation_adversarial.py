"""Independent adversarial cases; all fixtures and databases are temporary."""
import os
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

# Standalone discovery must not load a private application profile. When the
# full suite has already imported app.config, retain its caller-owned fixture
# environment instead of changing profile selection halfway through the suite.
_profile = None
if 'app.config' not in sys.modules:
    _profile = tempfile.TemporaryDirectory(prefix='curation-review-profile-')
    os.environ['XDG_CONFIG_HOME'] = _profile.name
    os.environ.pop('IS_DOCKER', None)

from fastapi import HTTPException
from PIL import Image
from PIL.PngImagePlugin import PngInfo
from sqlmodel import Session, SQLModel, create_engine, select
from app.curation_models import CurationDataset, CurationGrant, CurationSource, CurationOperation, CurationArtifact
from app.config import settings
from app.schemas.curation import MaterializeInput, CaptionInput, ReviewInput, ExportInput
from app.services import curation_artifacts as artifacts
from app.services.curation_plans import materialize, add_caption, review
from app.services.frozen_exports import admit_export, execute_export
from app.services.curation_policy import digest


class ReviewRegressions(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='curation-independent-')
        self.base = Path(self.temp.name)
        self.source = self.base / 'source'
        self.store = self.base / 'store'
        self.source.mkdir()
        self.store.mkdir()
        self.env = patch.dict(os.environ, {'OMOIDE_CURATION_FIXTURES': '1'})
        self.env.start()
        self.engine = create_engine('sqlite:///' + str(self.base / 'review.sqlite'))
        SQLModel.metadata.create_all(self.engine)
        self.token = 'independent-fixture-agent-token'
        Image.new('RGB', (12, 8), '#356aa0').save(self.source / 'still.png')
        with Session(self.engine) as session:
            self.dataset = CurationDataset(name='Independent fixture', subject_id='synthetic-geometric-fixture',
                source_root=str(self.source), source_device=self.source.stat().st_dev, source_inode=self.source.stat().st_ino,
                store_root=str(self.store), store_device=self.store.stat().st_dev, store_inode=self.store.stat().st_ino,
                policy={'version': 'fixture-stills-v1', 'fixture_only': True, 'generative_enabled': False})
            session.add(self.dataset)
            session.flush()
            self.source_row = CurationSource(dataset_id=self.dataset.id, label='Geometric still', relative_path='still.png',
                sha256=digest((self.source / 'still.png').read_bytes()), size=(self.source / 'still.png').stat().st_size,
                group_id='capture-one', split='train')
            session.add(self.source_row)
            session.add(CurationGrant(token_sha256=digest(self.token.encode()), actor_id='independent-reviewer',
                actor_kind='agent', dataset_id=self.dataset.id, expires_at=datetime.now(UTC).replace(tzinfo=None)+timedelta(hours=1),
                operations=['read', 'materialize', 'caption', 'export'], disclosure=False))
            self.human_token = 'independent-fixture-human-token'
            session.add(CurationGrant(token_sha256=digest(self.human_token.encode()), actor_id='fixture-reviewer',
                actor_kind='fixture_human', dataset_id=self.dataset.id, expires_at=datetime.now(UTC).replace(tzinfo=None)+timedelta(hours=1),
                operations=['read', 'review', 'export'], disclosure=True))
            session.commit()
            self.dataset_id = self.dataset.id
            self.source_id = self.source_row.id
        self.request = MaterializeInput(expected_revision=0, source_id=self.source_id, idempotency_key='independent-root-race')

    def tearDown(self):
        self.engine.dispose()
        self.env.stop()
        self.temp.cleanup()

    def prepare_export(self):
        with Session(self.engine) as session:
            data = materialize(session, self.token, self.dataset_id, self.request)
            item = data['items'][0]
            data = add_caption(session, self.token, self.dataset_id, CaptionInput(expected_revision=data['revision'],
                artifact_id=item['artifact_id'], text='Geometric fixture, exact caption.'))
            item = data['items'][0]
            data = review(session, self.human_token, self.dataset_id, ReviewInput(expected_revision=data['revision'],
                artifact_id=item['artifact_id'], caption_id=item['caption']['id'],
                asset_sha256=item['sha256'], caption_sha256=item['caption']['sha256'], decision='accept'))
            return admit_export(session, self.token, self.dataset_id,
                ExportInput(expected_revision=data['revision'], idempotency_key='independent-export'))

    def test_registered_rest_routes_reject_actor_spoof_and_legacy_stamp(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.api import curation as api, annotations, datasets
        from app.database import get_session
        from app.services.curation_policy import install_curation_guard
        operation_id = self.prepare_export()
        with Session(self.engine) as session:
            member = session.get(CurationOperation, operation_id).snapshot['members'][0]
            revision = session.get(CurationDataset, self.dataset_id).revision
        app = FastAPI()
        install_curation_guard(app)
        app.include_router(api.router, prefix='/api/curation')
        app.include_router(annotations.router, prefix='/api/annotations')
        app.include_router(datasets.router, prefix='/api/datasets')
        def owned_session():
            with Session(self.engine) as session:
                yield session
        app.dependency_overrides[get_session] = owned_session
        body = dict(expected_revision=revision, artifact_id=member['artifact_id'], caption_id=member['caption_id'],
                    asset_sha256=member['asset_sha256'], caption_sha256=member['caption_sha256'], decision='accept')
        with TestClient(app) as client:
            endpoint = '/api/curation/datasets/' + self.dataset_id + '/reviews'
            headers = {'Authorization': 'Bearer ' + self.token, 'X-Actor-Type': 'fixture_human'}
            result = client.post(endpoint, headers=headers, json={**body, 'actor_kind': 'fixture_human'})
            self.assertEqual(result.status_code, 422)
            result = client.post(endpoint, headers=headers, json=body)
            self.assertEqual(result.status_code, 403)
            self.assertEqual(result.json()['detail']['code'], 'operation_forbidden')
            for method, path in [('POST', '/api/annotations/annotations/example/revisions'),
                                 ('POST', '/api/annotations/annotations/example/approve'),
                                 ('PATCH', '/api/datasets/1/items/1/caption'),
                                 ('POST', '/api/datasets/1/items/1/caption/reviewed'),
                                 ('PATCH', '/api/datasets/1/items/1'),
                                 ('POST', '/api/datasets/1/items/1/review')]:
                result = client.request(method, path, json={'author': 'human'})
                self.assertEqual(result.status_code, 403, path)
                self.assertEqual(result.json()['detail']['code'], 'legacy_human_authority_unavailable')
            preview = client.get('/api/curation/artifacts/' + member['artifact_id'] + '/content', headers=headers)
            self.assertEqual(preview.status_code, 403)

    def test_export_owner_revocation_before_publication_blocks_export(self):
        operation_id = self.prepare_export()
        def revoke(point):
            if point == 'before_publication':
                with Session(self.engine) as other:
                    grant = other.exec(select(CurationGrant).where(CurationGrant.token_sha256 == digest(self.token.encode()))).one()
                    grant.revoked = True
                    other.add(grant)
                    other.commit()
        with Session(self.engine) as session:
            with self.assertRaises(HTTPException) as caught:
                execute_export(session, self.token, operation_id, checkpoint=revoke)
            self.assertEqual(caught.exception.detail['code'], 'unauthorized')
        self.assertFalse((self.store / 'exports' / operation_id).exists())

    def test_export_baseline_has_exact_bytes_and_private_locator_free_manifest(self):
        operation_id = self.prepare_export()
        with Session(self.engine) as session:
            result = execute_export(session, self.token, operation_id)
            snapshot = session.get(CurationOperation, operation_id).snapshot
        self.assertEqual(result['status'], 'succeeded')
        destination = self.store / 'exports' / operation_id
        for member in snapshot['members']:
            self.assertEqual((destination / member['caption_name']).read_bytes(), member['caption_text'].encode())
            self.assertEqual((destination / member['image_name']).read_bytes(),
                (self.store / 'artifacts' / (member['asset_sha256'] + '.png')).read_bytes())
        manifest = (destination / 'manifest.json').read_text()
        for secret in (str(self.source), str(self.store), self.token, self.human_token):
            self.assertNotIn(secret, manifest)

    def test_caption_edit_during_export_uses_admitted_exact_bytes(self):
        operation_id = self.prepare_export()
        with Session(self.engine) as session:
            captured = session.get(CurationOperation, operation_id).snapshot['members'][0]
        def edit_caption(point):
            if point == 'before_publication':
                with Session(self.engine) as other:
                    dataset = other.get(CurationDataset, self.dataset_id)
                    add_caption(other, self.token, self.dataset_id, CaptionInput(expected_revision=dataset.revision,
                        artifact_id=captured['artifact_id'], text='New caption with café and a trailing newline.\n'))
        with Session(self.engine) as session:
            result = execute_export(session, self.token, operation_id, checkpoint=edit_caption)
        self.assertEqual(result['status'], 'succeeded')
        self.assertEqual((self.store / 'exports' / operation_id / captured['caption_name']).read_bytes(), captured['caption_text'].encode())

    def test_reviewer_revocation_before_publication_blocks_export(self):
        operation_id = self.prepare_export()
        def revoke(point):
            if point == 'before_publication':
                with Session(self.engine) as other:
                    grant = other.exec(select(CurationGrant).where(CurationGrant.token_sha256 == digest(self.human_token.encode()))).one()
                    grant.revoked = True
                    other.add(grant)
                    other.commit()
        with Session(self.engine) as session:
            with self.assertRaises(HTTPException) as caught:
                execute_export(session, self.token, operation_id, checkpoint=revoke)
            self.assertEqual(caught.exception.detail['code'], 'review_authority_revoked')
        self.assertFalse((self.store / 'exports' / operation_id).exists())

    def test_crash_after_publication_reconciles_one_logical_export(self):
        operation_id = self.prepare_export()
        def crash(point):
            if point == 'after_publication':
                raise RuntimeError('test-only simulated lost terminal DB commit')
        with Session(self.engine) as session:
            with self.assertRaisesRegex(RuntimeError, 'test-only'):
                execute_export(session, self.token, operation_id, checkpoint=crash)
        destination = self.store / 'exports' / operation_id
        before = {file.name: digest(file.read_bytes()) for file in destination.iterdir()}
        with Session(self.engine) as session:
            result = execute_export(session, self.token, operation_id)
            self.assertEqual(len(session.exec(select(CurationOperation).where(CurationOperation.kind == 'export')).all()), 1)
        self.assertEqual(result['status'], 'succeeded')
        self.assertEqual(result['attempts'], 2)
        self.assertEqual(before, {file.name: digest(file.read_bytes()) for file in destination.iterdir()})

    def test_default_disabled_and_presentation_mode_apply_to_direct_service(self):
        with Session(self.engine) as session:
            with patch.dict(os.environ, {'OMOIDE_CURATION_FIXTURES': '0'}):
                with self.assertRaises(HTTPException) as caught:
                    materialize(session, self.token, self.dataset_id, self.request)
                self.assertEqual(caught.exception.status_code, 404)
            with patch.object(settings.general, 'presentation_mode', True):
                with self.assertRaises(HTTPException) as caught:
                    materialize(session, self.token, self.dataset_id, self.request)
                self.assertEqual(caught.exception.detail['code'], 'presentation_mode')
            self.assertEqual(session.exec(select(CurationOperation)).all(), [])

    def test_generative_ancestry_remains_disabled_for_fixture_human(self):
        operation_id = self.prepare_export()
        with Session(self.engine) as session:
            op = session.get(CurationOperation, operation_id)
            member = op.snapshot['members'][0]
            artifact = session.get(CurationArtifact, member['artifact_id'])
            ancestor = CurationArtifact(dataset_id=self.dataset_id, source_id=self.source_id,
                sha256=artifact.sha256, pixel_sha256=artifact.pixel_sha256, cache_key='generative-ancestor',
                size=artifact.size, width=artifact.width, height=artifact.height, generative=True, provenance={})
            session.add(ancestor)
            session.flush()
            artifact.parent_id = ancestor.id
            artifact.generative = False
            session.add(artifact)
            session.commit()
            dataset = session.get(CurationDataset, self.dataset_id)
            with patch.dict(os.environ, {'OMOIDE_CURATION_ALLOW_GENERATIVE': '1'}):
                with self.assertRaises(HTTPException) as caught:
                    review(session, self.human_token, self.dataset_id, ReviewInput(expected_revision=dataset.revision,
                        artifact_id=member['artifact_id'], caption_id=member['caption_id'], asset_sha256=member['asset_sha256'],
                        caption_sha256=member['caption_sha256'], decision='accept'))
                self.assertEqual(caught.exception.detail['code'], 'generative_disabled')

    def test_source_final_swap_is_rejected_without_source_mutation(self):
        replacement = self.source / 'replacement.png'
        Image.new('RGB', (12, 8), 'red').save(replacement)
        with Session(self.engine) as session:
            dataset = session.get(CurationDataset, self.dataset_id)
            source = session.get(CurationSource, self.source_id)
            def swap():
                replacement.replace(self.source / 'still.png')
            with self.assertRaises(HTTPException) as caught:
                artifacts.source_bytes(dataset, source, hook=swap)
            self.assertEqual(caught.exception.detail['code'], 'source_changed')

    def test_export_rejects_pixel_equal_holdout_with_distinct_file_hash(self):
        metadata = PngInfo()
        metadata.add_text('fixture-note', 'alternate encoding of same pixels')
        with Image.open(self.source / 'still.png') as image:
            image.save(self.source / 'holdout.png', pnginfo=metadata, compress_level=1)
        training = (self.source / 'still.png').read_bytes()
        holdout = (self.source / 'holdout.png').read_bytes()
        self.assertNotEqual(digest(training), digest(holdout))
        self.assertEqual(artifacts.normalized(training)[1], artifacts.normalized(holdout)[1])
        with Session(self.engine) as session:
            session.add(CurationSource(dataset_id=self.dataset_id, label='Holdout fixture', relative_path='holdout.png',
                sha256=digest(holdout), size=len(holdout), group_id='a-different-group', split='test'))
            session.commit()
        with self.assertRaises(HTTPException) as caught:
            self.prepare_export()
        self.assertEqual(caught.exception.detail['code'], 'split_leakage')

    def test_export_rejects_new_holdout_sibling_after_admission(self):
        operation_id = self.prepare_export()
        with Session(self.engine) as session:
            session.add(CurationSource(dataset_id=self.dataset_id, label='Holdout fixture', relative_path='holdout.png',
                sha256='a' * 64, size=1, group_id='capture-one', split='test'))
            session.commit()
        with Session(self.engine) as session:
            with self.assertRaises(HTTPException) as caught:
                execute_export(session, self.token, operation_id)
            self.assertEqual(caught.exception.detail['code'], 'split_leakage')

    def test_export_rejects_published_version_directory_swap_during_verification(self):
        from app.services import frozen_exports
        operation_id = self.prepare_export()
        verify = frozen_exports._verify
        destination = self.store / 'exports' / operation_id
        swapped = False
        def swap(fd, snapshot):
            nonlocal swapped
            if destination.exists() and not swapped:
                swapped = True
                destination.rename(self.store / 'exports' / (operation_id + '-displaced'))
                destination.mkdir()
            return verify(fd, snapshot)
        with Session(self.engine) as session, patch.object(frozen_exports, '_verify', swap):
            with self.assertRaises(HTTPException) as caught:
                execute_export(session, self.token, operation_id)
            self.assertIn(caught.exception.detail['code'], {'output_directory_changed', 'source_unavailable'})
        with Session(self.engine) as session:
            self.assertNotEqual(session.get(CurationOperation, operation_id).status, 'succeeded')

    def test_export_rejects_exports_directory_rename_during_publication(self):
        operation_id = self.prepare_export()
        def swap(point):
            if point == 'before_publication':
                (self.store / 'exports').rename(self.store / 'displaced-exports')
                (self.store / 'exports').mkdir()
        with Session(self.engine) as session:
            with self.assertRaises(HTTPException) as caught:
                execute_export(session, self.token, operation_id, checkpoint=swap)
            self.assertIn(caught.exception.detail['code'], {'root_identity_changed', 'output_identity_changed', 'output_directory_changed', 'storage_unavailable', 'published_export_missing', 'publication_changed'})
        with Session(self.engine) as session:
            self.assertNotEqual(session.get(CurationOperation, operation_id).status, 'succeeded')

    def test_materialize_rejects_store_root_rename_during_write(self):
        original = artifacts.write_once
        swapped = False
        def swap_store(fd, name, data):
            nonlocal swapped
            if not swapped:
                swapped = True
                self.store.rename(self.base / 'displaced-store')
                self.store.mkdir()
            return original(fd, name, data)
        with Session(self.engine) as session, patch.object(artifacts, 'write_once', swap_store):
            with self.assertRaises(HTTPException):
                materialize(session, self.token, self.dataset_id, self.request)
        with Session(self.engine) as session:
            op = session.exec(select(CurationOperation)).one()
            self.assertNotEqual(op.status, 'succeeded')


if __name__ == '__main__':
    unittest.main(verbosity=2)
