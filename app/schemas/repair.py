from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

from app.models import ImageRepairStatus


class RepairParams(BaseModel):
    model_config = ConfigDict(extra="allow")

    prompt: str | None = Field(default=None, max_length=2000)
    seed: int | None = Field(default=None, ge=0)


class RepairRequest(BaseModel):
    profile: str = "omoide-upscale-v1"
    provider: str | None = None
    hosted_consent: StrictBool = False
    params: RepairParams = Field(default_factory=RepairParams)
    person_id: int | None = None

    @model_validator(mode="before")
    @classmethod
    def validate_raw_upscaler_params(cls, value):
        # RepairParams has legacy coercion for seed. Check upscaler JSON before
        # that coercion can turn booleans, floats or strings into integers.
        from app.services.upscalers import PROVIDER_PROFILES, normalize_params
        if isinstance(value, dict):
            provider = value.get("provider")
            profile = value.get("profile", "omoide-upscale-v1")
            if provider is None:
                provider = next((key for key, item in PROVIDER_PROFILES.items() if item == profile), None)
            params = value.get("params", {})
            if provider is not None and isinstance(params, dict):
                normalize_params(provider, params)
        return value

    @model_validator(mode="after")
    def validate_upscaler(self):
        from app.services.upscalers import PROVIDER_PROFILES, normalize_params
        if {"provider", "hosted_consent", "_transport"}.intersection(self.params.model_dump(exclude_none=True)):
            raise ValueError("Reserved repair parameter")
        if self.provider is None and self.profile in PROVIDER_PROFILES.values():
            self.provider = next(key for key, value in PROVIDER_PROFILES.items() if value == self.profile)
        if self.provider is not None:
            if self.provider not in PROVIDER_PROFILES:
                raise ValueError("Unknown upscaler provider")
            if self.profile not in {"omoide-upscale-v1", PROVIDER_PROFILES[self.provider]}:
                raise ValueError("Provider can only be selected for upscale repairs")
            if self.provider.startswith("hypir:") and self.hosted_consent is not True:
                raise ValueError("Hosted upscaling requires explicit per-run consent")
            self.profile = PROVIDER_PROFILES[self.provider]
            self.params = RepairParams.model_validate(normalize_params(self.provider, self.params.model_dump(exclude_none=True)))
        return self

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
