"""Typed client for Omoide's allowlisted host-side ComfyUI bridge."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import socket
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from PIL import Image, ImageOps

PROTOCOL_VERSION = "omoide-comfy/v1"
CAPTION_PROFILE_ID = "omoide-caption-v1"
TAGS_PROFILE_ID = "omoide-tags-v1"
SUPPORTED_PROFILE_IDS = frozenset({CAPTION_PROFILE_ID, TAGS_PROFILE_ID})
MAX_REQUEST_BYTES = 32 * 1024 * 1024
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_IMAGE_BYTES = 16 * 1024 * 1024
MAX_IMAGE_DIMENSION = 16_384
MAX_IMAGE_PIXELS = 40_000_000
# Align Pillow's decoder policy with this boundary instead of inheriting its
# substantially larger process default.
Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
AttemptStatus = Literal[
    "queued",
    "running",
    "output-pending",
    "failed",
    "cancelled",
    "unknown",
]


class ComfyAnnotationError(RuntimeError):
    """Classified bridge, transport, validation, or Comfy execution failure."""

    def __init__(
        self,
        code: str,
        message: str,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class PreparedAnnotationImage:
    data: bytes
    media_type: str
    sha256: str
    width: int
    height: int


@dataclass(frozen=True, slots=True)
class ComfyBridgeHealth:
    ready: bool
    profiles: tuple[str, ...]
    configured_profiles: tuple[str, ...]
    unavailable_profiles: dict[str, str]
    active_attempt_id: UUID | None
    comfy_url: str


@dataclass(frozen=True, slots=True)
class ComfyAnnotationResult:
    attempt_id: UUID
    prompt_id: UUID
    profile_id: str
    image_sha256: str
    workflow_sha256: str
    raw_result: Any
    profile_provenance: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class ComfyAttemptState:
    attempt_id: UUID
    prompt_id: UUID
    status: AttemptStatus
    profile_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True, slots=True)
class ComfyCancelResult:
    attempt_id: UUID
    status: str


@dataclass(frozen=True, slots=True)
class ComfyAckResult:
    attempt_id: UUID
    status: Literal["deleted", "already-absent"]


def _has_alpha(image: Image.Image) -> bool:
    return image.mode in {"RGBA", "LA"} or (
        image.mode == "P" and "transparency" in image.info
    )


def prepare_annotation_image(image: Image.Image) -> PreparedAnnotationImage:
    """Orient and re-encode one still without EXIF or other source metadata."""

    if not isinstance(image, Image.Image):
        raise ComfyAnnotationError(
            "invalid-image",
            "annotation input must be a Pillow image",
        )
    if getattr(image, "n_frames", 1) != 1:
        raise ComfyAnnotationError(
            "invalid-image",
            "animated images must be reduced to one explicit frame",
        )
    try:
        # Reading the header size is allocation-cheap. Reject oversized images
        # before Pillow decodes their pixel payload or applies orientation.
        source_width, source_height = image.size
        if source_width <= 0 or source_height <= 0:
            raise ComfyAnnotationError(
                "invalid-image",
                "image dimensions must be positive",
            )
        if (
            source_width > MAX_IMAGE_DIMENSION
            or source_height > MAX_IMAGE_DIMENSION
        ):
            raise ComfyAnnotationError(
                "input-limit-exceeded",
                "image dimensions are too large",
            )
        if source_width * source_height > MAX_IMAGE_PIXELS:
            raise ComfyAnnotationError(
                "input-limit-exceeded",
                "image pixel count is too large",
            )
        image.load()
        oriented = ImageOps.exif_transpose(image)
        width, height = oriented.size
        buffer = io.BytesIO()
        if _has_alpha(oriented):
            converted = oriented.convert("RGBA")
            background = Image.new("RGBA", converted.size, (255, 255, 255, 255))
            clean = Image.alpha_composite(background, converted).convert("RGB")
            clean.save(buffer, format="PNG", compress_level=6)
            media_type = "image/png"
        else:
            converted = oriented.convert("RGB")
            clean = Image.new("RGB", converted.size)
            clean.paste(converted)
            clean.save(
                buffer,
                format="JPEG",
                quality=95,
                subsampling=0,
                optimize=False,
            )
            media_type = "image/jpeg"
    except ComfyAnnotationError:
        raise
    except (OSError, ValueError, Image.DecompressionBombError) as error:
        raise ComfyAnnotationError(
            "invalid-image",
            "image could not be normalized",
        ) from error
    data = buffer.getvalue()
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise ComfyAnnotationError(
            "input-limit-exceeded",
            "normalized image is empty or exceeds the encoded byte limit",
        )
    return PreparedAnnotationImage(
        data=data,
        media_type=media_type,
        sha256=hashlib.sha256(data).hexdigest(),
        width=width,
        height=height,
    )


def _read_exact(connection: socket.socket, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = connection.recv(remaining)
        if not chunk:
            raise ComfyAnnotationError(
                "protocol-error",
                "bridge response ended before its declared length",
            )
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _parse_uuid(value: object, field: str) -> UUID:
    if not isinstance(value, str):
        raise ComfyAnnotationError("protocol-error", f"{field} must be a UUID")
    try:
        parsed = UUID(value)
    except ValueError as error:
        raise ComfyAnnotationError("protocol-error", f"{field} must be a UUID") from error
    if str(parsed) != value:
        raise ComfyAnnotationError(
            "protocol-error",
            f"{field} must be a canonical UUID",
        )
    return parsed


class ComfyAnnotationClient:
    """Synchronous typed client used from Omoide's durable task boundary."""

    def __init__(self, socket_path: Path, timeout_seconds: float = 900.0) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.socket_path = socket_path
        self.timeout_seconds = timeout_seconds

    def _request(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            encoded = json.dumps(
                payload,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError) as error:
            raise ComfyAnnotationError(
                "protocol-error",
                "bridge request could not be encoded",
            ) from error
        if not encoded or len(encoded) > MAX_REQUEST_BYTES:
            raise ComfyAnnotationError(
                "input-limit-exceeded",
                "bridge request exceeds its byte limit",
            )
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(self.timeout_seconds)
                connection.connect(str(self.socket_path))
                connection.sendall(struct.pack("!I", len(encoded)) + encoded)
                size = struct.unpack("!I", _read_exact(connection, 4))[0]
                if size == 0 or size > MAX_RESPONSE_BYTES:
                    raise ComfyAnnotationError(
                        "protocol-error",
                        f"bridge response size {size} is not allowed",
                    )
                response = json.loads(_read_exact(connection, size))
        except ComfyAnnotationError:
            raise
        except (OSError, TimeoutError, ValueError, json.JSONDecodeError) as error:
            raise ComfyAnnotationError(
                "service-unavailable",
                "Comfy annotation bridge is unavailable",
                retryable=True,
            ) from error
        if not isinstance(response, dict):
            raise ComfyAnnotationError(
                "protocol-error",
                "bridge response JSON must be an object",
            )
        if response.get("protocol") != PROTOCOL_VERSION:
            raise ComfyAnnotationError(
                "protocol-error",
                "bridge returned an unsupported protocol",
            )
        if response.get("ok") is not True:
            failure = response.get("error")
            if not isinstance(failure, dict):
                raise ComfyAnnotationError(
                    "service-failed",
                    "bridge returned an invalid failure",
                )
            raise ComfyAnnotationError(
                str(failure.get("code", "service-failed")),
                str(failure.get("message", "annotation bridge request failed")),
                retryable=failure.get("retryable") is True,
            )
        return response

    @staticmethod
    def _parse_result(response: dict[str, Any]) -> ComfyAnnotationResult:
        profile_id = response.get("profile_id")
        image_sha256 = response.get("image_sha256")
        workflow_sha256 = response.get("workflow_sha256")
        profile_provenance = response.get("profile_provenance")
        if (
            not isinstance(profile_id, str)
            or profile_id not in SUPPORTED_PROFILE_IDS
            or not isinstance(image_sha256, str)
            or len(image_sha256) != 64
            or not isinstance(workflow_sha256, str)
            or len(workflow_sha256) != 64
            or not isinstance(profile_provenance, dict)
            or profile_provenance.get("profile_id") != profile_id
            or profile_provenance.get("workflow_sha256") != workflow_sha256
        ):
            raise ComfyAnnotationError(
                "protocol-error",
                "bridge result provenance is invalid",
            )
        return ComfyAnnotationResult(
            attempt_id=_parse_uuid(response.get("attempt_id"), "attempt_id"),
            prompt_id=_parse_uuid(response.get("prompt_id"), "prompt_id"),
            profile_id=profile_id,
            image_sha256=image_sha256,
            workflow_sha256=workflow_sha256,
            raw_result=response.get("raw_result"),
            profile_provenance=profile_provenance,
        )

    @staticmethod
    def _parse_state(response: dict[str, Any]) -> ComfyAttemptState:
        status = response.get("status")
        allowed_statuses = {
            "queued",
            "running",
            "output-pending",
            "failed",
            "cancelled",
            "unknown",
        }
        profile_id = response.get("profile_id")
        if status not in allowed_statuses or (
            profile_id is not None and profile_id not in SUPPORTED_PROFILE_IDS
        ):
            raise ComfyAnnotationError(
                "protocol-error",
                "bridge attempt state is invalid",
            )
        error_code = response.get("error_code")
        error_message = response.get("error_message")
        if error_code is not None and not isinstance(error_code, str):
            raise ComfyAnnotationError("protocol-error", "error_code must be text")
        if error_message is not None and not isinstance(error_message, str):
            raise ComfyAnnotationError("protocol-error", "error_message must be text")
        return ComfyAttemptState(
            attempt_id=_parse_uuid(response.get("attempt_id"), "attempt_id"),
            prompt_id=_parse_uuid(response.get("prompt_id"), "prompt_id"),
            status=status,
            profile_id=profile_id,
            error_code=error_code,
            error_message=error_message,
        )

    def health(self) -> ComfyBridgeHealth:
        response = self._request(
            {"protocol": PROTOCOL_VERSION, "action": "health"}
        )
        profiles = response.get("profiles")
        configured_profiles = response.get("configured_profiles")
        unavailable_profiles = response.get("unavailable_profiles")
        active_attempt_id = response.get("active_attempt_id")
        comfy_url = response.get("comfy_url")
        if (
            not isinstance(response.get("ready"), bool)
            or not isinstance(profiles, list)
            or not isinstance(configured_profiles, list)
            or not isinstance(unavailable_profiles, dict)
            or any(
                not isinstance(profile, str) or profile not in SUPPORTED_PROFILE_IDS
                for profile in profiles
            )
            or any(
                not isinstance(profile, str) or profile not in SUPPORTED_PROFILE_IDS
                for profile in configured_profiles
            )
            or any(
                not isinstance(profile, str)
                or profile not in SUPPORTED_PROFILE_IDS
                or not isinstance(reason, str)
                for profile, reason in unavailable_profiles.items()
            )
            or not isinstance(comfy_url, str)
        ):
            raise ComfyAnnotationError(
                "protocol-error",
                "bridge health response is invalid",
            )
        return ComfyBridgeHealth(
            ready=response["ready"],
            profiles=tuple(profiles),
            configured_profiles=tuple(configured_profiles),
            unavailable_profiles=dict(unavailable_profiles),
            active_attempt_id=(
                _parse_uuid(active_attempt_id, "active_attempt_id")
                if active_attempt_id is not None
                else None
            ),
            comfy_url=comfy_url,
        )

    def annotate(
        self,
        *,
        attempt_id: UUID,
        profile_id: str,
        image: Image.Image,
    ) -> ComfyAnnotationResult:
        if profile_id not in SUPPORTED_PROFILE_IDS:
            raise ComfyAnnotationError(
                "unknown-profile",
                "annotation profile is not supported by this client",
            )
        prepared = prepare_annotation_image(image)
        response = self._request(
            {
                "protocol": PROTOCOL_VERSION,
                "action": "annotate",
                "attempt_id": str(attempt_id),
                "profile_id": profile_id,
                "image": base64.b64encode(prepared.data).decode("ascii"),
                "media_type": prepared.media_type,
                "image_sha256": prepared.sha256,
            }
        )
        if response.get("kind") != "result":
            raise ComfyAnnotationError(
                "protocol-error",
                "annotate response did not contain a result",
            )
        result = self._parse_result(response)
        if (
            result.attempt_id != attempt_id
            or result.prompt_id != attempt_id
            or result.profile_id != profile_id
            or result.image_sha256 != prepared.sha256
        ):
            raise ComfyAnnotationError(
                "provenance-mismatch",
                "bridge result does not match the submitted attempt",
            )
        return result

    def get_attempt(
        self,
        attempt_id: UUID,
    ) -> ComfyAnnotationResult | ComfyAttemptState:
        response = self._request(
            {
                "protocol": PROTOCOL_VERSION,
                "action": "get_attempt",
                "attempt_id": str(attempt_id),
            }
        )
        if response.get("kind") == "result":
            result = self._parse_result(response)
            if result.attempt_id != attempt_id or result.prompt_id != attempt_id:
                raise ComfyAnnotationError(
                    "provenance-mismatch",
                    "bridge result belongs to a different attempt",
                )
            return result
        if response.get("kind") == "state":
            state = self._parse_state(response)
            if state.attempt_id != attempt_id or state.prompt_id != attempt_id:
                raise ComfyAnnotationError(
                    "provenance-mismatch",
                    "bridge state belongs to a different attempt",
                )
            return state
        raise ComfyAnnotationError(
            "protocol-error",
            "get_attempt response has an invalid kind",
        )

    def cancel(self, *, attempt_id: UUID) -> ComfyCancelResult:
        response = self._request(
            {
                "protocol": PROTOCOL_VERSION,
                "action": "cancel",
                "attempt_id": str(attempt_id),
            }
        )
        returned_id = _parse_uuid(response.get("attempt_id"), "attempt_id")
        status = response.get("status")
        if returned_id != attempt_id or not isinstance(status, str):
            raise ComfyAnnotationError(
                "provenance-mismatch",
                "cancel response does not match the requested attempt",
            )
        return ComfyCancelResult(attempt_id=returned_id, status=status)

    def ack_attempt(self, *, attempt_id: UUID) -> ComfyAckResult:
        response = self._request(
            {
                "protocol": PROTOCOL_VERSION,
                "action": "ack_attempt",
                "attempt_id": str(attempt_id),
            }
        )
        returned_id = _parse_uuid(response.get("attempt_id"), "attempt_id")
        status = response.get("status")
        if returned_id != attempt_id or status not in {"deleted", "already-absent"}:
            raise ComfyAnnotationError(
                "provenance-mismatch",
                "acknowledgement response does not match the requested attempt",
            )
        return ComfyAckResult(attempt_id=returned_id, status=status)
