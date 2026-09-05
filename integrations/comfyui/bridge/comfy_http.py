"""Bounded stdlib HTTP client for the loopback native ComfyUI API."""

from __future__ import annotations

import json
import socket
import uuid
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import (
    HTTPRedirectHandler,
    OpenerDirector,
    ProxyHandler,
    Request,
    build_opener,
)
from urllib.parse import quote

from .errors import BridgeError

MAX_HTTP_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_EXACT_HISTORY_RESPONSE_BYTES = 32 * 1024 * 1024


class ComfyTransportError(BridgeError):
    """A transport failure for which submit acceptance may be ambiguous."""

    def __init__(self, message: str) -> None:
        super().__init__("comfy-unavailable", message, retryable=True)


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, url):
        return None


class ComfyHttpClient:
    def __init__(self, base_url: str, timeout_seconds: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self._opener: OpenerDirector = build_opener(
            ProxyHandler({}),
            _RejectRedirects(),
        )

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
        timeout_seconds: float | None = None,
        max_response_bytes: int = MAX_HTTP_RESPONSE_BYTES,
    ) -> Any:
        request = Request(
            self.base_url + path,
            data=body,
            headers=headers or {},
            method=method,
        )
        try:
            with self._opener.open(
                request,
                timeout=timeout_seconds or self.timeout_seconds,
            ) as response:
                content_length = response.headers.get("Content-Length")
                if (
                    content_length is not None
                    and int(content_length) > max_response_bytes
                ):
                    raise BridgeError(
                        "comfy-protocol-error",
                        "Comfy response exceeds the allowed size",
                    )
                payload = response.read(max_response_bytes + 1)
        except HTTPError as error:
            raise BridgeError(
                "comfy-http-error",
                f"Comfy returned HTTP {error.code}",
                retryable=error.code >= 500,
            ) from error
        except (
            URLError,
            TimeoutError,
            socket.timeout,
            ConnectionError,
            OSError,
        ) as error:
            raise ComfyTransportError(
                "Comfy could not be reached on loopback"
            ) from error
        except ValueError as error:
            raise BridgeError(
                "comfy-protocol-error",
                "Comfy returned an invalid Content-Length",
            ) from error
        if len(payload) > max_response_bytes:
            raise BridgeError(
                "comfy-protocol-error",
                "Comfy response exceeds the allowed size",
            )
        try:
            return json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise BridgeError(
                "comfy-protocol-error",
                "Comfy response is not valid JSON",
            ) from error

    def _request_empty(
        self,
        method: str,
        path: str,
        *,
        body: bytes,
        headers: dict[str, str] | None = None,
    ) -> None:
        """Send a bounded request whose successful response has no JSON body."""

        request = Request(
            self.base_url + path,
            data=body,
            headers=headers or {},
            method=method,
        )
        try:
            with self._opener.open(
                request,
                timeout=self.timeout_seconds,
            ) as response:
                content_length = response.headers.get("Content-Length")
                if (
                    content_length is not None
                    and int(content_length) > MAX_HTTP_RESPONSE_BYTES
                ):
                    raise BridgeError(
                        "comfy-protocol-error",
                        "Comfy response exceeds the allowed size",
                    )
                payload = response.read(MAX_HTTP_RESPONSE_BYTES + 1)
        except HTTPError as error:
            raise BridgeError(
                "comfy-http-error",
                f"Comfy returned HTTP {error.code}",
                retryable=error.code >= 500,
            ) from error
        except (
            URLError,
            TimeoutError,
            socket.timeout,
            ConnectionError,
            OSError,
        ) as error:
            raise ComfyTransportError(
                "Comfy could not be reached on loopback"
            ) from error
        except ValueError as error:
            raise BridgeError(
                "comfy-protocol-error",
                "Comfy returned an invalid Content-Length",
            ) from error
        if len(payload) > MAX_HTTP_RESPONSE_BYTES:
            raise BridgeError(
                "comfy-protocol-error",
                "Comfy response exceeds the allowed size",
            )

    def system_stats(self) -> dict[str, Any]:
        result = self._request_json("GET", "/system_stats")
        if not isinstance(result, dict):
            raise BridgeError("comfy-protocol-error", "system_stats must be an object")
        return result

    def queue(self) -> dict[str, Any]:
        result = self._request_json("GET", "/queue")
        if not isinstance(result, dict):
            raise BridgeError("comfy-protocol-error", "queue must be an object")
        return result

    def history(self, prompt_id: str) -> dict[str, Any]:
        # Exact history can contain verbose terminal diagnostics that must be
        # provenance-verified before their one UUID is safely deleted. Keep a
        # separate finite cap without widening queue, prompt, upload, catalog,
        # or health responses.
        result = self._request_json(
            "GET",
            f"/history/{prompt_id}",
            max_response_bytes=MAX_EXACT_HISTORY_RESPONSE_BYTES,
        )
        if not isinstance(result, dict):
            raise BridgeError("comfy-protocol-error", "history must be an object")
        return result

    def delete_history(self, prompt_id: str) -> None:
        body = json.dumps(
            {"delete": [prompt_id]},
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        self._request_empty(
            "POST",
            "/history",
            body=body,
            headers={"Content-Type": "application/json"},
        )

    def object_info(self, node_class: str) -> dict[str, Any]:
        result = self._request_json(
            "GET",
            f"/object_info/{quote(node_class, safe='')}",
        )
        if not isinstance(result, dict):
            raise BridgeError("comfy-protocol-error", "object_info must be an object")
        return result

    def upload_image(
        self,
        *,
        filename: str,
        image: bytes,
        media_type: str,
        subfolder: str,
    ) -> dict[str, Any]:
        boundary = f"omoide-{uuid.uuid4().hex}"
        parts: list[bytes] = []

        def add_field(name: str, value: str) -> None:
            parts.extend(
                [
                    f"--{boundary}\r\n".encode("ascii"),
                    f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(
                        "ascii"
                    ),
                    value.encode("utf-8"),
                    b"\r\n",
                ]
            )

        parts.extend(
            [
                f"--{boundary}\r\n".encode("ascii"),
                (
                    'Content-Disposition: form-data; name="image"; '
                    f'filename="{filename}"\r\n'
                ).encode("ascii"),
                f"Content-Type: {media_type}\r\n\r\n".encode("ascii"),
                image,
                b"\r\n",
            ]
        )
        add_field("type", "input")
        add_field("subfolder", subfolder)
        add_field("overwrite", "false")
        parts.append(f"--{boundary}--\r\n".encode("ascii"))
        result = self._request_json(
            "POST",
            "/upload/image",
            body=b"".join(parts),
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        if not isinstance(result, dict):
            raise BridgeError("comfy-protocol-error", "upload result must be an object")
        return result

    def submit(
        self,
        *,
        prompt_id: str,
        prompt: dict[str, Any],
        extra_data: dict[str, Any],
    ) -> dict[str, Any]:
        body = json.dumps(
            {
                "prompt_id": prompt_id,
                "prompt": prompt,
                "extra_data": extra_data,
            },
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        try:
            result = self._request_json(
                "POST",
                "/prompt",
                body=body,
                headers={"Content-Type": "application/json"},
            )
            if not isinstance(result, dict):
                raise BridgeError(
                    "comfy-protocol-error",
                    "prompt result must be an object",
                )
            return result
        except ComfyTransportError:
            raise
        except BridgeError as error:
            if error.code == "comfy-http-error" and not error.retryable:
                raise
            raise ComfyTransportError(
                "Comfy submission response was non-definitive"
            ) from error

    def cancel(self, prompt_id: str) -> bool:
        result = self._request_json(
            "POST",
            f"/api/jobs/{prompt_id}/cancel",
            body=b"",
        )
        if not isinstance(result, dict) or not isinstance(result.get("cancelled"), bool):
            raise BridgeError("comfy-protocol-error", "cancel result is invalid")
        return result["cancelled"]
