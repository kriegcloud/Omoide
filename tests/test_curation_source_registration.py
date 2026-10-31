"""CPU-only bounded fixture manifests and simulated kernel metadata fault cases."""
from copy import deepcopy
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from PIL import Image
from sqlalchemy import event, text
from sqlmodel import Session, SQLModel, create_engine, select

from app.curation_models import CurationDataset, CurationGrant, CurationSource
from app.models import (AnnotationAuthor, AnnotationKind, AnnotationReviewStatus, DatasetCaptionSource,
    DatasetItem, Media, MediaAnnotation, TrainingDataset)
from app.services import curation_registration as registration, source_locations as locations
from app.services.curation_artifacts import directory, source_bytes
from app.services.curation_legacy_plan import plan_legacy_dataset_items
from app.services.curation_policy import digest


class SourceRegistrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.sources, self.store = self.root / 'sources', self.root / 'store'
        self.sources.mkdir()
        self.store.mkdir()
        Image.new('RGB', (16, 12), '#224466').save(self.sources / 'one.png')
        self.initial = (self.sources / 'one.png').read_bytes()
        self.engine = create_engine('sqlite:///' + str(self.root / 'test.sqlite'))
        self.addCleanup(self.engine.dispose)
        SQLModel.metadata.create_all(self.engine)
        with directory(str(self.sources)) as fd:
            self.mount_id = locations.descriptor_mount_id(fd)
        # Btrfs subvolumes can have st_dev distinct from mountinfo's pseudo device.
        self.mount = {'mount_id': self.mount_id, 'device': '8:7',
            'filesystem_root': '/', 'mountpoint': str(self.root),
            'filesystem_type': 'btrfs', 'source': '/dev/test-fixture'}
        self.mounts = {self.mount_id: self.mount}
        self.table_patch = patch.object(locations, '_mount_table', side_effect=lambda: deepcopy(self.mounts))
        self.table_patch.start()
        self.addCleanup(self.table_patch.stop)
        self.findmnt_patch = patch.object(locations.subprocess, 'run', side_effect=self.findmnt)
        self.findmnt_mock = self.findmnt_patch.start()
        self.addCleanup(self.findmnt_patch.stop)
        self.manifest = {'schema_version': 'omoide.source-registration/v1',
            'name': 'Fixture registration', 'subject_id': 'fixture-subject',
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

    def findmnt(self, *_args, **_kwargs):
        mount = self.mount
        row = {'uuid': 'fixture-uuid', 'id': mount['mount_id'], 'maj:min': mount['device'],
            'fsroot': mount['filesystem_root'], 'target': mount['mountpoint'],
            'fstype': mount['filesystem_type'], 'source': mount['source']}
        return SimpleNamespace(returncode=0, stdout=json.dumps({'filesystems': [row]}))

    def register(self, manifest=None):
        with Session(self.engine) as session:
            result = registration.register_source_manifest(session, manifest or self.manifest)
            dataset = session.get(CurationDataset, result['dataset_id'])
            session.expunge(dataset)
            return result, dataset

    def assert_code(self, code, function, *args, **kwargs):
        with self.assertRaises(HTTPException) as raised:
            function(*args, **kwargs)
        self.assertEqual(raised.exception.detail['code'], code)

    def test_btrfs_descriptor_device_may_differ_from_mountinfo(self):
        with directory(str(self.sources)) as fd:
            stat_device = os.fstat(fd).st_dev
            self.assertNotEqual(f'{os.major(stat_device)}:{os.minor(stat_device)}', self.mount['device'])
            observed = locations.observe_source_volume(str(self.sources), opened_fd=fd)
            self.assertEqual(observed['device'], self.mount['device'])
        _, dataset = self.register()
        with directory(str(self.sources)) as fd:
            locations.verify_source_descriptor(dataset, fd)

    def test_registration_is_bounded_attestation_without_grants_or_source_writes(self):
        before = (self.sources / 'one.png').stat()
        with patch.object(Path, 'rglob', side_effect=AssertionError('enumeration forbidden')), \
                patch.object(os, 'scandir', side_effect=AssertionError('enumeration forbidden')):
            result, dataset = self.register()
        self.assertEqual(result['source_count'], 1)
        self.assertEqual(dataset.policy_version, 'production-stills-v1')
        self.assertIs(dataset.policy['fixture_only'], False)
        self.assertIs(dataset.policy['generative_enabled'], False)
        self.assertEqual(dataset.policy['registration']['basis'], 'operator_attestation')
        self.assertIs(dataset.policy['registration']['assertions_are_independently_verified'], False)
        with Session(self.engine) as session:
            self.assertEqual(session.exec(select(CurationGrant)).all(), [])
            source = session.exec(select(CurationSource)).one()
            self.assertTrue(source.lineage_known)
            self.assertFalse(source.generative)
            evidence = dataset.policy['registration']['source_assertions'][source.id]
            self.assertEqual(evidence['sha256'], source.sha256)
            self.assertIs(evidence['review_acceptance'], False)
            self.assertEqual(source_bytes(dataset, source), self.initial)
        after = (self.sources / 'one.png').stat()
        self.assertEqual((before.st_ino, before.st_size, before.st_mtime_ns, before.st_mode),
                         (after.st_ino, after.st_size, after.st_mtime_ns, after.st_mode))
        self.assertEqual(list(self.store.iterdir()), [])
        self.assertEqual((self.sources / 'one.png').read_bytes(), self.initial)

    def test_bad_uuid_is_not_an_authorization_claim(self):
        self.manifest['expected_filesystem_uuid'] = 'wrong-uuid'
        self.assert_code('source_volume_uuid_mismatch', self.register)
        with Session(self.engine) as session:
            self.assertEqual(session.exec(select(CurationDataset)).all(), [])

    def test_missing_uuid_fails_even_when_manifest_names_uuid(self):
        row = json.loads(self.findmnt().stdout)
        row['filesystems'][0]['uuid'] = None
        self.findmnt_mock.side_effect = None
        self.findmnt_mock.return_value = SimpleNamespace(returncode=0, stdout=json.dumps(row))
        self.assert_code('source_volume_uuid_unavailable', self.register)

    def test_uuid_change_after_registration_blocks_source_reads(self):
        _, dataset = self.register()
        row = json.loads(self.findmnt().stdout)
        row['filesystems'][0]['uuid'] = 'replacement-uuid'
        self.findmnt_mock.side_effect = None
        self.findmnt_mock.return_value = SimpleNamespace(returncode=0, stdout=json.dumps(row))
        self.assert_code('source_volume_changed', locations.verify_source_volume, dataset)

    def test_wrong_mountpoint_rejected(self):
        self.manifest['expected_mountpoint'] = '/'
        self.assert_code('source_mountpoint_mismatch', self.register)

    def test_mount_unavailable_errors_are_closed(self):
        for result in (SimpleNamespace(returncode=1, stdout=''),
                       SimpleNamespace(returncode=0, stdout='not-json'),
                       SimpleNamespace(returncode=0, stdout='{"filesystems":[]}')):
            with self.subTest(result=result):
                self.findmnt_mock.side_effect = None
                self.findmnt_mock.return_value = result
                self.assert_code('source_volume_identity_unavailable', self.register)
        for failure in (FileNotFoundError(), subprocess.TimeoutExpired('findmnt', 5)):
            self.findmnt_mock.side_effect = failure
            self.assert_code('source_volume_identity_unavailable', self.register)

    def test_unmounted_and_replaced_mount_fail(self):
        self.mounts = {}
        self.assert_code('source_volume_changed', self.register)
        self.mounts = {self.mount_id: {**self.mount, 'source': '/dev/replacement'}}
        self.assert_code('source_volume_changed', self.register)

    def test_namespace_changes_during_observation_fail(self):
        with patch.object(locations, '_namespace_identity', side_effect=[(1, 2), (1, 3)]):
            self.assert_code('source_volume_changed', locations.observe_source_volume, str(self.sources))

    def test_mount_changes_during_observation_fail(self):
        with patch.object(locations, '_mount_table', side_effect=[self.mounts, {}]):
            self.assert_code('source_volume_changed', locations.observe_source_volume, str(self.sources))

    def test_same_device_nested_mount_and_descriptor_mismatch_fail(self):
        _, dataset = self.register()
        with directory(str(self.sources)) as fd:
            with patch.object(locations, 'descriptor_mount_id', return_value=self.mount_id + 1):
                self.assert_code('source_volume_changed', locations.verify_source_descriptor, dataset, fd)
            bad = SimpleNamespace(**dataset.model_dump())
            bad.source_device += 1
            self.assert_code('source_volume_changed', locations.verify_source_descriptor, bad, fd)
        actual_findmnt = self.findmnt
        def nested_mount(command, **kwargs):
            result = actual_findmnt()
            if command[command.index('--target') + 1].endswith('/one.png'):
                row = json.loads(result.stdout)
                row['filesystems'][0]['id'] = self.mount_id + 1
                return SimpleNamespace(returncode=0, stdout=json.dumps(row))
            return result
        self.findmnt_mock.side_effect = nested_mount
        self.assert_code('source_volume_changed', locations.verify_source_volume, dataset, 'one.png')

    def test_source_descriptor_guard_runs_after_read(self):
        _, dataset = self.register()
        with Session(self.engine) as session:
            source = session.exec(select(CurationSource)).one()
        def switch_mount():
            self.mount_id += 1
        # read_at's hook runs after the descriptor guard and before its byte read.
        with patch.object(locations, 'descriptor_mount_id', side_effect=lambda _fd: self.mount_id):
            self.assert_code('source_volume_changed', source_bytes, dataset, source, switch_mount)

    def test_actual_fixture_only_skip_requires_entire_fixture_policy(self):
        fixture = SimpleNamespace(policy_version='fixture-stills-v1', policy={
            'version': 'fixture-stills-v1', 'fixture_only': True, 'generative_enabled': False})
        self.assertIsNone(locations.verify_source_volume(fixture))
        locations.verify_source_descriptor(fixture, -1)
        for field, value in (('version', 'production-stills-v1'), ('fixture_only', 'true'),
                             ('generative_enabled', True)):
            bad = deepcopy(fixture)
            bad.policy[field] = value
            self.assert_code('source_volume_policy_required', locations.verify_source_volume, bad)
        fixture.policy_version = 'production-stills-v1'
        self.assert_code('source_volume_policy_required', locations.verify_source_volume, fixture)

    def test_hash_mismatch_leaves_no_partial_registration(self):
        self.manifest['files'][0]['sha256'] = '0' * 64
        self.assert_code('hash_mismatch', self.register)
        with Session(self.engine) as session:
            self.assertEqual(session.exec(select(CurationDataset)).all(), [])
            self.assertEqual(session.exec(select(CurationSource)).all(), [])

    def test_second_file_failure_rolls_back_entire_manifest(self):
        second = deepcopy(self.manifest['files'][0])
        second['relative_path'] = 'missing.png'
        self.manifest['files'].append(second)
        self.assert_code('source_unavailable', self.register)
        with Session(self.engine) as session:
            self.assertEqual(session.exec(select(CurationDataset)).all(), [])

    def test_source_root_replacement_is_fenced(self):
        _, dataset = self.register()
        self.sources.rename(self.root / 'old-source')
        self.sources.mkdir()
        self.assert_code('root_identity_changed', locations.verify_source_volume, dataset)

    def test_symlink_component_and_file_rejected(self):
        (self.sources / 'link.png').symlink_to(self.sources / 'one.png')
        self.manifest['files'][0]['relative_path'] = 'link.png'
        self.assert_code('source_unavailable', self.register)
        alias = self.root / 'source-alias'
        alias.symlink_to(self.sources, target_is_directory=True)
        self.manifest['source_root'] = str(alias)
        self.assert_code('storage_unavailable', self.register)

    def test_path_traversal_and_unbounded_manifest_rejected_before_observation(self):
        for path in ('../one.png', '/one.png', 'a//one.png', 'a/./one.png'):
            with self.subTest(path=path):
                value = deepcopy(self.manifest)
                value['files'][0]['relative_path'] = path
                self.assert_code('unsafe_path', self.register, value)
        for files in ([], self.manifest['files'] * 101):
            value = deepcopy(self.manifest)
            value['files'] = files
            self.assert_code('invalid_registration_manifest', self.register, value)
        self.assertEqual(self.findmnt_mock.call_count, 0)

    def test_unknown_or_generative_is_never_registerable_even_when_excluded(self):
        for kind, complete, split in (('unknown', True, 'train'), ('generative', True, 'excluded'),
                                      ('original_capture', False, 'train')):
            value = deepcopy(self.manifest)
            value['files'][0]['ancestry'].update(kind=kind, ancestry_complete=complete)
            value['files'][0]['split'] = split
            self.assert_code('unverified_or_generative_ancestry', self.register, value)
        self.assertEqual(self.findmnt_mock.call_count, 0)

    def test_derivative_requires_parent_evidence_and_pins_attestation(self):
        self.manifest['files'][0]['ancestry']['kind'] = 'non_generative_derivative'
        self.assert_code('invalid_ancestry_attestation', self.register)
        self.manifest['files'][0]['ancestry']['parent_sha256'] = ['a' * 64]
        _, dataset = self.register()
        assertion = next(iter(dataset.policy['registration']['source_assertions'].values()))
        self.assertEqual(assertion['ancestry']['parent_sha256'], ['a' * 64])
        self.assertEqual(assertion['basis'], 'operator_attestation')

    def test_manifest_cannot_attest_cyclic_ancestry(self):
        for name, sha, parent in (('two.png', 'b' * 64, 'c' * 64),
                                  ('three.png', 'c' * 64, 'b' * 64)):
            item = deepcopy(self.manifest['files'][0])
            item.update(relative_path=name, sha256=sha)
            item['ancestry'].update(kind='non_generative_derivative', parent_sha256=[parent])
            self.manifest['files'].append(item)
        self.assert_code('invalid_ancestry_attestation', self.register)
        self.assertEqual(self.findmnt_mock.call_count, 0)

    def test_missing_subject_group_split_or_evidence_is_rejected(self):
        for field in ('subject_id', 'group_id', 'split', 'ancestry'):
            value = deepcopy(self.manifest)
            del value['files'][0][field]
            self.assert_code('invalid_registration_manifest', self.register, value)
        self.manifest['files'][0]['subject_id'] = 'other-subject'
        self.assert_code('subject_attestation_mismatch', self.register)

    def test_actor_claim_cannot_turn_registration_into_review_or_grant(self):
        for field, value in (('credentials', {}), ('actor_kind', 'human'), ('accepted', True)):
            manifest = deepcopy(self.manifest)
            manifest[field] = value
            self.assert_code('invalid_registration_manifest', self.register, manifest)

    def test_duplicate_paths_and_cross_split_hashes_groups_rejected(self):
        self.manifest['files'].append(deepcopy(self.manifest['files'][0]))
        self.assert_code('duplicate_or_unsafe_source_path', self.register)
        self.manifest['files'][1].update(relative_path='two.png', split='test')
        self.assert_code('registration_split_conflict', self.register)
        self.manifest['files'][1]['group_id'] = 'capture-two'
        self.assert_code('registration_split_conflict', self.register)

    def test_lexical_and_bind_mount_overlap_rejected(self):
        value = deepcopy(self.manifest)
        value['store_root'] = str(self.sources / 'out')
        self.assert_code('overlapping_roots', self.register, value)
        store_mount_id = self.mount_id + 1
        alias_mount = {**self.mount, 'mount_id': store_mount_id,
                       'mountpoint': str(self.store), 'filesystem_root': '/sources/subdirectory'}
        with directory(str(self.sources)) as source_fd, directory(str(self.store)) as store_fd:
            with patch.object(locations, '_mount_table', return_value={**self.mounts, store_mount_id: alias_mount}), \
                    patch.object(locations, 'descriptor_mount_id', side_effect=[self.mount_id, store_mount_id]):
                self.assert_code('overlapping_roots', locations.ensure_disjoint_roots,
                    str(self.sources), source_fd, str(self.store), store_fd)

    def test_clean_session_is_required(self):
        with Session(self.engine) as session:
            session.exec(text('SELECT 1'))
            self.assert_code('registration_requires_clean_session',
                registration.register_source_manifest, session, self.manifest)

    def test_findmnt_bind_suffix_is_checked_against_kernel_fsroot(self):
        self.mount['filesystem_root'] = '/subvolume'
        original = self.findmnt
        def with_suffix(*args, **kwargs):
            result = original()
            value = json.loads(result.stdout)
            value['filesystems'][0]['source'] += '[/subvolume]'
            result.stdout = json.dumps(value)
            return result
        self.findmnt_mock.side_effect = with_suffix
        with directory(str(self.sources)) as fd:
            self.assertEqual(locations.observe_source_volume(str(self.sources), opened_fd=fd)['source'],
                             '/dev/test-fixture')


class LegacyPlanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.engine = create_engine('sqlite:///' + str(self.root / 'legacy-fixture.sqlite'))
        self.addCleanup(self.engine.dispose)
        SQLModel.metadata.create_all(self.engine)
        with Session(self.engine) as session:
            dataset = TrainingDataset(name='Legacy fixture', slug='legacy-fixture',
                trigger_word='fixture', class_token='person', caption_template='{trigger} {caption}')
            media = Media(path='/unopened/fixture.png', filename='fixture.png', size=42)
            session.add_all([dataset, media])
            session.flush()
            item = DatasetItem(dataset_id=dataset.id, media_id=media.id,
                reviewed_at=datetime(2020, 1, 1), caption_reviewed_at=datetime(2020, 1, 1))
            annotation = MediaAnnotation(media_id=media.id, kind=AnnotationKind.CAPTION,
                author=AnnotationAuthor.HUMAN, review_status=AnnotationReviewStatus.APPROVED,
                revision=1, content={'caption': '  Exact UTF-8 café.\n'},
                provenance={'fixture': 'operator-label-is-not-presence'})
            session.add_all([item, annotation])
            session.commit()
            self.dataset_id, self.item_id, self.annotation_id = dataset.id, item.id, annotation.id

    def plan(self, item_ids=None):
        with Session(self.engine) as session:
            return plan_legacy_dataset_items(session, dataset_id=self.dataset_id,
                item_ids=item_ids if item_ids is not None else [self.item_id])

    def test_plan_reads_only_explicit_metadata_and_never_accepts_human_markers(self):
        queries = []
        def capture(_conn, _cursor, statement, *_args):
            queries.append(statement)
        event.listen(self.engine, 'before_cursor_execute', capture)
        self.addCleanup(event.remove, self.engine, 'before_cursor_execute', capture)
        with patch.object(os, 'open', side_effect=AssertionError('media filesystem accessed')), \
                patch.object(Path, 'read_bytes', side_effect=AssertionError('media bytes read')):
            plan = self.plan()
        self.assertTrue(all(query.lstrip().upper().startswith('SELECT') for query in queries))
        item = plan['items'][0]
        self.assertFalse(item['accepted'])
        self.assertFalse(item['registerable'])
        self.assertEqual(item['ancestry']['kind'], 'unknown')
        self.assertIn('unknown_ancestry', item['blockers'])
        caption = item['caption_proposal']
        self.assertEqual(caption['text'], '  Exact UTF-8 café.\n')
        self.assertEqual(caption['sha256'], digest(caption['text'].encode('utf-8')))
        self.assertEqual(caption['provenance']['annotation_id'], self.annotation_id)
        self.assertEqual(caption['provenance']['recorded_author'], 'human')
        self.assertEqual(caption['provenance']['recorded_review_status'], 'approved')
        self.assertFalse(plan['legacy_review_is_authority'])
        self.assertFalse(item['caption_is_existing_rendered_dataset_caption'])

    def test_caption_override_is_exact_and_never_interpreted_as_acceptance(self):
        with Session(self.engine) as session:
            item = session.get(DatasetItem, self.item_id)
            item.caption_override = '  Override\n'
            session.add(item)
            session.commit()
        proposal = self.plan()['items'][0]['caption_proposal']
        self.assertEqual(proposal['text'], '  Override\n')
        self.assertEqual(proposal['provenance']['kind'], 'legacy_dataset_item_override')

    def test_latest_raw_annotation_is_labeled_as_independent_proposal(self):
        with Session(self.engine) as session:
            item = session.get(DatasetItem, self.item_id)
            session.add(MediaAnnotation(media_id=item.media_id, kind=AnnotationKind.CAPTION,
                author=AnnotationAuthor.MACHINE, review_status=AnnotationReviewStatus.CANDIDATE,
                revision=2, content={'text': 'Latest raw candidate'}))
            session.commit()
        item = self.plan()['items'][0]
        self.assertEqual(item['caption_proposal']['text'], 'Latest raw candidate')
        self.assertIn('latest-raw-annotation', item['caption_selection_policy'])
        self.assertFalse(item['accepted'])

    def test_none_caption_source_does_not_promote_annotations(self):
        with Session(self.engine) as session:
            dataset = session.get(TrainingDataset, self.dataset_id)
            dataset.caption_source = DatasetCaptionSource.NONE
            session.add(dataset)
            session.commit()
        plan = self.plan()
        self.assertIsNone(plan['items'][0]['caption_proposal'])
        self.assertIn('legacy_dataset_caption_source_none', plan['items'][0]['blockers'])
        self.assertEqual(plan['legacy_caption_configuration']['source'], 'none')

    def test_template_caption_source_does_not_promote_annotations(self):
        with Session(self.engine) as session:
            dataset = session.get(TrainingDataset, self.dataset_id)
            dataset.caption_source = DatasetCaptionSource.TEMPLATE
            session.add(dataset)
            session.commit()
        plan = self.plan()
        self.assertIsNone(plan['items'][0]['caption_proposal'])
        self.assertIn('legacy_template_requires_explicit_caption', plan['items'][0]['blockers'])

    def test_malformed_caption_content_fails_as_proposal_not_exception(self):
        with Session(self.engine) as session:
            annotation = session.get(MediaAnnotation, self.annotation_id)
            annotation.content = []
            session.add(annotation)
            session.commit()
        self.assertIn('legacy_caption_not_text', self.plan()['items'][0]['blockers'])

    def test_explicit_selection_has_no_census_fallback(self):
        for selected in ([], [self.item_id] * 2, list(range(1, 102)), [True], ['1']):
            with self.subTest(selected=selected), self.assertRaises(HTTPException) as raised:
                self.plan(selected)
            self.assertEqual(raised.exception.detail['code'], 'invalid_legacy_plan_selection')
        with self.assertRaises(HTTPException) as raised:
            self.plan([self.item_id + 1])
        self.assertEqual(raised.exception.detail['code'], 'legacy_item_selection_unavailable')

    def test_dirty_session_cannot_autoflush(self):
        with Session(self.engine) as session:
            item = session.get(DatasetItem, self.item_id)
            item.caption_override = 'Pending mutation'
            with self.assertRaises(HTTPException) as raised:
                plan_legacy_dataset_items(session, dataset_id=self.dataset_id, item_ids=[self.item_id])
            self.assertEqual(raised.exception.detail['code'], 'legacy_plan_requires_clean_session')
        self.assertEqual(self.plan()['items'][0]['caption_proposal']['text'], '  Exact UTF-8 café.\n')


if __name__ == '__main__':
    unittest.main()
