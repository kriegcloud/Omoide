"""Real signed WebAuthn protocol fixtures, isolated DB and geometric pixels only."""
import base64
import hashlib
import importlib.util
import json
import os
import secrets
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine, select

from app.api.curation import router
from app.config import settings
from app.curation_models import (CurationChallenge, CurationCredential, CurationDataset,
    CurationGrant, CurationReview, CurationSource, CurationOperation, now)
from app.database import get_session
from app.schemas.curation import CaptionInput, ExportInput, MaterializeInput, RegistrationInput, ReviewInput
from app.services import curation_auth as auth
from app.services.curation_fixtures import create_fixture_dataset
from app.services.curation_plans import add_caption, detail, materialize, review
from app.services.curation_policy import PRODUCTION_POLICY_VERSION, authorize, install_curation_guard
from app.services.frozen_exports import admit_export, execute_export
from app.services.source_locations import descriptor_mount_id


def b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode()


class SyntheticAuthenticator:
    """A real ECDSA key with synthetic CTAP payloads; never a human claim."""
    def __init__(self):
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.identifier = secrets.token_bytes(32)
        self.counter = 0

    def credential(self, options, *, registration=False, origin='http://localhost:18123',
            rp='localhost', flags=None, challenge=None, cross_origin=False, user_handle=None):
        client_data = json.dumps({'type': 'webauthn.create' if registration else 'webauthn.get',
            'challenge': challenge or options['public_key']['challenge'], 'origin': origin,
            'crossOrigin': cross_origin}, separators=(',', ':')).encode()
        self.counter += 1
        flags = (0x45 if registration else 0x05) if flags is None else flags
        auth_data = hashlib.sha256(rp.encode()).digest() + bytes([flags]) + self.counter.to_bytes(4, 'big')
        if registration:
            public = self.key.public_key().public_numbers()
            cose = cbor2.dumps({1: 2, 3: -7, -1: 1, -2: public.x.to_bytes(32, 'big'), -3: public.y.to_bytes(32, 'big')})
            auth_data += bytes(16) + len(self.identifier).to_bytes(2, 'big') + self.identifier + cose
            response = {'clientDataJSON': b64(client_data),
                'attestationObject': b64(cbor2.dumps({'fmt': 'none', 'authData': auth_data, 'attStmt': {}})),
                'transports': ['internal']}
        else:
            signature = self.key.sign(auth_data + hashlib.sha256(client_data).digest(), ec.ECDSA(hashes.SHA256()))
            response = {'clientDataJSON': b64(client_data), 'authenticatorData': b64(auth_data),
                        'signature': b64(signature), 'userHandle': user_handle}
        return {'id': b64(self.identifier), 'rawId': b64(self.identifier), 'type': 'public-key',
                'response': response, 'clientExtensionResults': {}}


class ProductionAuthorityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='curation-authority-')
        self.root = Path(self.temp.name)
        self.source, self.store = self.root / 'source', self.root / 'store'
        self.source.mkdir(); self.store.mkdir()
        Image.new('RGB', (32, 24), '#3277aa').save(self.source / 'still.png')
        self.engine = create_engine('sqlite:///' + str(self.root / 'db.sqlite'), connect_args={'check_same_thread': False})
        @event.listens_for(self.engine, 'connect')
        def foreign_keys(connection, _):
            connection.execute('PRAGMA foreign_keys=ON')
        SQLModel.metadata.create_all(self.engine)
        self.env = patch.dict(os.environ, {'OMOIDE_CURATION_MODE': 'fixture',
            'OMOIDE_CURATION_RP_ID': 'localhost', 'OMOIDE_CURATION_ORIGIN': 'http://localhost:18123'})
        self.env.start()
        self.presentation = patch.object(settings.general, 'presentation_mode', False)
        self.presentation.start()
        with Session(self.engine) as session:
            seed = create_fixture_dataset(session, source_root=self.source, store_root=self.store,
                files=[{'relative_path': 'still.png', 'group_id': 'synthetic-capture'}])
            self.dataset_id = seed['dataset_id']; self.fixture_token = seed['credentials']['fixture_human']
            source_id = session.exec(select(CurationSource)).one().id
            prepared = materialize(session, seed['credentials']['agent'], self.dataset_id,
                MaterializeInput(source_id=source_id, expected_revision=0, idempotency_key='prepare-test'))
            prepared = add_caption(session, seed['credentials']['agent'], self.dataset_id,
                CaptionInput(artifact_id=prepared['items'][0]['artifact_id'], text='A geometric test rectangle.', expected_revision=1))
            item = prepared['items'][0]
            self.request = ReviewInput(expected_revision=2, artifact_id=item['artifact_id'], caption_id=item['caption']['id'],
                asset_sha256=item['sha256'], caption_sha256=item['caption']['sha256'], decision='accept')
            dataset = session.get(CurationDataset, self.dataset_id)
            with open(self.source / 'still.png', 'rb') as source_file:
                mount_id = descriptor_mount_id(source_file.fileno())
            info = self.source.stat(); namespace = os.stat('/proc/self/ns/mnt')
            self.volume = {'mount_id': mount_id, 'device': f'{os.major(info.st_dev)}:{os.minor(info.st_dev)}',
                'filesystem_root': '/', 'mountpoint': str(self.source), 'filesystem_type': 'test', 'source': 'test',
                'uuid': 'synthetic-volume', 'namespace_device': namespace.st_dev, 'namespace_inode': namespace.st_ino,
                'observer': 'findmnt+proc-mountinfo-v1'}
            dataset.policy_version = PRODUCTION_POLICY_VERSION
            dataset.policy = {'version': PRODUCTION_POLICY_VERSION, 'fixture_only': False, 'generative_enabled': False,
                              'max_items': 100, 'source_volume': self.volume, 'lineage_basis': 'synthetic authored pixels'}
            session.add(dataset); session.commit()
        os.environ['OMOIDE_CURATION_MODE'] = 'production'
        self.observer = patch('app.services.source_locations.observe_source_volume', return_value=self.volume)
        self.observer.start()
        self.human, self.human_id = self.issue('human', 'reviewer', ['read', 'enroll', 'review', 'preview', 'caption', 'export'], True)
        self.agent, self.agent_id = self.issue('agent', 'agent', ['read', 'caption', 'export'])
        self.authenticator = SyntheticAuthenticator()
        app = FastAPI(); install_curation_guard(app); app.include_router(router, prefix='/api/curation')
        def sessions():
            with Session(self.engine) as session:
                yield session
        app.dependency_overrides[get_session] = sessions
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close(); self.observer.stop(); self.presentation.stop(); self.env.stop()
        self.engine.dispose(); self.temp.cleanup()

    def issue(self, kind, actor, operations, disclosure=False):
        path = self.root / (actor + '.token')
        with Session(self.engine) as session:
            result = auth.issue_production_grant(session, dataset_id=self.dataset_id, actor_id=actor,
                actor_kind=kind, operations=operations, disclosure=disclosure, credential_file=path)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertNotIn('token', result)
        return path.read_text().strip(), result['grant_id']

    def headers(self, token=None):
        return {'Authorization': 'Bearer ' + (token or self.human)}

    def enroll(self, **kwargs):
        with Session(self.engine) as session:
            options = auth.registration_options(session, self.human)
            credential = self.authenticator.credential(options, registration=True, **kwargs)
            return auth.registration_verify(session, self.human,
                RegistrationInput(challenge_id=options['challenge_id'], credential=credential))

    def proof(self, request=None, **kwargs):
        request = request or self.request
        with Session(self.engine) as session:
            options = auth.review_options(session, self.human, self.dataset_id, request)
        credential = self.authenticator.credential(options, **kwargs)
        return request.model_copy(update={'presence': RegistrationInput(challenge_id=options['challenge_id'], credential=credential)})

    def accept(self, request):
        with Session(self.engine) as session:
            return review(session, self.human, self.dataset_id, request)

    def expect_error(self, code, callback, *args):
        with self.assertRaises(HTTPException) as caught:
            callback(*args)
        self.assertEqual(caught.exception.detail['code'], code)

    def test_real_signed_registration_review_and_frozen_export(self):
        self.enroll(); result = self.accept(self.proof())
        item = result['items'][0]
        self.assertTrue(item['eligible']); self.assertTrue(item['review']['human_presence_verified'])
        self.assertFalse(result['actor']['human_presence_verified'])
        self.assertEqual(item['review']['presence_evidence']['method'], 'webauthn')
        with Session(self.engine) as session:
            op_id = admit_export(session, self.agent, self.dataset_id, ExportInput(expected_revision=3, idempotency_key='signed-export'))
            receipt = execute_export(session, self.agent, op_id)
            snapshot = session.get(CurationOperation, op_id).snapshot
            self.assertEqual(receipt['status'], 'succeeded')
            self.assertFalse(snapshot['fixture_only'])
            self.assertEqual(snapshot['members'][0]['review_authority'], 'human')
            self.assertTrue(snapshot['members'][0]['human_presence_verified'])
            self.assertEqual(len(session.exec(select(CurationReview)).all()), 1)
            self.assertTrue(all(row.consumed_at for row in session.exec(select(CurationChallenge)).all()))

    def test_http_ceremonies_and_no_reusable_bearer_approval(self):
        status = self.client.get('/api/curation/status').json()
        self.assertEqual(status['mode'], 'production'); self.assertFalse(status['fixture_only'])
        options = self.client.post('/api/curation/auth/registration/options', headers=self.headers(), json={}).json()
        credential = self.authenticator.credential(options, registration=True)
        verified = self.client.post('/api/curation/auth/registration/verify', headers=self.headers(),
            json={'challenge_id': options['challenge_id'], 'credential': credential})
        self.assertEqual(verified.status_code, 200, verified.text)
        path = f'/api/curation/datasets/{self.dataset_id}'
        self.assertEqual(self.client.post(path + '/reviews', headers=self.headers(), json=self.request.model_dump()).status_code, 403)
        options_response = self.client.post(path + '/review-options', headers=self.headers(), json=self.request.model_dump())
        self.assertEqual(options_response.status_code, 200, options_response.text)
        options = options_response.json()
        body = self.request.model_dump(); body['presence'] = {'challenge_id': options['challenge_id'], 'credential': self.authenticator.credential(options)}
        result = self.client.post(path + '/reviews', headers=self.headers(), json=body)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.headers['cache-control'], 'no-store')

    def test_invalid_registration_protocol_fields(self):
        for case in ({'origin': 'http://evil.example'}, {'rp': 'evil.example'}, {'flags': 0x44}, {'flags': 0x41},
                     {'challenge': b64(b'wrong challenge')}, {'cross_origin': True}):
            with self.subTest(case=case):
                with Session(self.engine) as session:
                    options = auth.registration_options(session, self.human)
                    credential = self.authenticator.credential(options, registration=True, **case)
                    with self.assertRaises(HTTPException):
                        auth.registration_verify(session, self.human, RegistrationInput(challenge_id=options['challenge_id'], credential=credential))
                    self.assertEqual(session.exec(select(CurationCredential)).all(), [])

    def test_wrong_origin_rp_challenge_up_uv_and_embedding_rejected(self):
        self.enroll()
        for case in ({'origin': 'http://evil.example'}, {'rp': 'evil.example'}, {'flags': 0x04}, {'flags': 0x01},
                     {'challenge': b64(b'wrong challenge')}, {'cross_origin': True}, {'user_handle': b64(b'other user')}):
            with self.subTest(case=case):
                proof = self.proof(**case)
                with self.assertRaises(HTTPException): self.accept(proof)
                with Session(self.engine) as session:
                    self.assertEqual(session.exec(select(CurationReview)).all(), [])

    def test_corrupted_signature_and_wrong_credential_fail(self):
        self.enroll()
        proof = self.proof()
        proof.presence.credential['response']['signature'] = b64(b'not a signature')
        self.expect_error('presence_verification_failed', self.accept, proof)
        proof = self.proof(); proof.presence.credential['id'] = b64(b'other credential')
        self.expect_error('presence_verification_failed', self.accept, proof)

    def test_malformed_client_data_is_a_bounded_client_error(self):
        self.enroll(); proof = self.proof()
        proof.presence.credential['response']['clientDataJSON'] = b64(b'[]')
        self.expect_error('presence_response_invalid', self.accept, proof)

    def test_presence_required_for_all_decisions_and_changed_decision_or_rationale(self):
        self.enroll()
        for decision in ('accept', 'reject', 'defer'):
            request = self.request.model_copy(update={'decision': decision})
            self.expect_error('human_presence_required', self.accept, request)
        proof = self.proof()
        self.expect_error('presence_request_changed', self.accept, proof.model_copy(update={'decision': 'reject'}))
        self.expect_error('presence_request_changed', self.accept, proof.model_copy(update={'rationale': 'changed after signature'}))
        self.assertTrue(self.accept(proof)['items'][0]['eligible'])

    def test_one_use_replay_even_if_revision_is_replayed(self):
        self.enroll(); proof = self.proof(); self.accept(proof)
        self.expect_error('revision_conflict', self.accept, proof)
        # A stale client cannot hide replay by supplying the new draft revision.
        self.expect_error('presence_challenge_used', self.accept, proof.model_copy(update={'expected_revision': 3}))
        with Session(self.engine) as session:
            self.assertEqual(len(session.exec(select(CurationReview)).all()), 1)

    def test_expired_challenge(self):
        self.enroll(); proof = self.proof()
        with Session(self.engine) as session:
            row = session.get(CurationChallenge, proof.presence.challenge_id); row.expires_at = now() - timedelta(seconds=1)
            session.add(row); session.commit()
        self.expect_error('presence_challenge_expired', self.accept, proof)

    def test_caption_edit_after_challenge_invalidates_review(self):
        self.enroll(); proof = self.proof()
        with Session(self.engine) as session:
            add_caption(session, self.agent, self.dataset_id, CaptionInput(expected_revision=2, artifact_id=self.request.artifact_id, text='Changed caption.'))
        self.expect_error('revision_conflict', self.accept, proof)
        self.expect_error('review_hash_conflict', self.accept, proof.model_copy(update={'expected_revision': 3}))

    def test_asset_hash_policy_and_grant_binding(self):
        self.enroll(); proof = self.proof()
        self.expect_error('review_hash_conflict', self.accept, proof.model_copy(update={'asset_sha256': '0' * 64}))
        other, _ = self.issue('human', 'other', ['read', 'review', 'enroll'])
        with Session(self.engine) as session:
            self.expect_error('presence_challenge_invalid', review, session, other, self.dataset_id, proof)
            dataset = session.get(CurationDataset, self.dataset_id); dataset.policy = {**dataset.policy, 'changed': True}
            session.add(dataset); session.commit()
        self.expect_error('presence_request_changed', self.accept, proof)

    def test_agent_fixture_and_headerless_cannot_enroll_or_review(self):
        for token in (self.agent, self.fixture_token):
            with Session(self.engine) as session:
                with self.assertRaises(HTTPException): auth.registration_options(session, token)
                with self.assertRaises(HTTPException): review(session, token, self.dataset_id, self.request)
        self.assertEqual(self.client.post('/api/curation/auth/registration/options', json={}).status_code, 401)
        with Session(self.engine) as session:
            self.assertEqual(session.exec(select(CurationCredential)).all(), [])

    def test_revoked_key_blocks_pending_review_and_reenrollment(self):
        self.enroll(); proof = self.proof()
        with Session(self.engine) as session:
            credential = session.exec(select(CurationCredential)).one()
            auth.revoke_production_authority(session, grant_id=self.human_id, credential_id=credential.id)
            self.expect_error('credential_already_enrolled', auth.registration_options, session, self.human)
            self.assertEqual(auth.auth_status(session, self.human)['enrollment_available'], False)
        self.expect_error('review_authority_revoked', self.accept, proof)

    def test_repeated_registration_cannot_replace_key(self):
        self.enroll()
        with Session(self.engine) as session:
            self.expect_error('credential_already_enrolled', auth.registration_options, session, self.human)

    def test_revocation_blocks_admitted_export_without_rewriting_snapshot(self):
        self.enroll(); self.accept(self.proof())
        with Session(self.engine) as session:
            op_id = admit_export(session, self.agent, self.dataset_id, ExportInput(expected_revision=3, idempotency_key='revoked-export'))
            original = session.get(CurationOperation, op_id).snapshot
            credential = session.exec(select(CurationCredential)).one()
            auth.revoke_production_authority(session, grant_id=self.human_id, credential_id=credential.id)
            self.expect_error('review_authority_revoked', execute_export, session, self.agent, op_id)
            self.assertEqual(session.get(CurationOperation, op_id).snapshot, original)
            self.assertFalse((self.store / 'exports' / op_id).exists())

    def test_production_manifest_omits_private_policy_locators(self):
        private_path = '/private/SENTINEL-CURATION-SOURCE/evidence.json'
        private_uuid = 'SENTINEL-PRIVATE-UUID'
        with Session(self.engine) as session:
            dataset = session.get(CurationDataset, self.dataset_id)
            self.volume = {**self.volume, 'uuid': private_uuid}
            # The observer mock, rather than application code, supplies the
            # synthetic volume. Exact descriptor and hash checks still execute.
            from app.services import source_locations
            source_locations.observe_source_volume.return_value = self.volume
            dataset.policy = {**dataset.policy, 'source_volume': self.volume,
                'registration': {'source_assertions': [{'relative_path': 'SENTINEL-PRIVATE-RELATIVE.png',
                    'attestation': {'evidence_reference': private_path}}]}}
            session.add(dataset); session.commit()
        self.enroll(); self.accept(self.proof())
        with Session(self.engine) as session:
            op_id = admit_export(session, self.agent, self.dataset_id, ExportInput(expected_revision=3, idempotency_key='private-manifest'))
            receipt = execute_export(session, self.agent, op_id)
            snapshot = session.get(CurationOperation, op_id).snapshot
            manifest_bytes = (self.store / 'exports' / op_id / 'manifest.json').read_bytes()
            manifest_text = manifest_bytes.decode()
            self.assertIn(private_path, json.dumps(snapshot))
            for sentinel in (private_path, private_uuid, 'SENTINEL-PRIVATE-RELATIVE.png', self.human, self.agent, str(self.source)):
                self.assertNotIn(sentinel, manifest_text)
            manifest = json.loads(manifest_bytes)
            self.assertEqual(manifest['policy_sha256'], auth.digest(auth.canonical(snapshot['policy'])))
            self.assertEqual(manifest['source_registration_sha256'], auth.digest(auth.canonical(snapshot['policy']['registration'])))
            self.assertEqual(receipt['manifest_sha256'], hashlib.sha256(manifest_bytes).hexdigest())
            self.assertNotIn('origin', manifest['members'][0]['review_evidence'])
            self.assertNotIn('rp_id', manifest['members'][0]['review_evidence'])

    def test_redacted_production_manifest_reconciles_after_publication_crash(self):
        self.enroll(); self.accept(self.proof())
        with Session(self.engine) as session:
            op_id = admit_export(session, self.agent, self.dataset_id, ExportInput(expected_revision=3, idempotency_key='redacted-recovery'))
            def crash(stage):
                if stage == 'after_publication':
                    raise RuntimeError('synthetic crash')
            with self.assertRaisesRegex(RuntimeError, 'synthetic crash'):
                execute_export(session, self.agent, op_id, checkpoint=crash)
            published = self.store / 'exports' / op_id / 'manifest.json'
            initial = published.read_bytes()
            recovered = execute_export(session, self.agent, op_id)
            self.assertEqual(recovered['status'], 'succeeded')
            self.assertEqual(recovered['manifest_sha256'], hashlib.sha256(initial).hexdigest())
            self.assertEqual(published.read_bytes(), initial)

    def test_revoked_grant_and_expired_grant_are_rejected(self):
        self.enroll(); proof = self.proof()
        with Session(self.engine) as session:
            grant = session.get(CurationGrant, self.human_id); grant.expires_at = now() - timedelta(seconds=1)
            session.add(grant); session.commit()
        self.expect_error('unauthorized', self.accept, proof)
        with Session(self.engine) as session:
            grant = session.get(CurationGrant, self.human_id); grant.expires_at = now() + timedelta(hours=1); session.add(grant); session.commit()
            auth.revoke_production_authority(session, grant_id=self.human_id)
        self.expect_error('unauthorized', self.accept, proof)

    def test_presentation_mode_blocks_ceremonies_and_direct_review(self):
        self.enroll(); proof = self.proof()
        with patch.object(settings.general, 'presentation_mode', True), Session(self.engine) as session:
            self.expect_error('presentation_mode', auth.registration_options, session, self.human)
            self.expect_error('presentation_mode', auth.review_options, session, self.human, self.dataset_id, self.request)
            self.expect_error('presentation_mode', review, session, self.human, self.dataset_id, proof)

    def test_config_is_server_owned_and_changes_invalidate_challenge(self):
        self.enroll(); proof = self.proof()
        with patch.dict(os.environ, {'OMOIDE_CURATION_ORIGIN': 'http://localhost:18124'}):
            self.expect_error('presence_configuration_changed', self.accept, proof)
        for invalid in ('http://evil.example', 'http://localhost:18123/', 'http://localhost:18123#x', 'https://user@localhost'):
            with patch.dict(os.environ, {'OMOIDE_CURATION_ORIGIN': invalid}):
                self.expect_error('presence_configuration_invalid', auth.relying_party)

    def test_generative_ancestry_remains_blocked(self):
        self.enroll()
        with Session(self.engine) as session:
            source = session.exec(select(CurationSource)).one(); source.generative = True; session.add(source); session.commit()
            self.expect_error('generative_disabled', auth.review_options, session, self.human, self.dataset_id, self.request)

    def test_challenge_wrong_purpose_and_bounded_payload(self):
        with Session(self.engine) as session:
            registration = auth.registration_options(session, self.human)
        self.enroll(); proof = self.proof()
        proof.presence.challenge_id = registration['challenge_id']
        self.expect_error('presence_challenge_invalid', self.accept, proof)
        proof = self.proof(); proof.presence.credential['padding'] = 'x' * 65536
        self.expect_error('presence_response_invalid', self.accept, proof)


class LegacyProductionGuardTests(unittest.TestCase):
    def test_exact_routes_and_reviewed_at_only_patch_are_blocked_without_actor_trust(self):
        app = FastAPI(); install_curation_guard(app)
        @app.api_route('/{path:path}', methods=['GET', 'POST', 'PATCH', 'PUT', 'DELETE'])
        async def legacy(request: Request):
            return {'ok': True, 'body': (await request.body()).decode()}
        paths = [('PATCH', '/api/datasets/1/items/2/caption'), ('POST', '/api/datasets/1/items/2/caption/reviewed'),
                 ('POST', '/api/datasets/1/items/2/review'), ('POST', '/api/annotations/annotations/a/revisions'),
                 ('POST', '/api/annotations/annotations/a/approve')]
        with patch.dict(os.environ, {'OMOIDE_CURATION_MODE': 'production'}), TestClient(app) as client:
            for method, path in paths:
                for headers in ({}, {'Authorization': 'Bearer forged', 'X-Actor-Type': 'human'}):
                    for suffix in ('', '/', '?irrelevant=true'):
                        response = client.request(method, path + suffix, headers=headers, json={})
                        self.assertEqual(response.status_code, 403, (path, response.text))
            self.assertEqual(client.patch('/api/datasets/1/items/2', json={'reviewed_at': None}).status_code, 403)
            self.assertEqual(client.patch('/api/datasets/1/items/2', json={'reviewed_at': '2020-01-01'}).status_code, 403)
            allowed = client.patch('/api/datasets/1/items/2', json={'excluded': True})
            self.assertEqual(allowed.status_code, 200); self.assertEqual(json.loads(allowed.json()['body']), {'excluded': True})
            self.assertEqual(client.post('/api/config/reload', json={}).status_code, 200)
            self.assertEqual(client.get('/api/datasets/1/items/2/review').status_code, 200)
        with patch.dict(os.environ, {'OMOIDE_CURATION_MODE': 'fixture'}), TestClient(app) as client:
            self.assertEqual(client.post('/api/config/reload', json={}).status_code, 403)
        with patch.dict(os.environ, {'OMOIDE_CURATION_MODE': 'disabled'}), TestClient(app) as client:
            self.assertEqual(client.post('/api/datasets/1/items/2/review', json={}).status_code, 200)


class AuthorityMigrationTests(unittest.TestCase):
    def test_additive_migration_and_populated_refusal(self):
        from alembic.migration import MigrationContext
        from alembic.operations import Operations
        root = Path(__file__).resolve().parents[1]
        migrations = []
        for filename in ('5f60718293a4_fixture_still_curation.py', '60718293a4b5_curation_human_authority.py'):
            spec = importlib.util.spec_from_file_location(filename[:-3], root / 'alembic' / 'versions' / filename)
            module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module); migrations.append(module)
        self.assertEqual(migrations[1].down_revision, '5f60718293a4')
        engine = create_engine('sqlite://')
        with engine.begin() as connection, Operations.context(MigrationContext.configure(connection)):
            migrations[0].upgrade(); migrations[1].upgrade()
            self.assertEqual(connection.exec_driver_sql('PRAGMA foreign_key_check').all(), [])
            self.assertEqual(connection.exec_driver_sql('PRAGMA integrity_check').scalar(), 'ok')
            migrations[1].downgrade(); migrations[1].upgrade()
            connection.exec_driver_sql("INSERT INTO curation_challenge (id,grant_id,purpose,challenge,request_sha256,rp_id,origin,expires_at,created_at) VALUES ('x','x','register','x','x','localhost','http://localhost','2026-01-01','2026-01-01')")
            with self.assertRaisesRegex(RuntimeError, 'Refusing downgrade'):
                migrations[1].downgrade()
        engine.dispose()
