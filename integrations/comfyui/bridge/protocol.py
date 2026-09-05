"""Bounded length-prefixed JSON framing shared by bridge connections."""

from __future__ import annotations

import json
import socket
import struct
from typing import Any

from .errors import BridgeError

PROTOCOL_VERSION = "omoide-comfy/v1"
MAX_REQUEST_BYTES = 32 * 1024 * 1024
MAX_RESPONSE_BYTES = 8 * 1024 * 1024


def read_exact(connection: socket.socket, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = connection.recv(remaining)
        if not chunk:
            raise BridgeError(
                "protocol-error",
                "message ended before its declared length",
            )
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def read_message(connection: socket.socket) -> dict[str, Any]:
    size = struct.unpack("!I", read_exact(connection, 4))[0]
    if size == 0 or size > MAX_REQUEST_BYTES:
        raise BridgeError(
            "input-limit-exceeded",
            f"request size {size} is not allowed",
        )
    try:
        payload = json.loads(read_exact(connection, size))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BridgeError(
            "protocol-error",
            "request is not valid JSON",
        ) from error
    if not isinstance(payload, dict):
        raise BridgeError("protocol-error", "request JSON must be an object")
    return payload


def write_message(connection: socket.socket, payload: dict[str, Any]) -> None:
    try:
        encoded = json.dumps(
            payload,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise BridgeError(
            "protocol-error",
            "response could not be encoded",
        ) from error
    if not encoded or len(encoded) > MAX_RESPONSE_BYTES:
        raise BridgeError(
            "output-limit-exceeded",
            "response exceeds the bridge output limit",
        )
    connection.sendall(struct.pack("!I", len(encoded)) + encoded)
