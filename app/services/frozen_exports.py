"""Immutable admission snapshots, byte-only copies and recoverable publication.

No trainer activation and no format conversion occurs here. An OS advisory lock
serializes execution across processes. The DB admission and file publication are
separate; a valid published directory is reconciled after a lost final commit.
"""
import ctypes
import errno
import fcntl
import json
import os
from pathlib import Path

from sqlmodel import Session, select

from app.curation_models import (CurationArtifact, CurationEvent, CurationOperation,
    CurationReview, CurationGrant, CurationSource)
from app.services.curation_artifacts import (MAX_ARTIFACT_BYTES, artifact_bytes,
    canonical, directory, ensure_directory, normalized, read_at, source_bytes, write_once, verify_child_directory)
from app.services.curation_plans import (admitted, artifact_for, generative_ancestry,
    item_state, latest_caption, latest_review, receipt, record_failure, transaction)
from app.services.curation_policy import PRODUCTION_POLICY_VERSION, dataset_for, digest, fail, require_revision
from app.services.curation_auth import review_authority_error


IMPLEMENTATION = {name: digest((Path(__file__).parent / name).read_bytes())
                  for name in ('curation_artifacts.py', 'curation_media.py', 'curation_policy.py',
                               'curation_auth.py', 'curation_plans.py', 'frozen_exports.py')}

def _check_splits(session, dataset, members):
    sources = session.exec(select(CurationSource).where(CurationSource.dataset_id == dataset.id)).all()
    for source in sources:
        if source.split not in {'validation', 'test'}:
            continue
        if any(source.group_id == member['group_id'] or source.sha256 == member['source_sha256'] for member in members):
            fail('split_leakage')
        # A different encoding/metadata hash is still the same captured pixels.
        # Unknown/unsupported holdout content conservatively blocks a freeze.
        _, pixel_hash, *_ = normalized(source_bytes(dataset, source))
        if any(pixel_hash == member['pixel_sha256'] for member in members):
            fail('split_leakage')


def _snapshot(session, dataset):
    sources = session.exec(select(CurationSource).where(CurationSource.dataset_id == dataset.id).order_by(CurationSource.id)).all()
    artifacts = session.exec(select(CurationArtifact).where(CurationArtifact.dataset_id == dataset.id).order_by(CurationArtifact.id)).all()
    by_source = {a.source_id: a for a in artifacts}
    train_sources = [source for source in sources if source.split == 'train']
    bound = 100 if dataset.policy_version == PRODUCTION_POLICY_VERSION else 20
    maximum = dataset.policy.get('max_items', bound)
    if type(maximum) is not int or not 1 <= maximum <= bound or not train_sources or len(train_sources) > maximum:
        fail('invalid_membership')
    # Reviewed fixture groups and byte-equivalent inputs cannot cross splits.
    for source in train_sources:
        if any(other.split in {'validation', 'test'} and
               (other.group_id == source.group_id or other.sha256 == source.sha256)
               for other in sources):
            fail('split_leakage')
    members = []
    for source in train_sources:
        source_bytes(dataset, source)
        artifact = by_source.get(source.id)
        if not artifact:
            fail('unmaterialized_sources')
        state = item_state(session, dataset, artifact)
        if not state['eligible']:
            fail(state['blockers'][0])
        caption = latest_caption(session, artifact)
        review = latest_review(session, artifact, caption)
        artifact_bytes(dataset, artifact)
        members.append({'artifact_id': artifact.id, 'source_id': source.id,
            'source_sha256': source.sha256, 'asset_sha256': artifact.sha256,
            'pixel_sha256': artifact.pixel_sha256, 'width': artifact.width, 'height': artifact.height,
            'caption_id': caption.id, 'caption_text': caption.text, 'caption_sha256': caption.sha256,
            'review_id': review.id, 'review_grant_id': review.grant_id,
            'review_authority': review.actor_kind, 'human_presence_verified': bool(review.presence_evidence),
            'review_evidence': review.presence_evidence,
            'group_id': source.group_id, 'split': source.split,
            'image_name': artifact.id + '.png', 'caption_name': artifact.id + '.txt',
            'provenance': artifact.provenance, 'generative_ancestry': False})
    _check_splits(session, dataset, members)
    production = dataset.policy_version == PRODUCTION_POLICY_VERSION
    return {'schema_version': 'omoide-still-export-v1' if production else 'omoide-fixture-export-v1', 'fixture_only': not production,
            'dataset_id': dataset.id, 'subject_id': dataset.subject_id,
            'snapshot_revision': dataset.revision, 'policy_version': dataset.policy_version,
            'policy': dataset.policy, 'implementation_sha256': IMPLEMENTATION, 'members': members}


def admit_export(session, token, dataset_id, request):
    with transaction(session):
        dataset, grant = dataset_for(session, token, dataset_id, 'export')
        op, request_hash = admitted(session, grant, 'export', request.idempotency_key, request.model_dump())
        if op is None:
            require_revision(dataset, request.expected_revision)
            snapshot = _snapshot(session, dataset)
            op = CurationOperation(dataset_id=dataset_id, grant_id=grant.id, kind='export',
                idempotency_key=request.idempotency_key, request_sha256=request_hash,
                snapshot_revision=dataset.revision, snapshot=snapshot, item_count=len(snapshot['members']))
            session.add(op)
            session.flush()
            session.add(CurationEvent(operation_id=op.id, event='admitted', attempt=0))
        op_id = op.id
    return op_id


def get_export(session, token, operation_id):
    # Authorize dataset before returning any operation data.
    op = session.get(CurationOperation, operation_id, populate_existing=True)
    if op is None or op.kind != 'export':
        fail('not_found', 404)
    dataset_for(session, token, op.dataset_id)
    return receipt(op)


def _revalidate(session, token, op):
    dataset, grant = dataset_for(session, token, op.dataset_id, 'export')
    if grant.id != op.grant_id:
        fail('operation_owner_required', 403)
    if (dataset.policy_version != op.snapshot['policy_version'] or dataset.policy != op.snapshot['policy']
            or op.snapshot.get('implementation_sha256') != IMPLEMENTATION):
        fail('policy_stale')
    _check_splits(session, dataset, op.snapshot['members'])
    for member in op.snapshot['members']:
        artifact = artifact_for(session, dataset, member['artifact_id'])
        if generative_ancestry(session, dataset, artifact):
            fail('generative_disabled', 403)
        if artifact.sha256 != member['asset_sha256']:
            fail('artifact_changed')
        source = session.get(CurationSource, member['source_id'])
        if (source.sha256 != member['source_sha256'] or source.split != member['split']
                or source.group_id != member['group_id']):
            fail('source_changed')
        source_bytes(dataset, source)
        reviewer = session.get(CurationGrant, member['review_grant_id'], populate_existing=True)
        review = session.get(CurationReview, member['review_id'], populate_existing=True)
        if not reviewer or reviewer.revoked or not review or review.decision != 'accept':
            fail('review_authority_revoked')
        if (review.asset_sha256 != member['asset_sha256'] or review.caption_sha256 != member['caption_sha256']
                or review.caption_id != member['caption_id'] or review.artifact_id != member['artifact_id']
                or review.policy_version != dataset.policy_version
                or review.actor_kind != member['review_authority']
                or review.presence_evidence != member.get('review_evidence', {})):
            fail('review_stale')
        authority_error = review_authority_error(session, dataset, review)
        if authority_error:
            fail(authority_error)
        # A later explicit rejection of this exact caption cancels acceptance;
        # independent new caption drafts do not rewrite this admitted version.
        newer_review = session.exec(select(CurationReview).where(
            CurationReview.artifact_id == member['artifact_id'],
            CurationReview.caption_id == member['caption_id'],
            CurationReview.revision > review.revision).order_by(CurationReview.revision.desc())).first()
        if newer_review and newer_review.decision != 'accept':
            fail('review_withdrawn')
    return dataset


def _rename_noreplace(source_fd, source, target_fd, target):
    # Linux-only fixture implementation: never overwrite an existing export.
    libc = ctypes.CDLL(None, use_errno=True)
    rename = getattr(libc, 'renameat2', None)
    if rename is None:
        fail('atomic_publication_unavailable')
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(source_fd, source.encode(), target_fd, target.encode(), 1) != 0:
        error = ctypes.get_errno()
        if error == errno.EEXIST:
            fail('publication_collision')
        fail('atomic_publication_failed')
    os.fsync(source_fd)
    os.fsync(target_fd)


def published_manifest(snapshot):
    """Project private admission state into a manifest without source locators.

    The durable snapshot retains exact volume and operator-attestation evidence
    for revalidation. Only their hashes cross the export boundary. Fixture v1
    manifests preserve their historical shape; they contain no production roots.
    """
    if snapshot.get('fixture_only') is True:
        return snapshot
    policy = snapshot['policy']
    public = {key: snapshot[key] for key in ('schema_version', 'fixture_only',
        'dataset_id', 'subject_id', 'snapshot_revision', 'policy_version', 'implementation_sha256')}
    public['policy_sha256'] = digest(canonical(policy))
    public['source_registration_sha256'] = digest(canonical(policy.get('registration', {})))
    public['policy_summary'] = {'version': snapshot['policy_version'], 'fixture_only': False,
        'generative_enabled': False, 'max_items': policy['max_items'],
        'source_authority': 'operator_attestation', 'source_assertions_independently_verified': False}
    public['members'] = []
    for member in snapshot['members']:
        item = {key: member[key] for key in ('artifact_id', 'source_id', 'source_sha256',
            'asset_sha256', 'pixel_sha256', 'width', 'height', 'caption_id', 'caption_text',
            'caption_sha256', 'review_id', 'review_grant_id', 'review_authority',
            'human_presence_verified', 'group_id', 'split', 'image_name', 'caption_name',
            'provenance', 'generative_ancestry')}
        evidence = member['review_evidence']
        item['review_evidence'] = {key: evidence[key] for key in ('method', 'user_present',
            'user_verified', 'credential_id', 'challenge_id', 'request_sha256',
            'assertion_sha256', 'verified_at')}
        item['review_evidence']['relying_party_sha256'] = digest(canonical(
            {'rp_id': evidence['rp_id'], 'origin': evidence['origin']}))
        public['members'].append(item)
    return public


def _verify(directory_fd, snapshot):
    manifest = canonical(published_manifest(snapshot))
    if read_at(directory_fd, 'manifest.json') != manifest:
        fail('manifest_mismatch')
    expected = {'manifest.json', 'SUCCESS.json'}
    for member in snapshot['members']:
        expected.update((member['image_name'], member['caption_name']))
        read_at(directory_fd, member['image_name'], member['asset_sha256'], MAX_ARTIFACT_BYTES)
        caption = read_at(directory_fd, member['caption_name'], member['caption_sha256'])
        if caption != member['caption_text'].encode():
            fail('caption_mismatch')
    fresh = os.open('.', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory_fd)
    try:
        actual = set(os.listdir(fresh))
    finally:
        os.close(fresh)
    if actual != expected:
        fail('inventory_mismatch')
    marker = {'manifest_sha256': digest(manifest), 'item_count': len(snapshot['members'])}
    if read_at(directory_fd, 'SUCCESS.json') != canonical(marker):
        fail('success_marker_mismatch')
    return digest(manifest)


def execute_export(session: Session, token: str, operation_id: str, *, checkpoint=None):
    """Replay the same immutable version. Checkpoints are test crash injection only."""
    get_export(session, token, operation_id)
    op = session.get(CurationOperation, operation_id)
    dataset, grant = dataset_for(session, token, op.dataset_id, 'export')
    # Establish operation ownership before creating a directory/lock or entering
    # the failure journal. A denied peer grant must not mutate this operation.
    # _revalidate repeats authorization inside each publication transaction.
    if grant.id != op.grant_id:
        fail('operation_owner_required', 403)
    with directory(dataset.store_root, (dataset.store_device, dataset.store_inode)) as root:
        exports = ensure_directory(root, 'exports')
        lock = None
        try:
            lock = os.open('.' + operation_id + '.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600, dir_fd=exports)
            fcntl.flock(lock, fcntl.LOCK_EX)
            with transaction(session):
                op = session.get(CurationOperation, operation_id, populate_existing=True)
                dataset = _revalidate(session, token, op)
                already_done = op.status == 'succeeded'
                if not already_done:
                    op.attempts += 1
                    op.status = 'running'
                    op.error_code = None
                    session.add(op)
                    session.add(CurationEvent(operation_id=op.id, event='attempt_started', attempt=op.attempts))
                snapshot = json.loads(canonical(op.snapshot))
                attempt = op.attempts
            if checkpoint:
                checkpoint('after_running_commit')
            try:
                published = os.open(operation_id, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=exports)
            except FileNotFoundError:
                published = None
            if published is None:
                if already_done:
                    fail('published_export_missing')
                staging_root = ensure_directory(root, 'staging')
                stage_name = operation_id + '-' + str(attempt)
                stage = ensure_directory(staging_root, stage_name)
                try:
                    if checkpoint:
                        checkpoint('after_staging_directory')
                    for member in snapshot['members']:
                        artifact = session.get(CurationArtifact, member['artifact_id'])
                        data = artifact_bytes(dataset, artifact)
                        if digest(data) != member['asset_sha256']:
                            fail('artifact_changed')
                        write_once(stage, member['image_name'], data)
                        # Immutable captured bytes, never read latest_caption here.
                        write_once(stage, member['caption_name'], member['caption_text'].encode())
                    manifest = canonical(published_manifest(snapshot))
                    write_once(stage, 'manifest.json', manifest)
                    write_once(stage, 'SUCCESS.json', canonical({'manifest_sha256': digest(manifest), 'item_count': len(snapshot['members'])}))
                    _verify(stage, snapshot)
                    if checkpoint:
                        checkpoint('before_publication')
                    with transaction(session):
                        op = session.get(CurationOperation, operation_id, populate_existing=True)
                        dataset = _revalidate(session, token, op)
                        # Reopen root to fence rename/root substitution during copy.
                        with directory(dataset.store_root, (dataset.store_device, dataset.store_inode)) as current_root:
                            verify_child_directory(current_root, 'exports', exports)
                            verify_child_directory(current_root, 'staging', staging_root)
                            verify_child_directory(staging_root, stage_name, stage)
                            _rename_noreplace(staging_root, stage_name, exports, operation_id)
                    if checkpoint:
                        checkpoint('after_publication')
                finally:
                    os.close(stage)
                    os.close(staging_root)
                published = os.open(operation_id, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=exports)
            try:
                manifest_hash = _verify(published, snapshot)
            finally:
                os.close(published)
            with transaction(session):
                op = session.get(CurationOperation, operation_id, populate_existing=True)
                dataset = _revalidate(session, token, op)
                with directory(dataset.store_root, (dataset.store_device, dataset.store_inode)) as current_root:
                    verify_child_directory(current_root, 'exports', exports)
                    final_fd = os.open(operation_id, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=exports)
                    try:
                        _verify(final_fd, snapshot)
                        verify_child_directory(exports, operation_id, final_fd)
                    finally:
                        os.close(final_fd)
                op.status = 'succeeded'
                op.manifest_sha256 = manifest_hash
                op.error_code = None
                session.add(op)
                session.add(CurationEvent(operation_id=op.id, event='publication_verified', attempt=op.attempts))
            return get_export(session, token, operation_id)
        except Exception as exc:
            record_failure(session, operation_id, exc)
            raise
        finally:
            if lock is not None:
                os.close(lock)
            os.close(exports)
