from __future__ import annotations

import unicodedata
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models import (
    AnnotationAttemptStatus,
    AnnotationAuthor,
    AnnotationKind,
    AnnotationReviewStatus,
)

# The pinned WD label set's largest namespace contains 8,106 observations.
# Keep bounded headroom for that raw result while rejecting pathological edits.
MAX_ANNOTATION_TAGS_PER_NAMESPACE = 10_000


class AnnotationGenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: AnnotationKind


class AnnotationTagScore(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=256)
    score: float = Field(ge=0.0, le=1.0)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = unicodedata.normalize("NFKC", value).strip()
        if not normalized:
            raise ValueError("tag name cannot be blank")
        return normalized


class AnnotationCaptionContent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=32_768)


class AnnotationTagsContent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rating: list[AnnotationTagScore] = Field(
        default_factory=list,
        max_length=MAX_ANNOTATION_TAGS_PER_NAMESPACE,
    )
    general: list[AnnotationTagScore] = Field(
        default_factory=list,
        max_length=MAX_ANNOTATION_TAGS_PER_NAMESPACE,
    )
    character: list[AnnotationTagScore] = Field(
        default_factory=list,
        max_length=MAX_ANNOTATION_TAGS_PER_NAMESPACE,
    )

    @model_validator(mode="after")
    def reject_duplicate_names(self) -> "AnnotationTagsContent":
        for namespace in ("rating", "general", "character"):
            seen: set[str] = set()
            for entry in getattr(self, namespace):
                key = entry.name.casefold()
                if key in seen:
                    raise ValueError(
                        f"duplicate normalized tag name in {namespace}: {entry.name}"
                    )
                seen.add(key)
        return self


class AnnotationRevisionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: dict[str, Any]


class AnnotationAttemptRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    media_id: int
    kind: AnnotationKind
    profile_id: str
    backend: str
    status: AnnotationAttemptStatus
    external_prompt_id: str
    predecessor_attempt_id: str | None
    input_sha256: str | None
    workflow_sha256: str | None
    provenance: dict[str, Any] | None
    error_code: str | None
    error_message: str | None
    retryable: bool
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class AnnotationAttemptDetailRead(AnnotationAttemptRead):
    raw_result: dict[str, Any] | None
    normalized_result: dict[str, Any] | None


class MediaAnnotationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    media_id: int
    attempt_id: str | None
    parent_id: str | None
    revision: int
    kind: AnnotationKind
    author: AnnotationAuthor
    review_status: AnnotationReviewStatus
    schema_version: str
    content: dict[str, Any]
    provenance: dict[str, Any] | None
    created_at: datetime
    approved_at: datetime | None


class MediaAnnotationState(BaseModel):
    media_id: int
    attempts: list[AnnotationAttemptRead]
    annotations: list[MediaAnnotationRead]


class AnnotationHealthRead(BaseModel):
    enabled: bool
    ready: bool
    profiles: list[str] = Field(default_factory=list)
    configured_profiles: list[str] = Field(default_factory=list)
    unavailable_profiles: dict[str, str] = Field(default_factory=dict)
    active_attempt_id: str | None = None
    backend: Literal["comfy"] = "comfy"
    detail: str | None = None
