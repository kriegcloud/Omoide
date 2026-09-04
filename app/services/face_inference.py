"""Typed client boundary for the local AdaFace Unix-socket service."""

from __future__ import annotations

import base64
import json
import socket
import struct
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import cv2
import numpy as np

PROTOCOL_VERSION = "omoide-ml/v1"
FACE_MODEL_FINGERPRINT = (
    "adaface-kprpe:"
    "308142aa50adf2e187711354f7524635d3414f1e:"
    "99d16ed4aac0fdf0fcc82526b9b70703f3ec8c3041bf1bf44bd22751536e65db"
)
MAX_RESPONSE_BYTES = 24 * 1024 * 1024


class FaceInferenceError(RuntimeError):
    """Failure returned by or encountered while calling the face service."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _read_exact(connection: socket.socket, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = connection.recv(remaining)
        if not chunk:
            raise FaceInferenceError(
                "protocol-error", "response ended before its declared length"
            )
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


class AdaFaceSocketAnalysis:
    """InsightFace-compatible adapter backed by a local Unix socket."""

    def __init__(self, socket_path: Path, timeout_seconds: float = 180.0) -> None:
        self.socket_path = socket_path
        self.timeout_seconds = timeout_seconds

    def _request(self, payload: dict[str, Any]) -> dict[str, Any]:
        encoded = json.dumps(payload, separators=(",", ":")).encode()
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(self.timeout_seconds)
                connection.connect(str(self.socket_path))
                connection.sendall(struct.pack("!I", len(encoded)) + encoded)
                size = struct.unpack("!I", _read_exact(connection, 4))[0]
                if size == 0 or size > MAX_RESPONSE_BYTES:
                    raise FaceInferenceError(
                        "protocol-error", f"response size {size} is not allowed"
                    )
                response = json.loads(_read_exact(connection, size))
        except FaceInferenceError:
            raise
        except (OSError, ValueError, json.JSONDecodeError) as error:
            raise FaceInferenceError("service-unavailable", str(error)) from error
        if not isinstance(response, dict):
            raise FaceInferenceError(
                "protocol-error", "response JSON must be an object"
            )
        if response.get("ok") is not True:
            failure = response.get("error")
            if not isinstance(failure, dict):
                raise FaceInferenceError(
                    "service-failed", "service returned an invalid failure"
                )
            raise FaceInferenceError(
                str(failure.get("code", "service-failed")),
                str(failure.get("message", "face inference failed")),
            )
        if response.get("protocol") != PROTOCOL_VERSION:
            raise FaceInferenceError(
                "protocol-error", "service returned an unsupported protocol"
            )
        return response

    def health(self) -> dict[str, Any]:
        return self._request({"protocol": PROTOCOL_VERSION, "action": "health"})

    def get(self, image: np.ndarray) -> list[Any]:
        if not isinstance(image, np.ndarray) or image.size == 0:
            raise FaceInferenceError(
                "invalid-image", "face input must be a non-empty array"
            )
        # Omoide passes RGB arrays; OpenCV and the AdaFace service use BGR.
        bgr = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
        success, encoded_image = cv2.imencode(
            ".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, 95]
        )
        if not success:
            raise FaceInferenceError("invalid-image", "face input could not be encoded")
        response = self._request(
            {
                "protocol": PROTOCOL_VERSION,
                "action": "faces",
                "image": base64.b64encode(encoded_image).decode("ascii"),
            }
        )
        faces = response.get("faces")
        if not isinstance(faces, list):
            raise FaceInferenceError("protocol-error", "service response omitted faces")
        parsed: list[Any] = []
        for face in faces:
            if not isinstance(face, dict):
                raise FaceInferenceError(
                    "protocol-error", "face result must be an object"
                )
            embedding = face.get("embedding")
            parsed.append(
                SimpleNamespace(
                    bbox=np.asarray(face["bbox"], dtype=np.float32),
                    det_score=float(face["detScore"]),
                    kps=np.asarray(face["keypoints"], dtype=np.float32),
                    embedding=(
                        np.asarray(embedding, dtype=np.float32)
                        if embedding is not None
                        else None
                    ),
                    embedding_reason=face.get("embeddingReason"),
                    aligner_confidence=float(face.get("alignerConfidence", 0.0)),
                )
            )
        return parsed
