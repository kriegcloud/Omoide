"""Local Unix-socket service for pinned AdaFace inference.

The service accepts encoded image bytes, never filesystem paths. This keeps the
GPU process independent from Omoide's media mounts while allowing the gallery
container to use the higher-quality recognizer.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import signal
import socket
import struct
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from .backends.adaface_kprpe import MODEL_NAME, load_backend
from .backends.base import WorkerArguments, WorkerError

PROTOCOL_VERSION = "omoide-ml/v1"
MAX_REQUEST_BYTES = 32 * 1024 * 1024
MAX_IMAGE_BYTES = 16 * 1024 * 1024
MAX_FACES = 32


def _read_exact(connection: socket.socket, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = connection.recv(remaining)
        if not chunk:
            raise WorkerError(
                "protocol-error", "request ended before its declared length"
            )
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _read_message(connection: socket.socket) -> dict[str, Any]:
    size = struct.unpack("!I", _read_exact(connection, 4))[0]
    if size == 0 or size > MAX_REQUEST_BYTES:
        raise WorkerError("input-limit-exceeded", f"request size {size} is not allowed")
    try:
        payload = json.loads(_read_exact(connection, size))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise WorkerError(
            "protocol-error", f"request is not valid JSON: {error}"
        ) from error
    if not isinstance(payload, dict):
        raise WorkerError("protocol-error", "request JSON must be an object")
    return payload


def _write_message(connection: socket.socket, payload: dict[str, Any]) -> None:
    encoded = json.dumps(payload, separators=(",", ":"), allow_nan=False).encode()
    connection.sendall(struct.pack("!I", len(encoded)) + encoded)


def _decode_image(payload: dict[str, Any]) -> np.ndarray:
    encoded = payload.get("image")
    if not isinstance(encoded, str):
        raise WorkerError("protocol-error", "image must be a base64 string")
    try:
        image_bytes = base64.b64decode(encoded, validate=True)
    except ValueError as error:
        raise WorkerError("protocol-error", "image is not valid base64") from error
    if not image_bytes or len(image_bytes) > MAX_IMAGE_BYTES:
        raise WorkerError("input-limit-exceeded", "encoded image is empty or too large")
    image = cv2.imdecode(np.frombuffer(image_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None or image.size == 0:
        raise WorkerError("invalid-image", "encoded image could not be decoded")
    return image


def _face_payload(face: Any) -> dict[str, Any]:
    embedding = getattr(face, "embedding", None)
    return {
        "bbox": np.asarray(face.bbox, dtype=np.float32).tolist(),
        "detScore": float(face.det_score),
        "keypoints": np.asarray(face.kps, dtype=np.float32).tolist(),
        "embedding": (
            np.asarray(embedding, dtype=np.float32).tolist()
            if embedding is not None
            else None
        ),
        "embeddingReason": getattr(face, "embedding_reason", None),
        "alignerConfidence": float(getattr(face, "aligner_confidence", 0.0)),
    }


class AdaFaceService:
    def __init__(self, arguments: argparse.Namespace) -> None:
        worker_arguments = WorkerArguments(
            source_dir=Path("."),
            reference_dir=Path("."),
            model_root=arguments.model_root,
            detection_threshold=arguments.detection_threshold,
            match_threshold=0.0,
            review_threshold=0.0,
            min_face_area_pct=0.0,
            recursive=False,
            accept_model_license=False,
            backend="adaface-kprpe",
            compute="rocm",
            devices=(arguments.device,),
            batch_size=arguments.batch_size,
            detector_path=arguments.detector,
            aligner_path=arguments.aligner,
            recognizer_path=arguments.recognizer,
        )
        self.backend = load_backend(
            worker_arguments,
            lambda _: (_ for _ in ()).throw(
                WorkerError("configuration-error", "detector fallback is disabled")
            ),
        )

    def handle(self, request: dict[str, Any]) -> dict[str, Any]:
        if request.get("protocol") != PROTOCOL_VERSION:
            raise WorkerError("protocol-error", "unsupported protocol version")
        action = request.get("action")
        if action == "health":
            return {
                "ok": True,
                "protocol": PROTOCOL_VERSION,
                "model": MODEL_NAME,
                "runtime": self.backend.model["runtime"],
            }
        if action != "faces":
            raise WorkerError("protocol-error", f"unsupported action: {action!r}")
        faces = self.backend.analysis.get(_decode_image(request))
        if len(faces) > MAX_FACES:
            raise WorkerError("input-limit-exceeded", "model returned too many faces")
        return {
            "ok": True,
            "protocol": PROTOCOL_VERSION,
            "model": MODEL_NAME,
            "faces": [_face_payload(face) for face in faces],
        }


def _serve(arguments: argparse.Namespace) -> None:
    service = AdaFaceService(arguments)
    socket_path = arguments.socket
    socket_path.parent.mkdir(parents=True, exist_ok=True)
    if socket_path.exists() or socket_path.is_socket():
        socket_path.unlink()
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(SystemExit(0)))
    try:
        server.bind(str(socket_path))
        os.chmod(socket_path, 0o600)
        server.listen(8)
        while True:
            connection, _ = server.accept()
            with connection:
                connection.settimeout(arguments.timeout)
                try:
                    response = service.handle(_read_message(connection))
                except WorkerError as error:
                    response = {
                        "ok": False,
                        "error": {"code": error.code, "message": error.message},
                    }
                except Exception as error:  # noqa: BLE001 - service boundary
                    response = {
                        "ok": False,
                        "error": {"code": "service-failed", "message": str(error)},
                    }
                _write_message(connection, response)
    finally:
        server.close()
        if socket_path.exists() or socket_path.is_socket():
            socket_path.unlink()


def main() -> None:
    parser = argparse.ArgumentParser(description="Pinned AdaFace service for Omoide")
    parser.add_argument("--socket", type=Path, required=True)
    parser.add_argument("--model-root", type=Path, required=True)
    parser.add_argument("--detector", type=Path, required=True)
    parser.add_argument("--aligner", type=Path, required=True)
    parser.add_argument("--recognizer", type=Path, required=True)
    parser.add_argument("--device", type=int, default=0)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--detection-threshold", type=float, default=0.5)
    parser.add_argument("--timeout", type=float, default=180.0)
    _serve(parser.parse_args())


if __name__ == "__main__":
    main()
