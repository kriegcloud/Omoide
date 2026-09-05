"""Typed post-training generation client for the ComfyUI bridge."""

from __future__ import annotations

import base64
import binascii
import hashlib
import io
import json
import socket
import struct
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from PIL import Image

from app.services.comfy_annotation import (
    MAX_IMAGE_RESPONSE_BYTES,
    MAX_REQUEST_BYTES,
    MAX_RESPONSE_BYTES,
    PROTOCOL_VERSION,
    ComfyAnnotationClient,
    ComfyAnnotationError,
    ComfyBridgeHealth,
    _parse_uuid,
    _read_exact,
)

SUPPORTED_EVAL_PROFILES = frozenset({"omoide-eval-zimage-v1"})


@dataclass(frozen=True, slots=True)
class ComfyEvalResult:
    attempt_id: UUID
    profile_id: str
    params_sha256: str
    workflow_sha256: str
    image: bytes
    media_type: str
    sha256: str
    width: int
    height: int


class ComfyEvalClient(ComfyAnnotationClient):
    """Use the shared socket without widening annotation's fixed allowlist."""

    def _request(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            encoded = json.dumps(payload, separators=(",", ":"), allow_nan=False).encode("utf-8")
        except (TypeError, ValueError) as error:
            raise ComfyAnnotationError("protocol-error", "bridge request could not be encoded") from error
        if not encoded or len(encoded) > MAX_REQUEST_BYTES:
            raise ComfyAnnotationError("input-limit-exceeded", "bridge request exceeds its byte limit")
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(self.timeout_seconds)
                connection.connect(str(self.socket_path))
                connection.sendall(struct.pack("!I", len(encoded)) + encoded)
                size = struct.unpack("!I", _read_exact(connection, 4))[0]
                response_limit = MAX_IMAGE_RESPONSE_BYTES if payload.get("action") == "generate" else MAX_RESPONSE_BYTES
                if size == 0 or size > response_limit:
                    raise ComfyAnnotationError("protocol-error", f"bridge response size {size} is not allowed")
                response = json.loads(_read_exact(connection, size))
        except ComfyAnnotationError:
            raise
        except (OSError, TimeoutError, ValueError, json.JSONDecodeError) as error:
            raise ComfyAnnotationError("service-unavailable", "Comfy evaluation bridge is unavailable", retryable=True) from error
        if not isinstance(response, dict) or response.get("protocol") != PROTOCOL_VERSION:
            raise ComfyAnnotationError("protocol-error", "bridge returned an invalid response")
        if response.get("ok") is not True:
            failure = response.get("error")
            if not isinstance(failure, dict):
                raise ComfyAnnotationError("service-failed", "bridge returned an invalid failure")
            raise ComfyAnnotationError(
                str(failure.get("code", "service-failed")),
                str(failure.get("message", "evaluation bridge request failed")),
                retryable=failure.get("retryable") is True,
            )
        return response

    def health(self) -> ComfyBridgeHealth:
        response = self._request({"protocol": PROTOCOL_VERSION, "action": "health"})
        profiles = response.get("profiles")
        configured = response.get("configured_profiles")
        unavailable = response.get("unavailable_profiles")
        active = response.get("active_attempt_id")
        comfy_url = response.get("comfy_url")
        result_kinds = response.get("profile_result_kinds", {})
        if not isinstance(profiles, list) or not isinstance(configured, list) or not isinstance(unavailable, dict) or not isinstance(result_kinds, dict) or not isinstance(comfy_url, str):
            raise ComfyAnnotationError("protocol-error", "bridge health response is invalid")
        ready = tuple(value for value in profiles if value in SUPPORTED_EVAL_PROFILES)
        return ComfyBridgeHealth(
            ready=bool(ready),
            profiles=ready,
            configured_profiles=tuple(value for value in configured if value in SUPPORTED_EVAL_PROFILES),
            unavailable_profiles={key: value for key, value in unavailable.items() if key in SUPPORTED_EVAL_PROFILES and isinstance(value, str)},
            active_attempt_id=_parse_uuid(active, "active_attempt_id") if active is not None else None,
            comfy_url=comfy_url,
            profile_result_kinds={key: value for key, value in result_kinds.items() if key in SUPPORTED_EVAL_PROFILES and isinstance(value, str)},
        )

    def generate(self, attempt_id: UUID, profile_id: str, params: dict[str, Any]) -> ComfyEvalResult:
        if profile_id not in SUPPORTED_EVAL_PROFILES:
            raise ComfyAnnotationError("unknown-profile", "evaluation profile is not supported")
        canonical = json.dumps(params, allow_nan=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
        params_sha256 = hashlib.sha256(canonical).hexdigest()
        response = self._request({
            "protocol": PROTOCOL_VERSION,
            "action": "generate",
            "attempt_id": str(attempt_id),
            "profile_id": profile_id,
            "params": params,
        })
        raw = response.get("raw_result")
        workflow_sha256 = response.get("workflow_sha256")
        if (
            response.get("kind") != "result"
            or _parse_uuid(response.get("attempt_id"), "attempt_id") != attempt_id
            or _parse_uuid(response.get("prompt_id"), "prompt_id") != attempt_id
            or response.get("profile_id") != profile_id
            or response.get("image_sha256") != params_sha256
            or not isinstance(workflow_sha256, str)
            or len(workflow_sha256) != 64
            or not isinstance(raw, dict)
        ):
            raise ComfyAnnotationError("provenance-mismatch", "evaluation result does not match request")
        encoded = raw.get("image")
        try:
            data = base64.b64decode(encoded, validate=True) if isinstance(encoded, str) else b""
        except (ValueError, binascii.Error) as error:
            raise ComfyAnnotationError("protocol-error", "evaluation image is invalid base64") from error
        media_type = raw.get("media_type")
        digest = raw.get("sha256")
        width = raw.get("width")
        height = raw.get("height")
        if not data or media_type not in {"image/png", "image/jpeg"} or digest != hashlib.sha256(data).hexdigest() or not isinstance(width, int) or width <= 0 or not isinstance(height, int) or height <= 0:
            raise ComfyAnnotationError("protocol-error", "evaluation image metadata is invalid")
        try:
            with Image.open(io.BytesIO(data)) as opened:
                opened.verify()
            with Image.open(io.BytesIO(data)) as opened:
                if opened.size != (width, height):
                    raise ComfyAnnotationError("protocol-error", "evaluation dimensions do not match")
        except ComfyAnnotationError:
            raise
        except (OSError, Image.DecompressionBombError) as error:
            raise ComfyAnnotationError("protocol-error", "evaluation image could not be decoded") from error
        return ComfyEvalResult(attempt_id, profile_id, params_sha256, workflow_sha256, data, media_type, digest, width, height)
