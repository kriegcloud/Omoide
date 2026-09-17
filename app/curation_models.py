"""Additive curation provenance. No legacy media/annotation FK or backfill.

Content records are append-only through the service. Dataset revision and export
execution status are mutable control records; admitted export snapshots are not.
"""
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import Column, JSON, UniqueConstraint
from sqlmodel import Field, SQLModel


def uid() -> str:
    return uuid4().hex


def now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class CurationDataset(SQLModel, table=True):
    __tablename__ = 'curation_dataset'
    id: str = Field(default_factory=uid, primary_key=True)
    name: str
    revision: int = 0
    policy_version: str = 'fixture-stills-v1'
    subject_id: str
    source_root: str
    source_device: int
    source_inode: int
    store_root: str
    store_device: int
    store_inode: int
    policy: dict = Field(sa_column=Column(JSON, nullable=False))


class CurationGrant(SQLModel, table=True):
    __tablename__ = 'curation_grant'
    id: str = Field(default_factory=uid, primary_key=True)
    token_sha256: str = Field(unique=True)
    actor_id: str
    actor_kind: str
    dataset_id: str = Field(foreign_key='curation_dataset.id', index=True)
    expires_at: datetime
    revoked: bool = False
    disclosure: bool = False
    operations: list = Field(sa_column=Column(JSON, nullable=False))


class CurationSource(SQLModel, table=True):
    __tablename__ = 'curation_source'
    id: str = Field(default_factory=uid, primary_key=True)
    dataset_id: str = Field(foreign_key='curation_dataset.id', index=True)
    label: str
    relative_path: str
    sha256: str
    size: int
    group_id: str
    split: str = 'train'
    lineage_known: bool = True
    generative: bool = False


class CurationArtifact(SQLModel, table=True):
    __tablename__ = 'curation_artifact'
    __table_args__ = (UniqueConstraint('dataset_id', 'source_id', 'cache_key'),)
    id: str = Field(default_factory=uid, primary_key=True)
    dataset_id: str = Field(foreign_key='curation_dataset.id', index=True)
    source_id: str = Field(foreign_key='curation_source.id')
    parent_id: str | None = Field(default=None, foreign_key='curation_artifact.id')
    sha256: str
    pixel_sha256: str
    cache_key: str
    size: int
    width: int
    height: int
    generative: bool = False
    provenance: dict = Field(sa_column=Column(JSON, nullable=False))


class CurationCaption(SQLModel, table=True):
    __tablename__ = 'curation_caption'
    id: str = Field(default_factory=uid, primary_key=True)
    dataset_id: str = Field(foreign_key='curation_dataset.id', index=True)
    artifact_id: str = Field(foreign_key='curation_artifact.id')
    revision: int
    text: str
    sha256: str
    actor_id: str
    actor_kind: str
    created_at: datetime = Field(default_factory=now)


class CurationReview(SQLModel, table=True):
    __tablename__ = 'curation_review'
    id: str = Field(default_factory=uid, primary_key=True)
    dataset_id: str = Field(foreign_key='curation_dataset.id', index=True)
    artifact_id: str = Field(foreign_key='curation_artifact.id')
    caption_id: str = Field(foreign_key='curation_caption.id')
    asset_sha256: str
    caption_sha256: str
    decision: str
    rationale: str
    revision: int
    actor_id: str
    actor_kind: str
    grant_id: str = Field(foreign_key='curation_grant.id')
    policy_version: str
    presence_evidence: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    created_at: datetime = Field(default_factory=now)


class CurationCredential(SQLModel, table=True):
    """One enrolled public key per grant. Revocation never permits replacement."""
    __tablename__ = 'curation_credential'
    id: str = Field(default_factory=uid, primary_key=True)
    grant_id: str = Field(foreign_key='curation_grant.id', unique=True)
    credential_id: str = Field(unique=True)
    public_key: str
    sign_count: int
    rp_id: str
    origin: str
    revoked: bool = False
    registration_sha256: str
    created_at: datetime = Field(default_factory=now)


class CurationChallenge(SQLModel, table=True):
    """A server-owned, expiring ceremony bound to one exact operation."""
    __tablename__ = 'curation_challenge'
    id: str = Field(default_factory=uid, primary_key=True)
    grant_id: str = Field(foreign_key='curation_grant.id', index=True)
    purpose: str
    challenge: str = Field(unique=True)
    request_sha256: str
    credential_id: str | None = Field(default=None, foreign_key='curation_credential.id')
    rp_id: str
    origin: str
    expires_at: datetime
    consumed_at: datetime | None = None
    created_at: datetime = Field(default_factory=now)


class CurationOperation(SQLModel, table=True):
    __tablename__ = 'curation_operation'
    __table_args__ = (UniqueConstraint('grant_id', 'kind', 'idempotency_key'),)
    id: str = Field(default_factory=uid, primary_key=True)
    dataset_id: str = Field(foreign_key='curation_dataset.id', index=True)
    grant_id: str = Field(foreign_key='curation_grant.id')
    kind: str
    idempotency_key: str
    request_sha256: str
    status: str = 'admitted'
    snapshot_revision: int
    snapshot: dict = Field(sa_column=Column(JSON, nullable=False))
    manifest_sha256: str | None = None
    error_code: str | None = None
    item_count: int = 0
    attempts: int = 0
    # Execution control. The snapshot above stays immutable; these fields only
    # record who is currently allowed to execute it and how far they got.
    # `task_id` is the shared ProcessingTask row; no FK, so the generic task
    # lifecycle can never delete or block curation provenance.
    task_id: str | None = None
    lease_worker: str | None = None
    lease_attempt: str | None = None
    lease_expires_at: datetime | None = None
    progress_done: int = 0
    created_at: datetime = Field(default_factory=now)


class CurationEvent(SQLModel, table=True):
    __tablename__ = 'curation_event'
    id: str = Field(default_factory=uid, primary_key=True)
    operation_id: str = Field(foreign_key='curation_operation.id', index=True)
    event: str
    attempt: int
    created_at: datetime = Field(default_factory=now)
