"""Bounded, synchronous Unix socket protocol for the isolated vision worker.

No GPU libraries are imported in the container. Model ids are Immich repository
ids, identifying both architecture and weights rather than just dimension.
"""
from __future__ import annotations

import base64
import io
import json
import math
import socket
import struct
import threading
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

PROTOCOL_VERSION = 'omoide-vision/v1'
MAX_REQUEST_BYTES = 64 * 1024 * 1024
MAX_ITEMS = 64
HEALTH_TTL = 5.0


class VisionError(RuntimeError):
    """Unavailable worker or an invalid/untrusted worker response."""


def read_exact(connection, size):
    chunks = []
    while size:
        chunk = connection.recv(size)
        if not chunk:
            raise VisionError('truncated message')
        chunks.append(chunk)
        size -= len(chunk)
    return b''.join(chunks)


def read_message(connection):
    size = struct.unpack('!I', read_exact(connection, 4))[0]
    if not 0 < size <= MAX_REQUEST_BYTES:
        raise VisionError('message exceeds byte limit')
    try:
        value = json.loads(read_exact(connection, size))
    except (ValueError, UnicodeError) as exc:
        raise VisionError('invalid JSON') from exc
    if not isinstance(value, dict):
        raise VisionError('message must be an object')
    return value


def write_message(connection, payload):
    encoded = json.dumps(payload, separators=(',', ':'), allow_nan=False).encode()
    if not 0 < len(encoded) <= MAX_REQUEST_BYTES:
        raise VisionError('message exceeds byte limit')
    connection.sendall(struct.pack('!I', len(encoded)) + encoded)


def encode_image(image):
    """Lossless raw RGB transport (PNG cost ~60 ms/img; raw ~3 ms)."""
    if not isinstance(image, Image.Image):
        image = Image.fromarray(image)
    image = ImageOps.exif_transpose(image).convert('RGB')
    return {
        'format': 'rgb24',
        'width': image.width,
        'height': image.height,
        'data': base64.b64encode(image.tobytes()).decode('ascii'),
    }


# Requests are split so the encoded payload stays well under MAX_REQUEST_BYTES
# (full-resolution PNG photos are several MB each).
MAX_BATCH_BYTES = 48 * 1024 * 1024


def _item_size(item):
    return len(item['data']) if isinstance(item, dict) else len(item)


def _byte_batches(encoded_items):
    """Split encoded items into (offset, items) batches bounded by bytes and count."""
    batch, size, start = [], 0, 0
    for index, item in enumerate(encoded_items):
        if batch and (size + _item_size(item) > MAX_BATCH_BYTES or len(batch) >= MAX_ITEMS):
            yield start, batch
            batch, size, start = [], 0, index
        batch.append(item)
        size += _item_size(item)
    if batch:
        yield start, batch


class VisionClient:
    def __init__(self, socket_path: str | Path, timeout: float = 60.0, enabled: bool = True):
        self.socket_path = str(socket_path)
        self.timeout = timeout
        self.enabled = enabled
        self._health = None
        self._expires = 0.0
        self._health_lock = threading.Lock()

    def request(self, action, **payload):
        if not self.enabled:
            raise VisionError('vision service disabled')
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(self.timeout)
                connection.connect(self.socket_path)
                write_message(connection, {'protocol': PROTOCOL_VERSION, 'action': action, **payload})
                response = read_message(connection)
            if response.get('ok') is not True or response.get('protocol') != PROTOCOL_VERSION:
                error = response.get('error') if isinstance(response.get('error'), dict) else {}
                raise VisionError(
                    f"worker rejected request: {error.get('code', 'unknown')}: {error.get('message', '')}"[:300]
                )
            return response
        except (OSError, ValueError) as exc:
            raise VisionError('vision socket request failed') from exc

    def invalidate(self):
        # Negative cache prevents repeatedly probing a failed worker per item.
        with self._health_lock:
            self._health = None
            self._expires = time.monotonic() + HEALTH_TTL

    def available(self, model_id, embed_dim):
        if not self.enabled:
            return False
        with self._health_lock:
            if time.monotonic() >= self._expires:
                try:
                    self._health = self.request('health')
                except VisionError:
                    self._health = None
                self._expires = time.monotonic() + HEALTH_TTL
            health = self._health or {}
            return (health.get('protocol') == PROTOCOL_VERSION
                    and health.get('model_id') == model_id
                    and health.get('embed_dim') == embed_dim)

    def _embed(self, action, values, key, model_id, dimension):
        batches = []
        encoded = [encode_image(i) for i in values] if key == 'images' else list(values)
        for _offset, payload in _byte_batches(encoded):
            chunk = payload
            response = self.request(action, **{key: payload})
            vectors = response.get('vectors')
            if response.get('model_id') != model_id or not isinstance(vectors, list) or len(vectors) != len(chunk):
                raise VisionError('incompatible vector response')
            rows = []
            for encoded in vectors:
                try:
                    raw = base64.b64decode(encoded, validate=True)
                    vector = np.frombuffer(raw, dtype='<f4')
                except (ValueError, TypeError) as exc:
                    raise VisionError('invalid vector encoding') from exc
                if vector.size != dimension or not np.isfinite(vector).all():
                    raise VisionError('invalid vector dimension or values')
                norm = np.linalg.norm(vector)
                if not np.isfinite(norm) or norm <= 1e-12 or abs(float(norm) - 1) > .01:
                    raise VisionError('worker vector is not normalized')
                rows.append(vector / norm)
            batches.append(np.stack(rows))
        return np.concatenate(batches) if batches else np.empty((0, dimension), dtype=np.float32)

    def embed_images(self, images, model_id, dimension):
        return self._embed('embed_images', images, 'images', model_id, dimension)

    def embed_texts(self, texts, model_id, dimension):
        return self._embed('embed_texts', texts, 'texts', model_id, dimension)

    def quality(self, images):
        scores = []
        for _offset, chunk in _byte_batches([encode_image(i) for i in images]):
            response = self.request('quality', images=chunk)
            values = response.get('scores')
            if not isinstance(values, list) or len(values) != len(chunk):
                raise VisionError('invalid quality score count')
            try:
                values = [float(v) for v in values]
            except (ValueError, TypeError) as exc:
                raise VisionError('invalid quality scores') from exc
            if not all(math.isfinite(v) for v in values):
                raise VisionError('nonfinite quality scores')
            scores.extend(values)
        return scores


_client = None
_client_lock = threading.Lock()


def get_vision_client():
    from app.config import settings
    global _client
    config = settings.vision
    with _client_lock:
        key = (str(config.socket_path), config.timeout, config.enabled)
        if _client is None or (_client.socket_path, _client.timeout, _client.enabled) != key:
            _client = VisionClient(*key)
        return _client
