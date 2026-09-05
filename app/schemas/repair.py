from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models import ImageRepairStatus


class RepairRequest(BaseModel):
    profile: str
    params: dict[str, Any] = Field(default_factory=dict)
    person_id: int | None = None


class BulkRepairRequest(RepairRequest):
    media_ids: list[int] = Field(min_length=1, max_length=1000)


class ImageRepairJobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    media_id: int
    profile: str
    params: dict[str, Any]
    status: ImageRepairStatus
    external_prompt_id: str | None
    result_media_id: int | None
    mask_path: str | None
    error_code: str | None
    error_message: str | None
    retryable: bool
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class RepairJobPage(BaseModel):
    items: list[ImageRepairJobRead]
    next_cursor: str | None


class RepairHealthRead(BaseModel):
    enabled: bool
    ready: bool
    profiles: list[str] = Field(default_factory=list)
    detail: str | None = None
