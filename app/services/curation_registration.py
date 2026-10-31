"""Trusted local, exact-manifest production registration; no HTTP grant minting."""
from datetime import UTC, datetime
import os
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError
from sqlmodel import Session

from app.curation_models import CurationDataset, CurationSource
from app.services.curation_artifacts import canonical, directory, read_at, safe_parts
from app.services.curation_policy import digest, fail
from app.services.source_locations import (PRODUCTION_POLICY_VERSION, ensure_disjoint_roots, observe_source_volume,
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


class SourceRegistrationManifest(ClosedManifest):
    schema_version: Literal['omoide.source-registration/v1']
    name: Annotated[str, StringConstraints(min_length=1, max_length=256)]
    subject_id: Identifier
    source_root: Text
    store_root: Text
    expected_filesystem_uuid: Annotated[str, StringConstraints(pattern=r'^[A-Za-z0-9.-]{1,128}$')]
    expected_mountpoint: Text
    attestation: OperatorAttestation
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
        observed = observe_source_volume(spec.source_root, opened_fd=source_fd)
        if observed['uuid'] != spec.expected_filesystem_uuid.casefold():
            fail('source_volume_uuid_mismatch')
        if observed['mountpoint'] != spec.expected_mountpoint:
            fail('source_mountpoint_mismatch')
        policy = {'version': PRODUCTION_POLICY_VERSION, 'fixture_only': False,
            'generative_enabled': False, 'human_presence_verified': False,
            'max_items': 100, 'source_access': 'read-only', 'egress': 'local-review-only',
            'source_volume': observed,
            'authorization_manifest_sha256': digest(canonical(spec.model_dump())),
            'registration': {'basis': 'operator_attestation',
                'assertions_are_independently_verified': False,
                'received_at': datetime.now(UTC).isoformat(),
                'attestation': spec.attestation.model_dump(), 'source_assertions': {}},
            'identity': {'matching_enabled': False, 'frame_mining_enabled': False,
                         'outlier_enabled': False}}
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
