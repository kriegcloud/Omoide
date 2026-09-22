"""Source-root identity on filesystems whose inode numbers are not persistent.

The Linux exfat and fat drivers assign inode numbers with iunique() when an
inode is instantiated, so a registered source root on such media reports a new
inode after cache eviction or a remount even though nothing was replaced. The
fence there is carried by the pinned volume identity, relative paths and file
hashes; the strict inode fence stays in force everywhere else.
"""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from PIL import Image
from sqlmodel import Session, SQLModel, create_engine, select

from app.curation_models import CurationDataset, CurationSource
from app.services import curation_registration as registration, source_locations as locations
from app.services.curation_artifacts import directory, source_bytes
from app.services.curation_policy import digest


class RootIdentityTests(unittest.TestCase):
    filesystem_type = 'exfat'

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.sources, self.store = self.root / 'sources', self.root / 'store'
        self.sources.mkdir()
        self.store.mkdir()
        Image.new('RGB', (16, 12), '#446622').save(self.sources / 'one.png')
        self.initial = (self.sources / 'one.png').read_bytes()
        self.engine = create_engine('sqlite:///' + str(self.root / 'test.sqlite'))
        self.addCleanup(self.engine.dispose)
        SQLModel.metadata.create_all(self.engine)
        with directory(str(self.sources)) as fd:
            self.mount_id = locations.descriptor_mount_id(fd)
        self.namespace = (5, 4026531832)
        self.mount = {'mount_id': self.mount_id, 'device': '8:17', 'filesystem_root': '/',
                      'mountpoint': str(self.root), 'filesystem_type': self.filesystem_type,
                      'source': '/dev/test-removable'}
        for target, attribute, side_effect in (
                (locations, '_mount_table', lambda: {self.mount['mount_id']: deepcopy(self.mount)}),
                (locations, '_namespace_identity', lambda: self.namespace),
                (locations, 'descriptor_mount_id', lambda fd: self.mount['mount_id']),
                (locations.subprocess, 'run', self.findmnt)):
            patcher = patch.object(target, attribute, side_effect=side_effect)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.manifest = {'schema_version': 'omoide.source-registration/v1',
            'name': 'Removable-media fixture', 'subject_id': 'fixture-subject',
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
        self.attestation = {'operator_id': 'fixture-operator', 'statement': 'simulated remount'}

    def findmnt(self, *_args, **_kwargs):
        mount = self.mount
        row = {'uuid': 'fixture-uuid', 'id': mount['mount_id'], 'maj:min': mount['device'],
               'fsroot': mount['filesystem_root'], 'target': mount['mountpoint'],
               'fstype': mount['filesystem_type'], 'source': mount['source']}
        return SimpleNamespace(returncode=0, stdout=json.dumps({'filesystems': [row]}))

    def dataset(self):
        with Session(self.engine) as session:
            dataset = session.get(CurationDataset, self.dataset_id)
            session.expunge(dataset)
            return dataset

    def read_source(self):
        with Session(self.engine) as session:
            dataset = session.get(CurationDataset, self.dataset_id)
            source = session.exec(select(CurationSource)).one()
            return source_bytes(dataset, source)

    def replace_source_root(self, data: bytes):
        """Same path, same bytes unless told otherwise, new directory inode."""
        replaced = self.root / 'replacement'
        replaced.mkdir()
        (replaced / 'one.png').write_bytes(data)
        (self.sources / 'one.png').unlink()
        self.sources.rmdir()
        replaced.rename(self.sources)

    def assert_code(self, code, function, *args, **kwargs):
        with self.assertRaises(HTTPException) as raised:
            function(*args, **kwargs)
        self.assertEqual(raised.exception.detail['code'], code)

    def test_registration_records_that_the_source_inode_is_not_persistent(self):
        record = self.dataset().policy['root_identity']
        self.assertEqual(record, {'basis': 'filesystem_type', 'filesystem_type': 'exfat',
                                  'source_inode_persistent': False, 'store_inode_persistent': True})
        self.assertEqual(locations.source_root_identity(self.dataset()),
                         (self.dataset().source_device, None))

    def test_new_root_inode_with_identical_bytes_is_not_a_replacement(self):
        before = self.dataset()
        self.replace_source_root(self.initial)
        self.assertNotEqual(self.sources.stat().st_ino, before.source_inode)
        self.assertEqual(self.read_source(), self.initial)
        with Session(self.engine) as session:
            report = registration.verify_dataset_volume(session, self.dataset_id)
        self.assertTrue(report['current_identity_valid'])
        self.assertEqual(report['source_count'], 1)
        with Session(self.engine) as session:
            result = registration.reattest_source_volume(session, self.dataset_id, self.attestation)
        after = self.dataset()
        self.assertEqual(result['revision'], before.revision + 1)
        self.assertEqual(after.source_inode, self.sources.stat().st_ino)
        self.assertEqual(after.policy['reattestations'][-1]['previous_source_inode'], before.source_inode)

    def test_new_root_inode_with_different_bytes_is_still_refused(self):
        other = self.initial[:-1] + bytes([self.initial[-1] ^ 0xFF])
        self.replace_source_root(other)
        self.assert_code('hash_mismatch', self.read_source)
        with Session(self.engine) as session:
            self.assert_code('hash_mismatch', registration.verify_dataset_volume, session, self.dataset_id)
        with Session(self.engine) as session:
            self.assert_code('hash_mismatch', registration.reattest_source_volume,
                             session, self.dataset_id, self.attestation)

    def test_device_and_store_root_fences_are_unchanged(self):
        dataset = self.dataset()
        dataset.source_device += 1
        self.assert_code('root_identity_changed', locations.verify_source_volume, dataset)
        replaced = self.root / 'store-replacement'
        replaced.mkdir()
        self.store.rmdir()
        replaced.rename(self.store)
        with Session(self.engine) as session:
            self.assert_code('root_identity_changed', registration.verify_dataset_volume, session, self.dataset_id)

    def test_policy_cannot_relax_the_fence_on_a_persistent_filesystem(self):
        dataset = self.dataset()
        policy = deepcopy(dataset.policy)
        policy['source_volume']['filesystem_type'] = 'ext4'
        self.mount['filesystem_type'] = 'ext4'
        dataset.policy = policy
        self.assert_code('source_volume_policy_required', locations.verify_source_volume, dataset)
        policy = deepcopy(dataset.policy)
        policy['root_identity']['filesystem_type'] = 'ext4'
        dataset.policy = policy
        self.assert_code('source_volume_policy_required', locations.verify_source_volume, dataset)
        policy = deepcopy(dataset.policy)
        policy['root_identity']['source_inode_persistent'] = True
        dataset.policy = policy
        self.assertEqual(locations.source_root_identity(dataset), (dataset.source_device, dataset.source_inode))


class PersistentRootIdentityTests(RootIdentityTests):
    filesystem_type = 'btrfs'

    def test_registration_records_that_the_source_inode_is_not_persistent(self):
        record = self.dataset().policy['root_identity']
        self.assertEqual(record['source_inode_persistent'], True)
        self.assertEqual(locations.source_root_identity(self.dataset()),
                         (self.dataset().source_device, self.dataset().source_inode))

    def test_new_root_inode_with_identical_bytes_is_not_a_replacement(self):
        self.replace_source_root(self.initial)
        self.assert_code('root_identity_changed', self.read_source)
        with Session(self.engine) as session:
            self.assert_code('root_identity_changed', registration.verify_dataset_volume,
                             session, self.dataset_id)

    def test_new_root_inode_with_different_bytes_is_still_refused(self):
        self.replace_source_root(b'x')
        self.assert_code('root_identity_changed', self.read_source)

    def test_policy_cannot_relax_the_fence_on_a_persistent_filesystem(self):
        dataset = self.dataset()
        policy = deepcopy(dataset.policy)
        policy['root_identity']['source_inode_persistent'] = False
        dataset.policy = policy
        self.assert_code('source_volume_policy_required', locations.verify_source_volume, dataset)
        del policy['root_identity']
        dataset.policy = dict(policy)
        self.assertEqual(locations.source_root_identity(dataset), (dataset.source_device, dataset.source_inode))


if __name__ == '__main__':
    unittest.main()
