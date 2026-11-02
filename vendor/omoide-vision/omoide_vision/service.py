"""Length-prefixed JSON Unix worker. Only this process imports GPU libraries."""
from __future__ import annotations

import argparse
import base64
import io
import json
import math
import os
import signal
import socket
import struct
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

PROTOCOL_VERSION = 'omoide-vision/v1'
MODEL_NAME = 'ViT-SO400M-16-SigLIP2-384'
MODEL_ID = 'immich-app/ViT-SO400M-16-SigLIP2-384__webli'
EMBED_DIM = 1152
MAX_REQUEST_BYTES = 64 * 1024 * 1024
MAX_IMAGE_BYTES = 16 * 1024 * 1024
MAX_ITEMS = 64


class WorkerError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def read_exact(connection, size):
    chunks = []
    while size:
        chunk = connection.recv(size)
        if not chunk:
            raise WorkerError('protocol-error', 'message ended before its declared length')
        chunks.append(chunk)
        size -= len(chunk)
    return b''.join(chunks)


def read_message(connection):
    size = struct.unpack('!I', read_exact(connection, 4))[0]
    if not 0 < size <= MAX_REQUEST_BYTES:
        raise WorkerError('input-limit-exceeded', 'request exceeds byte limit')
    try:
        payload = json.loads(read_exact(connection, size))
    except (ValueError, UnicodeError) as exc:
        raise WorkerError('protocol-error', 'invalid JSON') from exc
    if not isinstance(payload, dict):
        raise WorkerError('protocol-error', 'request must be an object')
    return payload


def write_message(connection, payload):
    encoded = json.dumps(payload, separators=(',', ':'), allow_nan=False).encode()
    if len(encoded) > MAX_REQUEST_BYTES:
        raise WorkerError('input-limit-exceeded', 'response exceeds byte limit')
    connection.sendall(struct.pack('!I', len(encoded)) + encoded)


MAX_PIXELS = 50_000_000


def _decode_rgb24(item):
    try:
        width, height = int(item['width']), int(item['height'])
        data = item['data']
    except (KeyError, TypeError, ValueError) as exc:
        raise WorkerError('protocol-error', 'rgb24 item needs width, height and data') from exc
    if not isinstance(data, str) or width <= 0 or height <= 0 or width * height > MAX_PIXELS:
        raise WorkerError('input-limit-exceeded', 'rgb24 dimensions are invalid or too large')
    try:
        raw = base64.b64decode(data, validate=True)
    except ValueError as exc:
        raise WorkerError('protocol-error', 'invalid base64') from exc
    if len(raw) != width * height * 3:
        raise WorkerError('protocol-error', 'rgb24 data does not match its dimensions')
    # The client already applied EXIF orientation; raw pixels carry none.
    return Image.frombytes('RGB', (width, height), raw)


def decode_image(encoded):
    if isinstance(encoded, dict):
        if encoded.get('format') != 'rgb24':
            raise WorkerError('protocol-error', 'unsupported raw image format')
        return _decode_rgb24(encoded)
    if not isinstance(encoded, str):
        raise WorkerError('protocol-error', 'image must be base64')
    if len(encoded) > ((MAX_IMAGE_BYTES + 2) // 3) * 4:
        raise WorkerError('input-limit-exceeded', 'image exceeds byte limit')
    try:
        raw = base64.b64decode(encoded, validate=True)
    except ValueError as exc:
        raise WorkerError('protocol-error', 'invalid base64') from exc
    if not raw or len(raw) > MAX_IMAGE_BYTES:
        raise WorkerError('input-limit-exceeded', 'image is empty or too large')
    try:
        with Image.open(io.BytesIO(raw)) as source:
            if source.format not in ('JPEG', 'PNG'):
                raise WorkerError('invalid-image', 'only JPEG and PNG are accepted')
            return ImageOps.exif_transpose(source).convert('RGB')
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise WorkerError('invalid-image', 'image cannot be decoded safely') from exc


def encode_vectors(features):
    vectors = np.asarray(features, dtype=np.float32)
    if vectors.ndim != 2 or not np.isfinite(vectors).all():
        raise WorkerError('inference-failed', 'invalid model vectors')
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    if not np.isfinite(norms).all() or (norms <= 1e-12).any():
        raise WorkerError('inference-failed', 'invalid model vector norms')
    vectors = (vectors / norms).astype('<f4')
    return [base64.b64encode(row.tobytes()).decode('ascii') for row in vectors]


# TOPIQ-NR was trained on KonIQ-10k (1024x768). Fixed input shapes matter on
# ROCm: every new shape triggers a MIOpen kernel search (5.9 s/img measured
# with variable shapes vs 28.7 ms/img at a fixed bucket on the R9700).
QUALITY_LANDSCAPE = (1024, 768)
QUALITY_PORTRAIT = (768, 1024)


def quality_batches(images):
    """Group images by orientation, resized to the KonIQ bucket, as float32 [0, 1] NCHW."""
    groups = {QUALITY_LANDSCAPE: [], QUALITY_PORTRAIT: []}
    for index, image in enumerate(images):
        size = QUALITY_LANDSCAPE if image.width >= image.height else QUALITY_PORTRAIT
        resized = image.convert('RGB').resize(size, Image.BICUBIC)
        groups[size].append((index, np.asarray(resized, dtype=np.float32).transpose(2, 0, 1) / 255.0))
    return [([i for i, _ in items], np.stack([p for _, p in items]))
            for items in groups.values() if items]


def _items(request, key):
    values = request.get(key)
    if not isinstance(values, list) or not 1 <= len(values) <= MAX_ITEMS:
        raise WorkerError('input-limit-exceeded', f'{key} must contain 1..{MAX_ITEMS} items')
    if key == 'images':
        # Images are base64 JPEG/PNG strings or raw {'format': 'rgb24', ...} items.
        if not all(isinstance(value, (str, dict)) for value in values):
            raise WorkerError('protocol-error', 'images must be base64 strings or rgb24 items')
    elif not all(isinstance(value, str) for value in values):
        raise WorkerError('protocol-error', f'{key} must be strings')
    return values


class VisionService:
    def __init__(self, arguments):
        import torch
        import open_clip

        torch.set_num_threads(8)
        self.torch = torch
        self.device = f'cuda:{arguments.device}'
        self.device_name = torch.cuda.get_device_name(arguments.device)
        self.batch_size = arguments.batch_size
        self.model, _, self.preprocess = open_clip.create_model_and_transforms(
            MODEL_NAME, pretrained='webli', device=self.device, precision='fp16'
        )
        self.model.eval()
        self.tokenizer = open_clip.get_tokenizer(MODEL_NAME)
        self.quality_model = None

    def embed_images(self, images):
        torch = self.torch
        outputs = []
        with torch.inference_mode():
            for start in range(0, len(images), self.batch_size):
                pixels = torch.stack([self.preprocess(image) for image in images[start:start + self.batch_size]])
                pixels = pixels.to(device=self.device, dtype=torch.float16)
                features = self.model.encode_image(pixels)
                outputs.append(features.float().cpu().numpy())
        return np.concatenate(outputs)

    def embed_texts(self, texts):
        outputs = []
        with self.torch.inference_mode():
            for start in range(0, len(texts), self.batch_size):
                tokens = self.tokenizer(texts[start:start + self.batch_size]).to(self.device)
                features = self.model.encode_text(tokens)
                outputs.append(features.float().cpu().numpy())
        return np.concatenate(outputs)

    def score_images(self, images):
        if self.quality_model is None:
            import pyiqa
            self.quality_model = pyiqa.create_metric('topiq_nr', device=self.device)
            self.quality_model.eval()
        scores = [0.0] * len(images)
        # TOPIQ takes original RGB [0, 1] pixels, not CLIP-normalized pixels.
        with self.torch.inference_mode():
            for indices, pixels in quality_batches(images):
                for start in range(0, len(indices), self.batch_size):
                    chunk = self.torch.from_numpy(pixels[start:start + self.batch_size]).to(self.device)
                    values = self.quality_model(chunk).reshape(-1).tolist()
                    for index, value in zip(indices[start:start + self.batch_size], values):
                        value = float(value)
                        if not math.isfinite(value):
                            raise WorkerError('inference-failed', 'nonfinite quality score')
                        scores[index] = value
        return scores

    def handle(self, request):
        if request.get('protocol') != PROTOCOL_VERSION:
            raise WorkerError('protocol-error', 'unsupported protocol version')
        action = request.get('action')
        result = {'ok': True, 'protocol': PROTOCOL_VERSION, 'model_id': MODEL_ID}
        if action == 'health':
            return {**result, 'models': [MODEL_ID] + (['topiq_nr'] if self.quality_model is not None else []),
                    'quality_model': 'topiq_nr', 'quality_loaded': self.quality_model is not None,
                    'device': self.device, 'device_name': self.device_name, 'dtype': 'float16', 'embed_dim': EMBED_DIM}
        if action == 'embed_texts':
            vectors = self.embed_texts(_items(request, 'texts'))
            return {**result, 'vectors': encode_vectors(vectors)}
        if action not in ('embed_images', 'quality'):
            raise WorkerError('protocol-error', 'unsupported action')
        images = [decode_image(value) for value in _items(request, 'images')]
        if action == 'embed_images':
            return {**result, 'vectors': encode_vectors(self.embed_images(images))}
        return {**result, 'scores': self.score_images(images), 'quality_model': 'topiq_nr'}


def _positive_int(value):
    result = int(value)
    if not 1 <= result <= MAX_ITEMS:
        raise argparse.ArgumentTypeError('batch size must be 1..64')
    return result


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--socket', type=Path, required=True)
    parser.add_argument('--device', type=int, default=0)
    parser.add_argument('--batch-size', type=_positive_int, default=16)
    parser.add_argument('--timeout', type=float, default=180.0)
    return parser


def serve(arguments):
    service = VisionService(arguments)
    path = arguments.socket
    path.parent.mkdir(parents=True, exist_ok=True)
    # Refuse to steal another active service's socket or remove a regular file.
    if path.exists() or path.is_socket():
        if not path.is_socket():
            raise RuntimeError('socket path already exists and is not a socket')
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
            probe.settimeout(1)
            try:
                probe.connect(str(path))
            except ConnectionRefusedError:
                path.unlink()
            else:
                raise RuntimeError('vision socket is already in use')
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(SystemExit(0)))
    bound = False
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        try:
            server.bind(str(path)); bound = True
            os.chmod(path, 0o600)
            server.listen(8)
            while True:
                connection, _ = server.accept()
                with connection:
                    connection.settimeout(arguments.timeout)
                    try:
                        response = service.handle(read_message(connection))
                    except WorkerError as exc:
                        response = {'ok': False, 'protocol': PROTOCOL_VERSION,
                                    'error': {'code': exc.code, 'message': exc.message}}
                    except Exception:
                        response = {'ok': False, 'protocol': PROTOCOL_VERSION,
                                    'error': {'code': 'service-failed', 'message': 'vision inference failed'}}
                    try:
                        write_message(connection, response)
                    except (OSError, WorkerError):
                        # A timed-out client must not bring down the worker.
                        pass
        finally:
            if bound:
                path.unlink(missing_ok=True)


def main():
    arguments = build_parser().parse_args()
    if arguments.timeout <= 0 or not math.isfinite(arguments.timeout) or arguments.device < 0:
        raise SystemExit('device must be nonnegative and timeout must be positive and finite')
    serve(arguments)


if __name__ == '__main__':
    main()
