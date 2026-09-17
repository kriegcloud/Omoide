"""The local operator CLI end to end against a task-owned migrated database copy."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from fastapi import HTTPException
from PIL import Image
from sqlalchemy import text
from sqlmodel import SQLModel, create_engine

from app.services.curation_policy import digest
from app.services.source_locations import observe_source_volume

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / 'scripts' / 'curation-authority.py'


class OperatorCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        try:
            self.observed = observe_source_volume(str(self.root))
        except HTTPException as exc:  # pragma: no cover - environment without findmnt UUIDs
            self.skipTest(f'volume identity unavailable here: {exc.detail}')
        self.sources, self.store = self.root / 'sources', self.root / 'store'
        self.sources.mkdir()
        self.store.mkdir()
        Image.new('RGB', (16, 12), '#123456').save(self.sources / 'one.png')
        self.database = self.root / 'omoide.db'
        engine = create_engine('sqlite:///' + str(self.database))
        SQLModel.metadata.create_all(engine)
        with engine.begin() as connection:
            connection.execute(text('CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)'))
            connection.execute(text("INSERT INTO alembic_version VALUES ('60718293a4b5')"))
        engine.dispose()
        manifest = {'schema_version': 'omoide.source-registration/v1', 'name': 'CLI fixture',
            'subject_id': 'cli-subject', 'source_root': str(self.sources), 'store_root': str(self.store),
            'expected_filesystem_uuid': self.observed['uuid'], 'expected_mountpoint': self.observed['mountpoint'],
            'attestation': {'operator_id': 'cli-operator', 'attestation_id': 'cli-attestation',
                'statement': 'Generated geometric fixture; test-only operator claim.',
                'evidence_reference': 'fixture:cli', 'rights_to_curate': True, 'subject_identity_attested': True},
            'files': [{'relative_path': 'one.png', 'sha256': digest((self.sources / 'one.png').read_bytes()),
                'label': 'Fixture', 'subject_id': 'cli-subject', 'group_id': 'capture-one', 'split': 'train',
                'ancestry': {'kind': 'original_capture', 'evidence_reference': 'fixture:cli',
                    'statement': 'Pillow primitive; no model.', 'ancestry_complete': True, 'parent_sha256': []}}]}
        self.manifest = self.root / 'manifest.json'
        self.manifest.write_text(json.dumps(manifest))

    def run_cli(self, *arguments, mode='production'):
        env = {k: v for k, v in os.environ.items() if k != 'IS_DOCKER'}
        env['OMOIDE_CURATION_MODE'] = mode
        env['XDG_CONFIG_HOME'] = str(self.root / 'config')
        result = subprocess.run([sys.executable, str(CLI), '--database', str(self.database), *arguments],
                                cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
        # The application logger prints a startup line on stdout; the receipt is the
        # last JSON line on stdout (success) or stderr (failure).
        receipts = [line for line in (result.stdout + result.stderr).splitlines() if line.startswith('{')]
        payload = json.loads(receipts[-1]) if receipts else None
        return result.returncode, payload

    def test_register_verify_reattest_grant_revoke(self):
        code, payload = self.run_cli('register', '--manifest', str(self.manifest))
        self.assertEqual((code, payload['ok']), (0, True), payload)
        dataset_id = payload['result']['dataset_id']
        self.assertEqual(payload['result']['source_count'], 1)

        code, payload = self.run_cli('verify', '--dataset-id', dataset_id)
        self.assertEqual((code, payload['ok']), (0, True), payload)
        self.assertTrue(payload['result']['current_identity_valid'])
        self.assertEqual(payload['result']['runtime_drift'], [])

        code, payload = self.run_cli('reattest', '--dataset-id', dataset_id,
                                     '--operator-id', 'cli-operator', '--statement', 'test re-pin')
        self.assertEqual((code, payload['ok']), (0, True), payload)
        self.assertEqual(payload['result']['revision'], 1)

        credential = self.root / 'human.token'
        code, payload = self.run_cli('grant', '--dataset-id', dataset_id, '--actor-id', 'cli-human',
                                     '--actor-kind', 'human', '--credential-file', str(credential))
        self.assertEqual((code, payload['ok']), (0, True), payload)
        self.assertEqual(oct(credential.stat().st_mode & 0o777), '0o600')
        self.assertNotIn(credential.read_text().strip(), json.dumps(payload))
        grant_id = payload['result']['grant_id']

        code, payload = self.run_cli('revoke', '--grant-id', grant_id)
        self.assertEqual((code, payload['ok']), (0, True), payload)

    def test_refuses_without_production_mode_or_migrated_database(self):
        code, _ = self.run_cli('verify', '--dataset-id', 'x', mode='fixture')
        self.assertNotEqual(code, 0)
        engine = create_engine('sqlite:///' + str(self.database))
        with engine.begin() as connection:
            connection.execute(text("UPDATE alembic_version SET version_num = '5f60718293a4'"))
        engine.dispose()
        code, _ = self.run_cli('verify', '--dataset-id', 'x')
        self.assertNotEqual(code, 0)

    def test_reports_missing_dataset_without_leaking_rows(self):
        code, payload = self.run_cli('verify', '--dataset-id', 'does-not-exist')
        self.assertEqual((code, payload), (1, {'ok': False, 'error': {'code': 'not_found'}}))
