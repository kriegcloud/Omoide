from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models import ImageRepairStatus


class RepairParams(BaseModel):
    model_config = ConfigDict(extra="allow")

    prompt: str | None = Field(default=None, max_length=2000)
    seed: int | None = Field(default=None, ge=0)


class RepairRequest(BaseModel):
    profile: str
    params: RepairParams = Field(default_factory=RepairParams)
    person_id: int | None = None

    @model_validator(mode="after")
    def validate_background_prompt(self):
        if (
            self.profile == "omoide-background-swap-v1"
            and not getattr(self, "randomize_prompts", False)
            and not (self.params.prompt or "").strip()
        ):
            raise ValueError("Background swap requires a prompt unless prompts are randomised")
        return self


class BulkRepairRequest(RepairRequest):
    media_ids: list[int] = Field(min_length=1, max_length=1000)
    randomize_prompts: bool = False


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
