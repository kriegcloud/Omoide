"""Allowlisted ComfyUI annotation bridge and Unix-socket server."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
import os
import signal
import socket
import stat
import threading
import time
import io
from collections import OrderedDict
from pathlib import Path
from typing import Any, Callable
from uuid import UUID

from PIL import Image

from .comfy_http import ComfyHttpClient, ComfyTransportError
from .config import BridgeConfig, Profile, STAGING_SUBFOLDER
from .errors import BridgeError
from .image_validation import ValidatedImage, validate_image
from .protocol import PROTOCOL_VERSION, read_message, write_message

LOGGER = logging.getLogger(__name__)
METADATA_KEY = "omoide_annotation"
MAX_RESULT_TEXT_BYTES = 2 * 1024 * 1024
MAX_RESULT_IMAGE_BYTES = 64 * 1024 * 1024
MAX_PARAMS_BYTES = 8 * 1024
MAX_CANCELLED_CACHE = 256
MAX_CONNECTION_WORKERS = 4
_TRANSIENT_READ_CODES = frozenset(
    {"comfy-unavailable", "comfy-http-error", "comfy-protocol-error"}
)


def _canonical_attempt_id(value: object) -> str:
    if not isinstance(value, str):
        raise BridgeError("protocol-error", "attempt_id must be a UUID string")
    try:
        parsed = UUID(value)
    except ValueError as error:
        raise BridgeError("protocol-error", "attempt_id must be a UUID string") from error
    canonical = str(parsed)
    if canonical != value:
        raise BridgeError("protocol-error", "attempt_id must be canonical lowercase UUID")
    return canonical


def _require_request_keys(
    request: dict[str, Any],
    *,
    allowed: set[str],
    required: set[str],
) -> None:
    unknown = set(request) - allowed
    missing = required - set(request)
    if unknown:
        raise BridgeError("protocol-error", "request contains unsupported fields")
    if missing:
        raise BridgeError("protocol-error", "request is missing required fields")


def _metadata_from_prompt(prompt: object) -> dict[str, Any] | None:
    if not isinstance(prompt, list) or len(prompt) < 4:
        return None
    extra_data = prompt[3]
    if not isinstance(extra_data, dict):
        return None
    metadata = extra_data.get(METADATA_KEY)
    return metadata if isinstance(metadata, dict) else None


class BridgeService:
    """Owns admission, staging, submission, polling, and reconciliation."""

    def __init__(
        self,
        config: BridgeConfig,
        *,
        http_client: ComfyHttpClient | None = None,
        profile_readiness: Callable[[Profile], tuple[bool, str | None]] | None = None,
    ) -> None:
        self.config = config
        self.http = http_client or ComfyHttpClient(config.comfy_base_url)
        self._annotation_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._active_attempt_id: str | None = None
        self._cancel_events: dict[str, threading.Event] = {}
        self._cancelled: OrderedDict[str, None] = OrderedDict()
        self._artifact_cache: dict[Path, tuple[tuple[int, int, int, int], bool]] = {}
        self._profile_readiness = profile_readiness

    def _artifact_ready(self, path: Path, size: int, expected_sha256: str) -> bool:
        try:
            metadata = path.stat()
        except OSError:
            return False
        identity = (
            metadata.st_ino,
            metadata.st_size,
            metadata.st_mtime_ns,
            metadata.st_ctime_ns,
        )
        cached = self._artifact_cache.get(path)
        if cached is not None and cached[0] == identity:
            return cached[1]
        valid = metadata.st_size == size
        if valid:
            digest = hashlib.sha256()
            try:
                with path.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(chunk)
                valid = digest.hexdigest() == expected_sha256
            except OSError:
                valid = False
        self._artifact_cache[path] = (identity, valid)
        return valid

    def _default_profile_readiness(self, profile: Profile) -> tuple[bool, str | None]:
        catalogs: dict[str, dict[str, Any]] = {}
        try:
            stats = self.http.system_stats()
            system = stats.get("system")
            if not isinstance(system, dict):
                return False, "comfy-version-unavailable"
            if system.get("comfyui_version") != profile.comfy_version:
                return False, "comfy-version-mismatch"
            for node_class in profile.required_node_classes:
                catalog = self.http.object_info(node_class)
                node_info = catalog.get(node_class)
                if not isinstance(node_info, dict):
                    return False, f"node-unavailable:{node_class}"
                catalogs[node_class] = node_info
        except BridgeError:
            return False, "node-catalog-unavailable"

        for node_class, widget, expected in profile.required_combo_values:
            node_info = catalogs.get(node_class, {})
            input_info = node_info.get("input")
            choices: object | None = None
            if isinstance(input_info, dict):
                for group in ("required", "optional"):
                    group_info = input_info.get(group)
                    if isinstance(group_info, dict) and widget in group_info:
                        spec = group_info[widget]
                        if isinstance(spec, list) and spec:
                            choices = spec[0]
                        break
            if not isinstance(choices, list) or expected not in choices:
                return False, f"model-not-catalogued:{expected}"

        for artifact in profile.artifacts:
            if not artifact.path.is_file():
                return False, f"artifact-missing:{artifact.name}"
            if not self._artifact_ready(
                artifact.path,
                artifact.bytes_expected,
                artifact.sha256,
            ):
                return False, f"artifact-mismatch:{artifact.name}"
        for artifact in profile.executor_artifacts:
            if not artifact.path.is_file():
                return False, f"executor-source-missing:{artifact.name}"
            if not self._artifact_ready(
                artifact.path,
                artifact.bytes_expected,
                artifact.sha256,
            ):
                return False, f"executor-source-mismatch:{artifact.name}"
        return True, None

    def _profile_ready(self, profile: Profile) -> tuple[bool, str | None]:
        if self._profile_readiness is not None:
            return self._profile_readiness(profile)
        return self._default_profile_readiness(profile)

    def _set_active(self, attempt_id: str) -> threading.Event:
        event = threading.Event()
        with self._state_lock:
            self._active_attempt_id = attempt_id
            self._cancel_events[attempt_id] = event
        return event

    def _clear_active(self, attempt_id: str) -> None:
        with self._state_lock:
            if self._active_attempt_id == attempt_id:
                self._active_attempt_id = None
            self._cancel_events.pop(attempt_id, None)

    def _remember_cancelled(self, attempt_id: str) -> None:
        with self._state_lock:
            self._cancelled.pop(attempt_id, None)
            self._cancelled[attempt_id] = None
            while len(self._cancelled) > MAX_CANCELLED_CACHE:
                self._cancelled.popitem(last=False)
            event = self._cancel_events.get(attempt_id)
            if event is not None:
                event.set()

    def _was_cancelled(self, attempt_id: str) -> bool:
        with self._state_lock:
            return attempt_id in self._cancelled

    def _active(self) -> str | None:
        with self._state_lock:
            return self._active_attempt_id

    @staticmethod
    def _queue_rows(queue: dict[str, Any]) -> list[tuple[str, list[Any]]]:
        rows: list[tuple[str, list[Any]]] = []
        for key, state in (
            ("queue_running", "running"),
            ("queue_pending", "queued"),
        ):
            value = queue.get(key)
            if not isinstance(value, list):
                raise BridgeError(
                    "comfy-protocol-error",
                    f"Comfy {key} is not a list",
                )
            for item in value:
                if not isinstance(item, list) or len(item) < 4:
                    raise BridgeError(
                        "comfy-protocol-error",
                        "Comfy queue item has an invalid shape",
                    )
                rows.append((state, item))
        return rows

    def _state_payload(
        self,
        attempt_id: str,
        status: str,
        *,
        profile_id: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> dict[str, Any]:
        return {
            "ok": True,
            "protocol": PROTOCOL_VERSION,
            "action": "get_attempt",
            "kind": "state",
            "attempt_id": attempt_id,
            "prompt_id": attempt_id,
            "status": status,
            "profile_id": profile_id,
            "error_code": error_code,
            "error_message": error_message,
        }

    def _profile_from_metadata(
        self,
        attempt_id: str,
        metadata: dict[str, Any] | None,
    ) -> tuple[Profile, str] | None:
        if metadata is None or metadata.get("attempt_id") != attempt_id:
            return None
        profile_id = metadata.get("profile_id")
        if not isinstance(profile_id, str):
            return None
        profile = self.config.profiles.get(profile_id)
        if profile is None:
            return None
        if metadata.get("workflow_sha256") != profile.workflow_sha256:
            raise BridgeError(
                "provenance-mismatch",
                "Comfy history workflow digest does not match the loaded profile",
            )
        if metadata.get("profile_provenance_sha256") != profile.provenance_sha256():
            raise BridgeError(
                "provenance-mismatch",
                "Comfy history profile provenance does not match the loaded profile",
            )
        image_sha256 = metadata.get("image_sha256")
        if (
            not isinstance(image_sha256, str)
            or len(image_sha256) != 64
            or any(character not in "0123456789abcdef" for character in image_sha256)
        ):
            raise BridgeError(
                "provenance-mismatch",
                "Comfy history image digest is invalid",
            )
        return profile, image_sha256

    @staticmethod
    def _single_output_value(value: object) -> object:
        if isinstance(value, list):
            if len(value) != 1:
                raise BridgeError(
                    "output-invalid",
                    "annotation output must contain exactly one item",
                )
            return value[0]
        return value

    def _decode_output(self, profile: Profile, entry: dict[str, Any]) -> Any:
        outputs = entry.get("outputs")
        if not isinstance(outputs, dict):
            raise BridgeError("comfy-protocol-error", "history outputs are invalid")
        node_output = outputs.get(profile.output_node_id)
        if not isinstance(node_output, dict) or profile.output_key not in node_output:
            raise KeyError(profile.output_key)
        value = self._single_output_value(node_output[profile.output_key])
        if profile.result_kind == "image":
            return self._decode_image_output(value)
        if not isinstance(value, str):
            raise BridgeError("output-invalid", "annotation output must be text")
        if len(value.encode("utf-8")) > MAX_RESULT_TEXT_BYTES:
            raise BridgeError("output-limit-exceeded", "annotation output is too large")
        if profile.result_kind == "text":
            return value
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError as error:
            raise BridgeError(
                "output-invalid",
                "structured annotation output is not valid JSON",
            ) from error
        if not isinstance(decoded, (dict, list)):
            raise BridgeError(
                "output-invalid",
                "structured annotation output must be an object or array",
            )
        return decoded

    def _decode_image_output(self, value: object) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise BridgeError("output-invalid", "image output descriptor is invalid")
        filename = value.get("filename")
        subfolder = value.get("subfolder", "")
        if (
            not isinstance(filename, str)
            or not filename
            or Path(filename).name != filename
            or not isinstance(subfolder, str)
            or value.get("type") != "output"
        ):
            raise BridgeError("output-invalid", "image output identity is invalid")
        output_directory = self.config.output_directory
        if output_directory is None:
            raise BridgeError("configuration-error", "output directory is unavailable")
        relative_subfolder = Path(subfolder)
        if relative_subfolder.is_absolute() or ".." in relative_subfolder.parts:
            raise BridgeError("output-invalid", "image output escaped its directory")
        candidate = output_directory.joinpath(relative_subfolder, filename)
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(output_directory)
            metadata = resolved.stat()
            if not resolved.is_file() or metadata.st_size <= 0:
                raise BridgeError("output-invalid", "image output is not a file")
            if metadata.st_size > MAX_RESULT_IMAGE_BYTES:
                raise BridgeError("output-limit-exceeded", "image output is too large")
            data = resolved.read_bytes()
            with Image.open(io.BytesIO(data)) as opened:
                opened.verify()
            with Image.open(io.BytesIO(data)) as opened:
                if opened.format not in {"PNG", "JPEG"}:
                    raise BridgeError(
                        "output-invalid", "image output must be PNG or JPEG"
                    )
                width, height = opened.size
                media_type = "image/png" if opened.format == "PNG" else "image/jpeg"
        except BridgeError:
            raise
        except (OSError, ValueError, Image.DecompressionBombError) as error:
            raise BridgeError("output-invalid", "image output could not be read") from error
        return {
            "image": base64.b64encode(data).decode("ascii"),
            "media_type": media_type,
            "sha256": hashlib.sha256(data).hexdigest(),
            "width": width,
            "height": height,
        }

    def _result_payload(
        self,
        attempt_id: str,
        profile: Profile,
        image_sha256: str,
        raw_result: Any,
    ) -> dict[str, Any]:
        return {
            "ok": True,
            "protocol": PROTOCOL_VERSION,
            "action": "get_attempt",
            "kind": "result",
            "attempt_id": attempt_id,
            "prompt_id": attempt_id,
            "profile_id": profile.profile_id,
            "image_sha256": image_sha256,
            "workflow_sha256": profile.workflow_sha256,
            "profile_provenance": profile.provenance(),
            "raw_result": raw_result,
        }

    def _attempt_from_history(
        self,
        attempt_id: str,
        entry: dict[str, Any],
    ) -> dict[str, Any]:
        resolved = self._profile_from_metadata(
            attempt_id,
            _metadata_from_prompt(entry.get("prompt")),
        )
        if resolved is None:
            return self._state_payload(attempt_id, "unknown")
        profile, image_sha256 = resolved
        status_data = entry.get("status")
        status_string = status_data.get("status_str") if isinstance(status_data, dict) else None
        if status_string == "error":
            status = "cancelled" if self._was_cancelled(attempt_id) else "failed"
            return self._state_payload(
                attempt_id,
                status,
                profile_id=profile.profile_id,
                error_code=("cancelled" if status == "cancelled" else "execution-failed"),
                error_message=(
                    "annotation was cancelled"
                    if status == "cancelled"
                    else "Comfy execution failed"
                ),
            )
        try:
            output = self._decode_output(profile, entry)
        except KeyError:
            return self._state_payload(
                attempt_id,
                "output-pending",
                profile_id=profile.profile_id,
            )
        return self._result_payload(attempt_id, profile, image_sha256, output)

    def get_attempt(self, attempt_id: str) -> dict[str, Any]:
        history = self.http.history(attempt_id)
        entry = history.get(attempt_id)
        if entry is not None:
            if not isinstance(entry, dict):
                raise BridgeError(
                    "comfy-protocol-error",
                    "Comfy history entry has an invalid shape",
                )
            return self._attempt_from_history(attempt_id, entry)
        queue = self.http.queue()
        for status, item in self._queue_rows(queue):
            if item[1] != attempt_id:
                continue
            metadata = _metadata_from_prompt(item)
            resolved = self._profile_from_metadata(attempt_id, metadata)
            if resolved is None:
                return self._state_payload(attempt_id, "unknown")
            profile, _ = resolved
            return self._state_payload(
                attempt_id,
                status,
                profile_id=profile.profile_id,
            )
        if self._was_cancelled(attempt_id):
            return self._state_payload(attempt_id, "cancelled")
        return self._state_payload(attempt_id, "unknown")

    def ack_attempt(self, attempt_id: str) -> dict[str, Any]:
        """Delete one proven terminal Omoide history entry after durable commit."""

        history = self.http.history(attempt_id)
        if attempt_id not in history:
            if self._active() == attempt_id:
                raise BridgeError(
                    "ack-pending",
                    "Omoide is still processing this exact annotation attempt",
                    retryable=True,
                )
            queue = self.http.queue()
            if any(item[1] == attempt_id for _, item in self._queue_rows(queue)):
                raise BridgeError(
                    "ack-pending",
                    "Comfy is still processing this exact annotation attempt",
                    retryable=True,
                )
            # Close the queue-to-history transition race. A worker can publish
            # terminal history and clear its active/queue state after our first
            # read; never issue a durable absent receipt without this recheck.
            history = self.http.history(attempt_id)
            if attempt_id not in history:
                return {
                    "ok": True,
                    "protocol": PROTOCOL_VERSION,
                    "action": "ack_attempt",
                    "attempt_id": attempt_id,
                    "status": "already-absent",
                }

        entry = history[attempt_id]
        if not isinstance(entry, dict):
            raise BridgeError(
                "comfy-protocol-error",
                "Comfy history entry has an invalid shape",
            )
        resolved = self._profile_from_metadata(
            attempt_id,
            _metadata_from_prompt(entry.get("prompt")),
        )
        if resolved is None:
            raise BridgeError(
                "ack-refused",
                "Comfy history is not a verified Omoide annotation attempt",
            )
        status_data = entry.get("status")
        status_string = (
            status_data.get("status_str")
            if isinstance(status_data, dict)
            else None
        )
        if status_string not in {"success", "error"}:
            raise BridgeError(
                "ack-refused",
                "Comfy history is not a verified terminal Omoide attempt",
            )

        self.http.delete_history(attempt_id)
        remaining = self.http.history(attempt_id)
        if attempt_id in remaining:
            raise BridgeError(
                "history-delete-unconfirmed",
                "Comfy history still contains the acknowledged attempt",
                retryable=True,
            )
        return {
            "ok": True,
            "protocol": PROTOCOL_VERSION,
            "action": "ack_attempt",
            "attempt_id": attempt_id,
            "status": "deleted",
        }

    def health(self) -> dict[str, Any]:
        ready_profiles: list[str] = []
        unavailable_profiles: dict[str, str] = {}
        try:
            self.http.system_stats()
            self._queue_rows(self.http.queue())
        except BridgeError:
            unavailable_profiles = {
                profile_id: "comfy-unavailable"
                for profile_id in sorted(self.config.profiles)
            }
        else:
            for profile_id, profile in sorted(self.config.profiles.items()):
                profile_ready, reason = self._profile_ready(profile)
                if profile_ready:
                    ready_profiles.append(profile_id)
                else:
                    unavailable_profiles[profile_id] = reason or "profile-unavailable"
        return {
            "ok": True,
            "protocol": PROTOCOL_VERSION,
            "action": "health",
            "ready": bool(ready_profiles),
            "profiles": ready_profiles,
            "configured_profiles": sorted(self.config.profiles),
            "profile_result_kinds": {
                profile_id: profile.result_kind
                for profile_id, profile in sorted(self.config.profiles.items())
            },
            "unavailable_profiles": unavailable_profiles,
            "active_attempt_id": self._active(),
            "comfy_url": self.config.comfy_base_url,
        }

    def _decode_request_image(self, request: dict[str, Any]) -> ValidatedImage:
        encoded = request.get("image")
        if not isinstance(encoded, str):
            raise BridgeError("protocol-error", "image must be base64 text")
        try:
            image = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as error:
            raise BridgeError("protocol-error", "image is not valid base64") from error
        media_type = request.get("media_type")
        image_sha256 = request.get("image_sha256")
        if not isinstance(media_type, str) or not isinstance(image_sha256, str):
            raise BridgeError(
                "protocol-error",
                "media_type and image_sha256 must be strings",
            )
        return validate_image(image, media_type, image_sha256)

    def _ensure_empty_queue(self) -> None:
        if self._queue_rows(self.http.queue()):
            raise BridgeError(
                "busy",
                "Comfy already has pending or running work",
                retryable=True,
            )

    def _verify_upload(self, response: dict[str, Any], staged_name: str) -> None:
        if (
            response.get("name") != staged_name
            or response.get("subfolder") != STAGING_SUBFOLDER
            or response.get("type") != "input"
        ):
            raise BridgeError(
                "comfy-protocol-error",
                "Comfy did not preserve the exact staged image identity",
            )

    def _cleanup_staged(self, staged_name: str) -> None:
        if Path(staged_name).name != staged_name:
            raise BridgeError("staging-cleanup-failed", "staged filename is invalid")
        base = self.config.staging_directory
        candidate = base / staged_name
        try:
            if candidate.resolve(strict=False).parent != base:
                raise BridgeError(
                    "staging-cleanup-failed",
                    "staged file escaped the configured directory",
                )
            candidate.unlink(missing_ok=True)
        except OSError as error:
            raise BridgeError(
                "staging-cleanup-failed",
                "staged image could not be removed",
            ) from error

    def _metadata(
        self,
        attempt_id: str,
        profile: Profile,
        image: ValidatedImage,
    ) -> dict[str, Any]:
        return {
            METADATA_KEY: {
                "protocol": PROTOCOL_VERSION,
                "attempt_id": attempt_id,
                "profile_id": profile.profile_id,
                "image_sha256": image.sha256,
                "workflow_sha256": profile.workflow_sha256,
                "profile_provenance_sha256": profile.provenance_sha256(),
                "image_width": image.width,
                "image_height": image.height,
            }
        }

    def _reconcile_ambiguous_submit(
        self,
        attempt_id: str,
        profile: Profile,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + profile.reconcile_seconds
        while True:
            try:
                state = self.get_attempt(attempt_id)
            except BridgeError as error:
                if error.code not in _TRANSIENT_READ_CODES and not error.retryable:
                    raise
                state = self._state_payload(attempt_id, "unknown")
            if state.get("kind") == "result" or state.get("status") != "unknown":
                return state
            if time.monotonic() >= deadline:
                raise BridgeError(
                    "submit-unknown",
                    "Comfy submission outcome is unknown; it was not resubmitted",
                    retryable=False,
                )
            time.sleep(profile.poll_interval_seconds)

    def _resolve_timeout(
        self,
        attempt_id: str,
        profile: Profile,
    ) -> dict[str, Any]:
        try:
            cancelled = self.http.cancel(attempt_id)
        except BridgeError as error:
            LOGGER.warning("Timed-out prompt %s could not be cancelled: %s", attempt_id, error)
            raise BridgeError(
                "job-state-unknown",
                "annotation timed out and exact cancellation could not be proven",
                retryable=False,
            ) from error
        if cancelled:
            self._remember_cancelled(attempt_id)
            raise BridgeError(
                "cancelled",
                "annotation timed out and the exact prompt was cancelled",
                retryable=True,
            )
        try:
            final_state = self.get_attempt(attempt_id)
        except BridgeError as error:
            raise BridgeError(
                "job-state-unknown",
                "annotation timed out and its final state could not be proven",
                retryable=False,
            ) from error
        if final_state.get("kind") == "result":
            final_state["action"] = "annotate"
            return final_state
        if final_state.get("status") == "failed":
            raise BridgeError("execution-failed", "Comfy execution failed")
        if final_state.get("status") == "cancelled":
            raise BridgeError("cancelled", "annotation was cancelled", retryable=True)
        raise BridgeError(
            "job-state-unknown",
            "annotation timed out without a provable terminal state",
            retryable=False,
        )

    def _poll_result(
        self,
        attempt_id: str,
        profile: Profile,
        cancel_event: threading.Event,
        initial_state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + profile.timeout_seconds
        unknown_since: float | None = None
        state = initial_state
        while True:
            if cancel_event.is_set():
                try:
                    self.http.cancel(attempt_id)
                except BridgeError:
                    pass
                raise BridgeError("cancelled", "annotation was cancelled")
            if state is None:
                try:
                    state = self.get_attempt(attempt_id)
                except BridgeError as error:
                    if error.code not in _TRANSIENT_READ_CODES and not error.retryable:
                        raise
                    now = time.monotonic()
                    if now >= deadline:
                        return self._resolve_timeout(attempt_id, profile)
                    time.sleep(profile.poll_interval_seconds)
                    continue
            if state.get("kind") == "result":
                state["action"] = "annotate"
                return state
            status = state.get("status")
            if status == "failed":
                raise BridgeError("execution-failed", "Comfy execution failed")
            if status == "cancelled" or (status == "unknown" and cancel_event.is_set()):
                raise BridgeError("cancelled", "annotation was cancelled")
            now = time.monotonic()
            if status == "unknown":
                unknown_since = unknown_since or now
                if now - unknown_since >= profile.reconcile_seconds:
                    raise BridgeError(
                        "job-lost",
                        "submitted prompt is no longer in Comfy queue or history",
                    )
            else:
                unknown_since = None
            if now >= deadline:
                return self._resolve_timeout(attempt_id, profile)
            time.sleep(profile.poll_interval_seconds)
            state = None

    def _execute_image_request(
        self,
        request: dict[str, Any],
        *,
        action: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        attempt_id = _canonical_attempt_id(request.get("attempt_id"))
        profile_id = request.get("profile_id")
        if not isinstance(profile_id, str) or profile_id not in self.config.profiles:
            raise BridgeError("unknown-profile", f"{action} profile is not allowlisted")
        profile = self.config.profiles[profile_id]
        if (action == "repair") != (profile.result_kind == "image"):
            raise BridgeError("unknown-profile", f"profile does not support {action}")
        profile_ready, reason = self._profile_ready(profile)
        if not profile_ready:
            raise BridgeError(
                "profile-unavailable",
                f"{action} profile is not ready: {reason or 'unknown'}",
                retryable=True,
            )
        image = self._decode_request_image(request)
        if not self._annotation_lock.acquire(blocking=False):
            raise BridgeError("busy", "another annotation is active", retryable=True)
        staged_name = f"{attempt_id}{image.extension}"
        cleanup_required = False
        cancel_event = self._set_active(attempt_id)
        try:
            existing = self.get_attempt(attempt_id)
            if existing.get("kind") == "result" or existing.get("status") != "unknown":
                raise BridgeError(
                    "attempt-exists",
                    "attempt UUID already exists in Comfy; use get_attempt",
                )
            self._ensure_empty_queue()
            if cancel_event.is_set():
                raise BridgeError("cancelled", "annotation was cancelled")
            staged_path = self.config.staging_directory / staged_name
            if staged_path.exists() or staged_path.is_symlink():
                raise BridgeError(
                    "staging-collision",
                    "attempt staging file already exists",
                )
            cleanup_required = True
            upload_response = self.http.upload_image(
                filename=staged_name,
                image=image.data,
                media_type=image.media_type,
                subfolder=STAGING_SUBFOLDER,
            )
            self._verify_upload(upload_response, staged_name)
            if cancel_event.is_set():
                raise BridgeError("cancelled", "annotation was cancelled")
            prompt = profile.make_prompt(staged_name, params)
            initial_state: dict[str, Any] | None = None
            try:
                submission = self.http.submit(
                    prompt_id=attempt_id,
                    prompt=prompt,
                    extra_data=self._metadata(attempt_id, profile, image),
                )
            except ComfyTransportError:
                initial_state = self._reconcile_ambiguous_submit(attempt_id, profile)
            else:
                if submission.get("prompt_id") != attempt_id:
                    initial_state = self._reconcile_ambiguous_submit(
                        attempt_id,
                        profile,
                    )
            if cancel_event.is_set():
                try:
                    self.http.cancel(attempt_id)
                except BridgeError:
                    pass
                raise BridgeError("cancelled", "annotation was cancelled")
            result = self._poll_result(
                attempt_id,
                profile,
                cancel_event,
                initial_state=initial_state,
            )
            result["action"] = action
            return result
        finally:
            try:
                if cleanup_required:
                    self._cleanup_staged(staged_name)
            finally:
                self._clear_active(attempt_id)
                self._annotation_lock.release()

    def annotate(self, request: dict[str, Any]) -> dict[str, Any]:
        return self._execute_image_request(request, action="annotate")

    def repair(self, request: dict[str, Any]) -> dict[str, Any]:
        raw_params = request.get("params")
        if raw_params is not None and not isinstance(raw_params, dict):
            raise BridgeError("protocol-error", "params must be an object")
        try:
            encoded = json.dumps(
                raw_params or {},
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        except (TypeError, ValueError) as error:
            raise BridgeError("protocol-error", "params must be JSON-compatible") from error
        if len(encoded) > MAX_PARAMS_BYTES:
            raise BridgeError("input-limit-exceeded", "params exceeds its byte limit")
        profile_id = request.get("profile_id")
        profile = self.config.profiles.get(profile_id) if isinstance(profile_id, str) else None
        params = raw_params if raw_params else None
        if params is not None and profile is not None and profile.input_json_node_id is None:
            raise BridgeError("invalid-params", "repair profile does not accept params")
        return self._execute_image_request(request, action="repair", params=params)

    def cancel(self, attempt_id: str) -> dict[str, Any]:
        if self._active() == attempt_id:
            self._remember_cancelled(attempt_id)
            status = "cancel-requested"
        else:
            cancelled = self.http.cancel(attempt_id)
            if cancelled:
                self._remember_cancelled(attempt_id)
                status = "cancel-requested"
            else:
                state = self.get_attempt(attempt_id)
                if state.get("kind") == "result":
                    status = "already-succeeded"
                else:
                    state_status = state.get("status")
                    status = {
                        "failed": "already-failed",
                        "cancelled": "already-cancelled",
                    }.get(str(state_status), "not-cancelled")
                    if state_status == "unknown":
                        # A cancellation can win the database transition before
                        # annotate reaches this process. Keep a bounded UUID
                        # tombstone so that later admission cannot submit it.
                        self._remember_cancelled(attempt_id)
                        status = "cancel-requested"
        return {
            "ok": True,
            "protocol": PROTOCOL_VERSION,
            "action": "cancel",
            "attempt_id": attempt_id,
            "status": status,
        }

    def handle(self, request: dict[str, Any]) -> dict[str, Any]:
        if request.get("protocol") != PROTOCOL_VERSION:
            raise BridgeError("protocol-error", "unsupported protocol version")
        action = request.get("action")
        if action == "health":
            _require_request_keys(
                request,
                allowed={"protocol", "action"},
                required={"protocol", "action"},
            )
            return self.health()
        if action == "get_attempt":
            _require_request_keys(
                request,
                allowed={"protocol", "action", "attempt_id"},
                required={"protocol", "action", "attempt_id"},
            )
            return self.get_attempt(_canonical_attempt_id(request.get("attempt_id")))
        if action == "cancel":
            _require_request_keys(
                request,
                allowed={"protocol", "action", "attempt_id"},
                required={"protocol", "action", "attempt_id"},
            )
            return self.cancel(_canonical_attempt_id(request.get("attempt_id")))
        if action == "ack_attempt":
            _require_request_keys(
                request,
                allowed={"protocol", "action", "attempt_id"},
                required={"protocol", "action", "attempt_id"},
            )
            return self.ack_attempt(
                _canonical_attempt_id(request.get("attempt_id"))
            )
        if action == "annotate":
            _require_request_keys(
                request,
                allowed={
                    "protocol",
                    "action",
                    "attempt_id",
                    "profile_id",
                    "image",
                    "media_type",
                    "image_sha256",
                },
                required={
                    "protocol",
                    "action",
                    "attempt_id",
                    "profile_id",
                    "image",
                    "media_type",
                    "image_sha256",
                },
            )
            return self.annotate(request)
        if action == "repair":
            _require_request_keys(
                request,
                allowed={
                    "protocol",
                    "action",
                    "attempt_id",
                    "profile_id",
                    "image",
                    "media_type",
                    "image_sha256",
                    "params",
                },
                required={
                    "protocol",
                    "action",
                    "attempt_id",
                    "profile_id",
                    "image",
                    "media_type",
                    "image_sha256",
                },
            )
            return self.repair(request)
        raise BridgeError("protocol-error", "unsupported action")


class BridgeUnixServer:
    """Small threaded AF_UNIX server with safe stale-socket handling."""

    def __init__(
        self,
        service: BridgeService,
        socket_path: Path,
        *,
        request_timeout_seconds: float = 10.0,
        max_workers: int = MAX_CONNECTION_WORKERS,
    ) -> None:
        if not socket_path.is_absolute():
            raise BridgeError("configuration-error", "socket path must be absolute")
        if len(os.fsencode(socket_path)) >= 100:
            raise BridgeError("configuration-error", "socket path is too long")
        if max_workers < 1 or max_workers > 32:
            raise BridgeError("configuration-error", "max_workers is outside its limit")
        self.service = service
        self.socket_path = socket_path
        self.request_timeout_seconds = request_timeout_seconds
        self._stop = threading.Event()
        self._server: socket.socket | None = None
        self._socket_identity: tuple[int, int] | None = None
        self._worker_slots = threading.BoundedSemaphore(max_workers)

    def _prepare_socket_path(self) -> None:
        self.socket_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            existing = self.socket_path.lstat()
        except FileNotFoundError:
            return
        if not stat.S_ISSOCK(existing.st_mode):
            raise BridgeError(
                "socket-path-blocked",
                "socket path already exists and is not a socket",
            )
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            probe.settimeout(0.2)
            probe.connect(str(self.socket_path))
        except (ConnectionRefusedError, FileNotFoundError):
            self.socket_path.unlink()
        except OSError as error:
            raise BridgeError(
                "socket-path-blocked",
                "existing socket could not be safely classified",
            ) from error
        else:
            raise BridgeError("socket-in-use", "bridge socket is already in use")
        finally:
            probe.close()

    def shutdown(self) -> None:
        self._stop.set()

    def _handle_connection(self, connection: socket.socket) -> None:
        try:
            with connection:
                connection.settimeout(self.request_timeout_seconds)
                try:
                    request = read_message(connection)
                    response = self.service.handle(request)
                except BridgeError as error:
                    response = error.as_response(PROTOCOL_VERSION)
                except Exception:  # noqa: BLE001 - process boundary
                    LOGGER.exception("Unexpected Comfy bridge request failure")
                    response = BridgeError(
                        "service-failed",
                        "bridge request failed unexpectedly",
                    ).as_response(PROTOCOL_VERSION)
                try:
                    write_message(connection, response)
                except (BridgeError, BrokenPipeError, ConnectionResetError, OSError):
                    LOGGER.warning("Bridge response could not be delivered")
        finally:
            self._worker_slots.release()

    def serve_forever(self) -> None:
        self._prepare_socket_path()
        previous_umask = os.umask(0o077)
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server = server
        try:
            server.bind(str(self.socket_path))
            os.chmod(self.socket_path, 0o600)
            identity = self.socket_path.stat()
            self._socket_identity = (identity.st_dev, identity.st_ino)
            server.listen(8)
            server.settimeout(0.25)
            while not self._stop.is_set():
                try:
                    connection, _ = server.accept()
                except socket.timeout:
                    continue
                if not self._worker_slots.acquire(blocking=False):
                    connection.close()
                    continue
                worker = threading.Thread(
                    target=self._handle_connection,
                    args=(connection,),
                    daemon=True,
                )
                worker.start()
        finally:
            os.umask(previous_umask)
            server.close()
            self._server = None
            try:
                current = self.socket_path.lstat()
            except FileNotFoundError:
                current = None
            if current is not None:
                identity = (current.st_dev, current.st_ino)
                if stat.S_ISSOCK(current.st_mode) and identity == self._socket_identity:
                    self.socket_path.unlink()


def install_signal_handlers(server: BridgeUnixServer) -> None:
    def stop(*_: object) -> None:
        server.shutdown()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
