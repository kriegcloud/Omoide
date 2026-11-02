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

PROTOCOL_VERSION = 'omoide-vision/v2'
MAX_REQUEST_BYTES = 64 * 1024 * 1024
# Detectors resize to 640-768 px internally; a 2048 px frame is ~16 MB as base64
# and keeps >16 MP photos under the frame limit.
DETECTION_MAX_EDGE = 2048
# LaMa sees the mask bounding box plus this much context (matches the worker).
INPAINT_CONTEXT = 192
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


def _rescale_detection(value, sx, sy):
    """Map worker boxes/quads from the sent frame back to source pixels."""
    if (sx, sy) == (1.0, 1.0) or not isinstance(value, dict):
        return value
    value = dict(value)
    if isinstance(value.get('boxes'), list):
        value['boxes'] = [
            [box[0] * sx, box[1] * sy, box[2] * sx, box[3] * sy, *box[4:]]
            if isinstance(box, list) and len(box) >= 4 else box
            for box in value['boxes']
        ]
    if isinstance(value.get('regions'), list):
        regions = []
        for region in value['regions']:
            quad = region.get('quad') if isinstance(region, dict) else None
            if isinstance(quad, list) and len(quad) == 8:
                region = {**region, 'quad': [v * (sx if i % 2 == 0 else sy) for i, v in enumerate(quad)]}
            regions.append(region)
        value['regions'] = regions
    return value


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


    def _detections(self, action, images):
        """Send detection frames at most DETECTION_MAX_EDGE; return source-pixel geometry."""
        frames, scales = [], []
        for image in images:
            if not isinstance(image, Image.Image):
                image = Image.fromarray(image)
            upright = ImageOps.exif_transpose(image).convert('RGB')
            sent = upright
            if max(upright.size) > DETECTION_MAX_EDGE:
                sent = upright.copy()
                sent.thumbnail((DETECTION_MAX_EDGE, DETECTION_MAX_EDGE), Image.Resampling.LANCZOS)
            frames.append(encode_image(sent))
            scales.append((upright.width / sent.width, upright.height / sent.height))
        results = []
        for offset, chunk in _byte_batches(frames):
            values = self.request(action, images=chunk).get('results')
            if not isinstance(values, list) or len(values) != len(chunk):
                raise VisionError('invalid detection result count')
            for index, value in enumerate(values):
                results.append(_rescale_detection(value, *scales[offset + index]))
        return results

    def detect_persons(self, images):
        results = self._detections('detect_persons', images)
        try:
            for result in results:
                score, boxes = result['score'], result['boxes']
                if not isinstance(boxes, list) or not 0 <= float(score) <= 1:
                    raise ValueError()
                for box in boxes:
                    if (len(box) != 5 or not all(math.isfinite(float(v)) for v in box)
                            or min(box[:4]) < 0 or not .35 <= box[4] <= 1):
                        raise ValueError()
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise VisionError('invalid person detections') from exc
        return results

    def detect_text(self, images):
        results = self._detections('detect_text', images)
        try:
            for result in results:
                if not isinstance(result['regions'], list):
                    raise ValueError()
                for region in result['regions']:
                    if (not isinstance(region['text'], str) or len(region['quad']) != 8
                            or not all(math.isfinite(float(v)) for v in region['quad'])):
                        raise ValueError()
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise VisionError('invalid text detections') from exc
        return results

    def inpaint(self, image, mask):
        """Return hard-composited RGB pixels, validating preservation independently.

        Only the mask's bounding box plus INPAINT_CONTEXT is sent, so large photos
        fit the frame limit; pixels outside that window are copied from the source.
        """
        if not isinstance(image, Image.Image):
            image = Image.fromarray(image)
        source = ImageOps.exif_transpose(image).convert('RGB')
        if isinstance(mask, bytes):
            try:
                with Image.open(io.BytesIO(mask)) as loaded:
                    if loaded.format != 'PNG':
                        raise ValueError('mask must be PNG')
                    mask = loaded.convert('L')
            except (OSError, ValueError) as exc:
                raise VisionError('invalid inpaint mask') from exc
        if not isinstance(mask, Image.Image) or mask.size != source.size:
            raise VisionError('inpaint mask dimensions must match image')
        mask = mask.convert('L')
        bbox = mask.point(lambda v: 255 if v else 0).getbbox()
        if bbox is None:
            return source.copy()
        left, top, right, bottom = bbox
        window = (max(0, left - INPAINT_CONTEXT), max(0, top - INPAINT_CONTEXT),
                  min(source.width, right + INPAINT_CONTEXT), min(source.height, bottom + INPAINT_CONTEXT))
        crop, crop_mask = source.crop(window), mask.crop(window)
        encoded = encode_image(crop)
        buffer = io.BytesIO()
        crop_mask.save(buffer, format='PNG')
        response = self.request('inpaint', image=encoded,
                                mask=base64.b64encode(buffer.getvalue()).decode('ascii'))
        try:
            result = response['image']
            raw = base64.b64decode(result['data'], validate=True)
            if (result['format'] != 'rgb24' or result['width'] != crop.width
                    or result['height'] != crop.height or len(raw) != crop.width * crop.height * 3):
                raise ValueError()
            patch = Image.frombytes('RGB', crop.size, raw)
            outside = np.asarray(crop_mask) == 0
            if not np.array_equal(np.asarray(patch)[outside], np.asarray(crop)[outside]):
                raise ValueError()
        except (KeyError, TypeError, ValueError) as exc:
            raise VisionError('invalid inpaint pixels or outside-mask mutation') from exc
        output = source.copy()
        output.paste(patch, window[:2])
        return output


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
