"""Explicit runtime re-attestation: restarts fence reads until an operator re-pins."""
from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from PIL import Image
from sqlmodel import Session, SQLModel, create_engine, select

from app.curation_models import CurationDataset, CurationSource
from app.services import curation_artifacts as artifacts, curation_registration as registration, \
    source_locations as locations
from app.services.curation_artifacts import directory, source_bytes
from app.services.curation_policy import digest


class ReattestationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.sources, self.store = self.root / 'sources', self.root / 'store'
        self.sources.mkdir()
        self.store.mkdir()
        Image.new('RGB', (16, 12), '#664422').save(self.sources / 'one.png')
        self.initial = (self.sources / 'one.png').read_bytes()
        self.engine = create_engine('sqlite:///' + str(self.root / 'test.sqlite'))
        self.addCleanup(self.engine.dispose)
        SQLModel.metadata.create_all(self.engine)
        with directory(str(self.sources)) as fd:
            self.real_mount_id = locations.descriptor_mount_id(fd)
        self.mount_id = self.real_mount_id
        self.uuid = 'fixture-uuid'
        self.namespace = (5, 4026531832)
        self.mount = {'mount_id': self.mount_id, 'device': '8:7', 'filesystem_root': '/',
                      'mountpoint': str(self.root), 'filesystem_type': 'btrfs', 'source': '/dev/test-fixture'}
        for target, attribute, side_effect in (
                (locations, '_mount_table', lambda: {self.mount['mount_id']: deepcopy(self.mount)}),
                (locations, '_namespace_identity', lambda: self.namespace),
                (locations, 'descriptor_mount_id', lambda fd: self.mount['mount_id']),
                (locations.subprocess, 'run', self.findmnt)):
            patcher = patch.object(target, attribute, side_effect=side_effect)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.manifest = {'schema_version': 'omoide.source-registration/v1',
            'name': 'Reattestation fixture', 'subject_id': 'fixture-subject',
            'source_root': str(self.sources), 'store_root': str(self.store),
            'expected_filesystem_uuid': 'fixture-uuid', 'expected_mountpoint': str(self.root),
            'attestation': {'operator_id': 'fixture-operator', 'attestation_id': 'fixture-attestation',
                'statement': 'Rights-clear geometric fixture, operator attestation only.',
                'evidence_reference': 'fixture:rights-clear', 'rights_to_curate': True,
                'subject_identity_attested': True},
            'files': [{'relative_path': 'one.png', 'sha256': digest(self.initial),
                'label': 'Geometric fixture', 'subject_id': 'fixture-subject',
                'group_id': 'capture-one', 'split': 'train',
                'ancestry': {'kind': 'original_capture', 'evidence_reference': 'fixture:generated-test',
                    'statement': 'CPU fixture generator; simulated operator claim for tests only.',
                    'ancestry_complete': True, 'parent_sha256': []}}]}
        with Session(self.engine) as session:
            self.dataset_id = registration.register_source_manifest(session, self.manifest)['dataset_id']
        self.attestation = {'operator_id': 'fixture-operator', 'statement': 'simulated container restart'}

    def findmnt(self, *_args, **_kwargs):
        mount = self.mount
        row = {'uuid': self.uuid, 'id': mount['mount_id'], 'maj:min': mount['device'],
               'fsroot': mount['filesystem_root'], 'target': mount['mountpoint'],
               'fstype': mount['filesystem_type'], 'source': mount['source']}
        return SimpleNamespace(returncode=0, stdout=json.dumps({'filesystems': [row]}))

    def simulate_restart(self):
        """A new runtime: new kernel mount id, new mount namespace, new device node."""
        self.mount = {**self.mount, 'mount_id': self.mount['mount_id'] + 1000, 'source': '/dev/test-fixture-b'}
        self.namespace = (5, 4026533717)

    def snapshot(self):
        with Session(self.engine) as session:
            dataset = session.get(CurationDataset, self.dataset_id)
            return dataset.revision, deepcopy(dataset.policy), dataset.source_device, dataset.store_device

    def read_source(self):
        with Session(self.engine) as session:
            dataset = session.get(CurationDataset, self.dataset_id)
            source = session.exec(select(CurationSource)).one()
            return source_bytes(dataset, source)

    def assert_code(self, code, function, *args, **kwargs):
        with self.assertRaises(HTTPException) as raised:
            function(*args, **kwargs)
        self.assertEqual(raised.exception.detail['code'], code)

    def test_runtime_drift_fences_reads_until_explicit_reattestation(self):
        self.assertEqual(self.read_source(), self.initial)
        before = self.snapshot()
        self.simulate_restart()
        self.assert_code('source_volume_changed', self.read_source)
        with Session(self.engine) as session:
            report = registration.verify_dataset_volume(session, self.dataset_id)
        self.assertFalse(report['current_identity_valid'])
        self.assertEqual(report['runtime_drift'], ['mount_id', 'source', 'namespace_inode'])
        self.assertEqual(self.snapshot(), before, 'verify must not write')
        self.assert_code('source_volume_changed', self.read_source)
        with Session(self.engine) as session:
            result = registration.reattest_source_volume(session, self.dataset_id, self.attestation)
        revision, policy, source_device, _ = self.snapshot()
        self.assertEqual(result['revision'], before[0] + 1)
        self.assertEqual(revision, before[0] + 1)
        self.assertEqual(policy['source_volume']['mount_id'], self.mount['mount_id'])
        self.assertEqual(policy['source_volume']['namespace_inode'], 4026533717)
        self.assertEqual(policy['source_volume']['uuid'], 'fixture-uuid')
        audit = policy['reattestations']
        self.assertEqual(len(audit), 1)
        self.assertEqual(audit[0]['basis'], 'operator_reattestation')
        self.assertEqual(audit[0]['previous_source_volume'], before[1]['source_volume'])
        self.assertEqual(audit[0]['runtime_drift'], ['mount_id', 'source', 'namespace_inode'])
        self.assertEqual(audit[0]['files_verified'], 1)
        self.assertEqual({k: v for k, v in policy.items() if k not in {'source_volume', 'reattestations'}},
                         {k: v for k, v in before[1].items() if k != 'source_volume'},
                         'no other policy field may change')
        self.assertEqual(self.read_source(), self.initial)
        with Session(self.engine) as session:
            self.assertTrue(registration.verify_dataset_volume(session, self.dataset_id)['current_identity_valid'])

    def test_reattestation_refuses_a_different_volume(self):
        before = self.snapshot()
        self.simulate_restart()
        self.uuid = 'other-uuid'
        with Session(self.engine) as session:
            self.assert_code('source_volume_identity_mismatch', registration.verify_dataset_volume, session, self.dataset_id)
        with Session(self.engine) as session:
            self.assert_code('source_volume_identity_mismatch', registration.reattest_source_volume,
                             session, self.dataset_id, self.attestation)
        self.assertEqual(self.snapshot(), before)
        self.mount = {**self.mount, 'filesystem_root': '/@other'}
        self.uuid = 'fixture-uuid'
        with Session(self.engine) as session:
            self.assert_code('source_volume_identity_mismatch', registration.reattest_source_volume,
                             session, self.dataset_id, self.attestation)
        self.assertEqual(self.snapshot(), before)

    def test_reattestation_refuses_modified_or_missing_source_bytes(self):
        before = self.snapshot()
        self.simulate_restart()
        Image.new('RGB', (16, 12), '#000000').save(self.sources / 'one.png')
        with Session(self.engine) as session:
            self.assert_code('hash_mismatch', registration.reattest_source_volume,
                             session, self.dataset_id, self.attestation)
        self.assertEqual(self.snapshot(), before)
        (self.sources / 'one.png').unlink()
        with Session(self.engine) as session:
            self.assert_code('source_unavailable', registration.reattest_source_volume,
                             session, self.dataset_id, self.attestation)
        self.assertEqual(self.snapshot(), before)

    def test_reattestation_refuses_replaced_roots_and_bad_attestations(self):
        before = self.snapshot()
        with Session(self.engine) as session:
            self.assert_code('invalid_registration_manifest', registration.reattest_source_volume,
                             session, self.dataset_id, {'operator_id': '', 'statement': 'x'})
            self.assert_code('invalid_registration_manifest', registration.reattest_source_volume,
                             session, self.dataset_id, {'operator_id': 'op', 'statement': 'x', 'extra': 1})
            self.assert_code('not_found', registration.reattest_source_volume, session, 'missing', self.attestation)
        replaced = self.root / 'replacement'
        replaced.mkdir()
        (self.sources / 'one.png').rename(replaced / 'one.png')
        self.sources.rmdir()
        replaced.rename(self.sources)
        with Session(self.engine) as session:
            self.assert_code('root_identity_changed', registration.reattest_source_volume,
                             session, self.dataset_id, self.attestation)
        self.assertEqual(self.snapshot(), before)

    def test_reattestation_invalidates_revision_bound_operations(self):
        from app.services.curation_policy import require_revision
        before = self.snapshot()
        with Session(self.engine) as session:
            registration.reattest_source_volume(session, self.dataset_id, self.attestation)
            dataset = session.get(CurationDataset, self.dataset_id)
            self.assert_code('revision_conflict', require_revision, dataset, before[0])
            require_revision(dataset, before[0] + 1)


class StoreCapabilityTests(unittest.TestCase):
    def test_probe_accepts_posix_directory_and_leaves_nothing_behind(self):
        with tempfile.TemporaryDirectory() as temp, directory(temp) as fd:
            artifacts.probe_store_capabilities(fd)
            self.assertEqual(os.listdir(temp), [])

    def test_probe_refuses_filesystems_without_links_or_enforced_modes(self):
        with tempfile.TemporaryDirectory() as temp, directory(temp) as fd:
            with patch.object(artifacts.os, 'link', side_effect=PermissionError('exfat')):
                with self.assertRaises(HTTPException) as raised:
                    artifacts.probe_store_capabilities(fd)
            self.assertEqual(raised.exception.detail['code'], 'store_root_unsupported_filesystem')
            self.assertEqual(os.listdir(temp), [])
            with patch.object(artifacts.os, 'fchmod', side_effect=lambda *_: None):
                with self.assertRaises(HTTPException) as raised:
                    artifacts.probe_store_capabilities(fd)
            self.assertEqual(raised.exception.detail['code'], 'store_root_unsupported_filesystem')
            self.assertEqual(os.listdir(temp), [])


if __name__ == '__main__':
    unittest.main()
