"""Typed image-repair client for the allowlisted ComfyUI bridge."""

from __future__ import annotations

import base64
import binascii
import hashlib
import io
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from PIL import Image

from app.services.comfy_annotation import (
    ComfyAnnotationClient,
    ComfyAnnotationError,
    prepare_annotation_image,
)

SUPPORTED_REPAIR_PROFILES = frozenset(
    {
        "omoide-remove-text-v1",
        "omoide-upscale-v1",
        "omoide-remove-people-v1",
    }
)


@dataclass(frozen=True, slots=True)
class ComfyRepairResult:
    attempt_id: UUID
    profile_id: str
    input_sha256: str
    workflow_sha256: str
    image: bytes
    media_type: str
    sha256: str
    width: int
    height: int


def _uuid(value: object, field: str) -> UUID:
    try:
        parsed = UUID(value) if isinstance(value, str) else None
    except ValueError as error:
        raise ComfyAnnotationError("protocol-error", f"{field} must be a UUID") from error
    if parsed is None or str(parsed) != value:
        raise ComfyAnnotationError("protocol-error", f"{field} must be a UUID")
    return parsed


class ComfyRepairClient(ComfyAnnotationClient):
    """Shares framing and transport with annotations while parsing image results."""

    supported_health_profiles = SUPPORTED_REPAIR_PROFILES

    def repair(
        self,
        *,
        attempt_id: UUID,
        profile_id: str,
        image: Image.Image,
        params: dict[str, Any] | None = None,
    ) -> ComfyRepairResult:
        if profile_id not in SUPPORTED_REPAIR_PROFILES:
            raise ComfyAnnotationError("unknown-profile", "repair profile is not supported")
        prepared = prepare_annotation_image(image)
        payload: dict[str, Any] = {
            "protocol": "omoide-comfy/v1",
            "action": "repair",
            "attempt_id": str(attempt_id),
            "profile_id": profile_id,
            "image": base64.b64encode(prepared.data).decode("ascii"),
            "media_type": prepared.media_type,
            "image_sha256": prepared.sha256,
        }
        if params:
            payload["params"] = params
        response = self._request(payload)
        if response.get("kind") != "result":
            raise ComfyAnnotationError("protocol-error", "repair response has no result")
        returned_id = _uuid(response.get("attempt_id"), "attempt_id")
        prompt_id = _uuid(response.get("prompt_id"), "prompt_id")
        raw = response.get("raw_result")
        workflow_sha256 = response.get("workflow_sha256")
        if (
            returned_id != attempt_id
            or prompt_id != attempt_id
            or response.get("profile_id") != profile_id
            or response.get("image_sha256") != prepared.sha256
            or not isinstance(workflow_sha256, str)
            or len(workflow_sha256) != 64
            or not isinstance(raw, dict)
        ):
            raise ComfyAnnotationError("provenance-mismatch", "repair result does not match request")
        encoded = raw.get("image")
        try:
            data = base64.b64decode(encoded, validate=True) if isinstance(encoded, str) else b""
        except (ValueError, binascii.Error) as error:
            raise ComfyAnnotationError("protocol-error", "repair image is invalid base64") from error
        media_type = raw.get("media_type")
        digest = raw.get("sha256")
        width = raw.get("width")
        height = raw.get("height")
        if (
            not data
            or media_type not in {"image/png", "image/jpeg"}
            or digest != hashlib.sha256(data).hexdigest()
            or not isinstance(width, int)
            or width <= 0
            or not isinstance(height, int)
            or height <= 0
        ):
            raise ComfyAnnotationError("protocol-error", "repair image metadata is invalid")
        try:
            with Image.open(io.BytesIO(data)) as opened:
                opened.verify()
            with Image.open(io.BytesIO(data)) as opened:
                if opened.size != (width, height):
                    raise ComfyAnnotationError("protocol-error", "repair dimensions do not match")
        except ComfyAnnotationError:
            raise
        except (OSError, Image.DecompressionBombError) as error:
            raise ComfyAnnotationError("protocol-error", "repair image could not be decoded") from error
        return ComfyRepairResult(
            attempt_id=returned_id,
            profile_id=profile_id,
            input_sha256=prepared.sha256,
            workflow_sha256=workflow_sha256,
            image=data,
            media_type=media_type,
            sha256=digest,
            width=width,
            height=height,
        )
