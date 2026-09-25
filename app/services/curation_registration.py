"""Trusted local, exact-manifest production registration; no HTTP grant minting."""
from datetime import UTC, datetime
import os
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError
from sqlmodel import Session

from sqlmodel import select

from app.curation_models import CurationDataset, CurationSource
from app.services.curation_artifacts import (canonical, directory, probe_store_capabilities, read_at,
    safe_parts)
from app.services.curation_media import VIDEO_EXTENSIONS
from app.services.curation_policy import digest, fail
from app.services.source_locations import (PRODUCTION_POLICY_VERSION, compare_source_volume, ensure_disjoint_roots,
    observe_source_volume, registered_source_volume, root_identity_record, source_root_identity,
    verify_source_descriptor, verify_source_volume)

Text = Annotated[str, StringConstraints(strip_whitespace=False, min_length=1, max_length=2048)]
Identifier = Annotated[str, StringConstraints(pattern=r'^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$')]
SHA256 = Annotated[str, StringConstraints(pattern=r'^[0-9a-f]{64}$')]


class ClosedManifest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)


class OperatorAttestation(ClosedManifest):
    operator_id: Identifier
    attestation_id: Identifier
    statement: Text
    evidence_reference: Text
    # These are explicit operator assertions, not verified identity/rights facts.
    rights_to_curate: Literal[True]
    subject_identity_attested: Literal[True]


class AncestryAttestation(ClosedManifest):
    kind: Literal['original_capture', 'non_generative_derivative', 'unknown', 'generative']
    evidence_reference: Text
    statement: Text
    # A derivative's complete known ancestry is an operator claim, pinned in policy.
    ancestry_complete: bool
    parent_sha256: list[SHA256] = Field(default_factory=list, max_length=100)


class SourceFileManifest(ClosedManifest):
    relative_path: Text
    sha256: SHA256
    label: Annotated[str, StringConstraints(min_length=1, max_length=256)]
    subject_id: Identifier
    group_id: Identifier
    split: Literal['train', 'validation', 'test', 'excluded']
    ancestry: AncestryAttestation


class MediaPolicyManifest(ClosedManifest):
    """Operator-set media policy, pinned into dataset policy at registration.

    Video frame materialization is off unless this object turns it on, and the
    flag is a registration-time decision: no HTTP route can raise it afterwards.
    """
    video_frame_materialization: bool = False


class SourceRegistrationManifest(ClosedManifest):
    schema_version: Literal['omoide.source-registration/v1']
    name: Annotated[str, StringConstraints(min_length=1, max_length=256)]
    subject_id: Identifier
    source_root: Text
    store_root: Text
    expected_filesystem_uuid: Annotated[str, StringConstraints(pattern=r'^[A-Za-z0-9.-]{1,128}$')]
    expected_mountpoint: Text
    attestation: OperatorAttestation
    media_policy: MediaPolicyManifest = Field(default_factory=MediaPolicyManifest)
    files: list[SourceFileManifest] = Field(min_length=1, max_length=100)


def _absolute_root(value: str) -> Path:
    path = Path(value)
    if (not path.is_absolute() or (value != '/' and any(part in {'.', '..', ''} for part in value.split('/')[1:]))
            or '\x00' in value):
        fail('unsafe_root')
    return path


def _validate_manifest(value) -> SourceRegistrationManifest:
    try:
        # Revalidate even an instance: frozen models still contain mutable lists.
        value = value.model_dump() if isinstance(value, SourceRegistrationManifest) else value
        manifest = SourceRegistrationManifest.model_validate(value)
    except ValidationError:
        fail('invalid_registration_manifest', 422)
    if len(canonical(manifest.model_dump())) > 262144:
        fail('registration_manifest_too_large', 422)
    source, store = _absolute_root(manifest.source_root), _absolute_root(manifest.store_root)
    mountpoint = _absolute_root(manifest.expected_mountpoint)
    if source == store or source in store.parents or store in source.parents:
        fail('overlapping_roots')
    if not source.is_relative_to(mountpoint):
        fail('source_mountpoint_mismatch')
    paths, groups, hashes, ancestry_by_hash = set(), {}, {}, {}
    for item in manifest.files:
        safe_parts(item.relative_path)
        if '\x00' in item.relative_path or item.relative_path in paths:
            fail('duplicate_or_unsafe_source_path')
        paths.add(item.relative_path)
        if item.subject_id != manifest.subject_id:
            fail('subject_attestation_mismatch')
        ancestry = item.ancestry
        if ancestry.kind in {'unknown', 'generative'} or not ancestry.ancestry_complete:
            fail('unverified_or_generative_ancestry', 403)
        if ((ancestry.kind == 'original_capture' and ancestry.parent_sha256)
                or (ancestry.kind == 'non_generative_derivative' and not ancestry.parent_sha256)
                or item.sha256 in ancestry.parent_sha256
                or len(set(ancestry.parent_sha256)) != len(ancestry.parent_sha256)):
            fail('invalid_ancestry_attestation')
        parents = tuple(sorted(ancestry.parent_sha256))
        if item.sha256 in ancestry_by_hash and ancestry_by_hash[item.sha256] != parents:
            fail('invalid_ancestry_attestation')
        ancestry_by_hash[item.sha256] = parents
        if item.split != 'excluded':
            for key, mapping in ((item.group_id, groups), (item.sha256, hashes)):
                prior = mapping.setdefault(key, item.split)
                if prior != item.split:
                    fail('registration_split_conflict')
    videos = [item.relative_path for item in manifest.files
              if PurePosixPath(item.relative_path).suffix.lower() in VIDEO_EXTENSIONS]
    if videos and not manifest.media_policy.video_frame_materialization:
        # Registering a video is pointless while its only materialization path is
        # refused; say so at registration rather than at the first frame request.
        fail('video_materialization_disabled', 403)
    visiting, visited = set(), set()
    def visit(sha):
        if sha in visiting:
            fail('invalid_ancestry_attestation')
        if sha in visited or sha not in ancestry_by_hash:
            return
        visiting.add(sha)
        for parent in ancestry_by_hash[sha]:
            visit(parent)
        visiting.remove(sha)
        visited.add(sha)
    for sha in ancestry_by_hash:
        visit(sha)
    return manifest


def register_source_manifest(session: Session, manifest: dict | SourceRegistrationManifest) -> dict:
    """Register 1..100 explicitly authorized files without discovering any others.

    Caller supplies an operator-reviewed authorization manifest and a dedicated
    session. No principal/grant/credential is created. Reads are bounded, sources
    are never written, and a failed registration rolls back all new records.
    """
    if session.new or session.dirty or session.deleted or session.in_transaction():
        fail('registration_requires_clean_session')
    spec = _validate_manifest(manifest)
    with directory(spec.source_root) as source_fd, directory(spec.store_root) as store_fd:
        source_stat, store_stat = os.fstat(source_fd), os.fstat(store_fd)
        if (source_stat.st_dev, source_stat.st_ino) == (store_stat.st_dev, store_stat.st_ino):
            fail('overlapping_roots')
        ensure_disjoint_roots(spec.source_root, source_fd, spec.store_root, store_fd)
        probe_store_capabilities(store_fd)
        observed = observe_source_volume(spec.source_root, opened_fd=source_fd)
        if observed['uuid'] != spec.expected_filesystem_uuid.casefold():
            fail('source_volume_uuid_mismatch')
        if observed['mountpoint'] != spec.expected_mountpoint:
            fail('source_mountpoint_mismatch')
        policy = {'version': PRODUCTION_POLICY_VERSION, 'fixture_only': False,
            'generative_enabled': False, 'human_presence_verified': False,
            'max_items': 100, 'source_access': 'read-only', 'egress': 'local-review-only',
            'source_volume': observed,
            'root_identity': root_identity_record(observed),
            'authorization_manifest_sha256': digest(canonical(spec.model_dump())),
            'registration': {'basis': 'operator_attestation',
                'assertions_are_independently_verified': False,
                'received_at': datetime.now(UTC).isoformat(),
                'attestation': spec.attestation.model_dump(), 'source_assertions': {}},
            'identity': {'matching_enabled': False, 'frame_mining_enabled': False,
                         'outlier_enabled': False},
            'media': {'video_frame_materialization': spec.media_policy.video_frame_materialization,
                      'allowed_video_extensions': list(VIDEO_EXTENSIONS),
                      'basis': 'operator_attestation', 'generative_derivatives': False,
                      'repair_and_mask_materialization': False}}
        dataset = CurationDataset(name=spec.name, subject_id=spec.subject_id,
            policy_version=PRODUCTION_POLICY_VERSION,
            source_root=spec.source_root, source_device=source_stat.st_dev,
            source_inode=source_stat.st_ino, store_root=spec.store_root,
            store_device=store_stat.st_dev, store_inode=store_stat.st_ino, policy=policy)
        sources = []
        for item in spec.files:
            verify_source_volume(dataset, item.relative_path)
            data = read_at(source_fd, item.relative_path, item.sha256,
                           descriptor_guard=lambda fd: verify_source_descriptor(dataset, fd))
            verify_source_volume(dataset, item.relative_path)
            source = CurationSource(dataset_id=dataset.id, relative_path=item.relative_path,
                label=item.label, sha256=item.sha256, size=len(data), group_id=item.group_id,
                split=item.split, lineage_known=True, generative=False)
            sources.append(source)
            policy['registration']['source_assertions'][source.id] = {
                **item.model_dump(), 'basis': 'operator_attestation',
                'subject_identity_inferred': False, 'review_acceptance': False}
        # Detect root/store replacement and volume changes before the atomic write.
        verify_source_volume(dataset)
        with directory(spec.store_root, (store_stat.st_dev, store_stat.st_ino)):
            pass
        try:
            session.add(dataset)
            session.add_all(sources)
            session.commit()
        except BaseException:
            session.rollback()
            raise
        return {'dataset_id': dataset.id, 'source_count': len(sources),
                'policy_version': PRODUCTION_POLICY_VERSION,
                'authorization_manifest_sha256': policy['authorization_manifest_sha256']}


class ReattestationAttestation(ClosedManifest):
    operator_id: Identifier
    statement: Text


def _registered_dataset(session: Session, dataset_id: str) -> tuple[CurationDataset, dict]:
    if session.new or session.dirty or session.deleted or session.in_transaction():
        fail('registration_requires_clean_session')
    if not isinstance(dataset_id, str) or not 1 <= len(dataset_id) <= 128:
        fail('not_found', 404)
    dataset = session.get(CurationDataset, dataset_id, populate_existing=True)
    if dataset is None:
        fail('not_found', 404)
    return dataset, registered_source_volume(dataset)


def _observe_registered_roots(dataset: CurationDataset, expected: dict):
    """Fresh observation of both roots in this runtime; identity failures raise."""
    with directory(dataset.source_root) as source_fd, directory(dataset.store_root) as store_fd:
        source_stat, store_stat = os.fstat(source_fd), os.fstat(store_fd)
        # A replaced directory is a different root even on the same volume. On a
        # filesystem without persistent inode numbers the source inode is not
        # comparable and the file hashes re-verified below carry that fence.
        source_device, source_inode = source_root_identity(dataset)
        if (source_stat.st_dev != source_device
                or (source_inode is not None and source_stat.st_ino != source_inode)
                or store_stat.st_ino != dataset.store_inode):
            fail('root_identity_changed')
        ensure_disjoint_roots(dataset.source_root, source_fd, dataset.store_root, store_fd)
        observed = observe_source_volume(dataset.source_root, opened_fd=source_fd)
    comparison = compare_source_volume(expected, observed)
    if comparison['persistent_mismatch']:
        fail('source_volume_identity_mismatch')
    return observed, comparison, source_stat, store_stat


def _candidate(dataset: CurationDataset, observed: dict, source_stat, store_stat) -> CurationDataset:
    """A transient copy pinned to the fresh observation; never added to a session."""
    policy = dict(dataset.policy)
    policy['source_volume'] = observed
    return CurationDataset(id=dataset.id, name=dataset.name, revision=dataset.revision,
        policy_version=dataset.policy_version, subject_id=dataset.subject_id,
        source_root=dataset.source_root, source_device=source_stat.st_dev,
        source_inode=dataset.source_inode, store_root=dataset.store_root,
        store_device=store_stat.st_dev, store_inode=dataset.store_inode, policy=policy)


def _verify_registered_files(session: Session, candidate: CurationDataset) -> int:
    """Re-read every registered file through fenced descriptors; hashes must match."""
    sources = session.exec(select(CurationSource).where(CurationSource.dataset_id == candidate.id)).all()
    if not sources:
        fail('not_found', 404)
    for source in sources:
        verify_source_volume(candidate, source.relative_path)
        with directory(candidate.source_root, source_root_identity(candidate)) as fd:
            read_at(fd, source.relative_path, source.sha256,
                    descriptor_guard=lambda fd: verify_source_descriptor(candidate, fd))
        verify_source_volume(candidate, source.relative_path)
    return len(sources)


def verify_dataset_volume(session: Session, dataset_id: str) -> dict:
    """Read-only: does this runtime still observe the registered volume and bytes?

    Writes nothing. ``current_identity_valid`` is False when only runtime fields
    drifted (restart, re-plug) and an explicit re-attestation would restore access.
    """
    dataset, expected = _registered_dataset(session, dataset_id)
    observed, comparison, source_stat, store_stat = _observe_registered_roots(dataset, expected)
    candidate = _candidate(dataset, observed, source_stat, store_stat)
    verified = _verify_registered_files(session, candidate)
    session.rollback()
    return {'dataset_id': dataset.id, 'revision': dataset.revision, 'source_count': verified,
            'runtime_drift': comparison['runtime_drift'],
            'current_identity_valid': (not comparison['runtime_drift']
                and source_stat.st_dev == dataset.source_device
                and store_stat.st_dev == dataset.store_device)}


def reattest_source_volume(session: Session, dataset_id: str, attestation: dict) -> dict:
    """Explicit, audited operator re-pin of runtime volume fields after a restart.

    The persistent identity (UUID, filesystem type and root, mountpoint) must
    match exactly and every registered file is re-read and hash-verified before
    anything changes. The dataset revision increments so in-flight reviews,
    materializations and exports bound to the old runtime identity conflict
    instead of silently continuing. Nothing is relocated and no threshold moves.
    """
    try:
        who = ReattestationAttestation.model_validate(attestation)
    except ValidationError:
        fail('invalid_registration_manifest', 422)
    dataset, expected = _registered_dataset(session, dataset_id)
    observed, comparison, source_stat, store_stat = _observe_registered_roots(dataset, expected)
    candidate = _candidate(dataset, observed, source_stat, store_stat)
    verified = _verify_registered_files(session, candidate)
    record = {'basis': 'operator_reattestation', 'reattested_at': datetime.now(UTC).isoformat(),
        'operator_id': who.operator_id, 'statement': who.statement,
        'previous_revision': dataset.revision, 'files_verified': verified,
        'runtime_drift': comparison['runtime_drift'],
        'previous_source_volume': expected, 'observed_source_volume': observed,
        'previous_source_device': dataset.source_device, 'previous_store_device': dataset.store_device,
        'previous_source_inode': dataset.source_inode}
    policy = dict(dataset.policy)
    policy['source_volume'] = observed
    policy['reattestations'] = [*policy.get('reattestations', []), record]
    dataset.policy = policy
    dataset.source_device = source_stat.st_dev
    dataset.store_device = store_stat.st_dev
    if source_root_identity(dataset)[1] is None:
        # Informational only on such filesystems; keep it current for the audit trail.
        dataset.source_inode = source_stat.st_ino
    dataset.revision += 1
    try:
        session.add(dataset)
        session.commit()
    except BaseException:
        session.rollback()
        raise
    # The committed pin must fence reads in this runtime before reporting success.
    verify_source_volume(dataset)
    return {'dataset_id': dataset.id, 'revision': dataset.revision, 'source_count': verified,
            'runtime_drift': comparison['runtime_drift']}
