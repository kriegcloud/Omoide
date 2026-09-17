"""Closed input contracts: actor identities and filesystem paths are never inputs."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class Closed(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class RevisionInput(Closed):
    expected_revision: int = Field(ge=0)


class MaterializeInput(RevisionInput):
    source_id: str = Field(min_length=1, max_length=64)
    idempotency_key: str = Field(min_length=8, max_length=128, pattern=r'^[a-zA-Z0-9_-]+$')


class CaptionInput(RevisionInput):
    artifact_id: str = Field(min_length=1, max_length=64)
    text: str = Field(min_length=1, max_length=8192)


class PresenceInput(Closed):
    challenge_id: str = Field(min_length=1, max_length=64)
    credential: dict


class RegistrationInput(PresenceInput):
    pass


class EmptyInput(Closed):
    pass


class ReviewInput(RevisionInput):
    artifact_id: str = Field(min_length=1, max_length=64)
    caption_id: str = Field(min_length=1, max_length=64)
    asset_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    caption_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    decision: Literal['accept', 'reject', 'defer']
    rationale: str = Field(default='', max_length=2048)
    presence: PresenceInput | None = None


class ExportInput(RevisionInput):
    idempotency_key: str = Field(min_length=8, max_length=128, pattern=r'^[a-zA-Z0-9_-]+$')
