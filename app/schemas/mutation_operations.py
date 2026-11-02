"""Authoritative outcomes for synchronous, checkpointed destructive mutations."""
from typing import Literal
from pydantic import BaseModel, Field


class SkippedMutation(BaseModel):
    key: str
    reason: str


class FailedMutation(BaseModel):
    key: str
    code: str
    message: str


class MutationReceipt(BaseModel):
    operation_id: str
    state: Literal['pending', 'succeeded', 'partial', 'failed', 'unknown']
    applied: list[str] = Field(default_factory=list)
    skipped: list[SkippedMutation] = Field(default_factory=list)
    failed: list[FailedMutation] = Field(default_factory=list)
    unknown: list[str] = Field(default_factory=list)
