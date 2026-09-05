"""Small, dependency-free validation for client-normalized JPEG and PNG data."""

from __future__ import annotations

import hashlib
import struct
import zlib
from dataclasses import dataclass

from .errors import BridgeError

MAX_IMAGE_BYTES = 16 * 1024 * 1024
MAX_IMAGE_DIMENSION = 16_384
MAX_IMAGE_PIXELS = 40_000_000
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
JPEG_SOF_MARKERS = frozenset(
    {
        0xC0,
        0xC1,
        0xC2,
        0xC3,
        0xC5,
        0xC6,
        0xC7,
        0xC9,
        0xCA,
        0xCB,
        0xCD,
        0xCE,
        0xCF,
    }
)


@dataclass(frozen=True)
class ValidatedImage:
    data: bytes
    media_type: str
    extension: str
    sha256: str
    width: int
    height: int


def _validate_dimensions(width: int, height: int) -> None:
    if width <= 0 or height <= 0:
        raise BridgeError("invalid-image", "image dimensions must be positive")
    if width > MAX_IMAGE_DIMENSION or height > MAX_IMAGE_DIMENSION:
        raise BridgeError("input-limit-exceeded", "image dimensions are too large")
    if width * height > MAX_IMAGE_PIXELS:
        raise BridgeError("input-limit-exceeded", "image pixel count is too large")


def _png_dimensions(data: bytes) -> tuple[int, int]:
    if len(data) < 33 or not data.startswith(PNG_SIGNATURE):
        raise BridgeError("invalid-image", "PNG signature or header is invalid")
    length = struct.unpack("!I", data[8:12])[0]
    if length != 13 or data[12:16] != b"IHDR":
        raise BridgeError("invalid-image", "PNG does not begin with an IHDR chunk")
    expected_crc = struct.unpack("!I", data[29:33])[0]
    actual_crc = zlib.crc32(data[12:29]) & 0xFFFFFFFF
    if expected_crc != actual_crc:
        raise BridgeError("invalid-image", "PNG IHDR checksum is invalid")
    width, height = struct.unpack("!II", data[16:24])
    return width, height


def _jpeg_dimensions(data: bytes) -> tuple[int, int]:
    if len(data) < 4 or data[:2] != b"\xff\xd8":
        raise BridgeError("invalid-image", "JPEG signature is invalid")
    position = 2
    while position < len(data):
        if data[position] != 0xFF:
            raise BridgeError("invalid-image", "JPEG marker stream is invalid")
        while position < len(data) and data[position] == 0xFF:
            position += 1
        if position >= len(data):
            break
        marker = data[position]
        position += 1
        if marker in {0x01, 0xD8, 0xD9} or 0xD0 <= marker <= 0xD7:
            continue
        if position + 2 > len(data):
            break
        segment_length = struct.unpack("!H", data[position : position + 2])[0]
        if segment_length < 2 or position + segment_length > len(data):
            raise BridgeError("invalid-image", "JPEG segment length is invalid")
        if marker in JPEG_SOF_MARKERS:
            if segment_length < 7:
                raise BridgeError("invalid-image", "JPEG frame header is truncated")
            height, width = struct.unpack(
                "!HH",
                data[position + 3 : position + 7],
            )
            return width, height
        if marker == 0xDA:
            break
        position += segment_length
    raise BridgeError("invalid-image", "JPEG frame dimensions were not found")


def validate_image(
    data: bytes,
    media_type: str,
    declared_sha256: str,
) -> ValidatedImage:
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise BridgeError(
            "input-limit-exceeded",
            "image is empty or exceeds the encoded byte limit",
        )
    actual_sha256 = hashlib.sha256(data).hexdigest()
    if declared_sha256 != actual_sha256:
        raise BridgeError("digest-mismatch", "image SHA-256 does not match its bytes")
    if media_type == "image/png":
        width, height = _png_dimensions(data)
        extension = ".png"
    elif media_type == "image/jpeg":
        width, height = _jpeg_dimensions(data)
        extension = ".jpg"
    else:
        raise BridgeError(
            "invalid-image-type",
            "bridge accepts only normalized JPEG or PNG images",
        )
    _validate_dimensions(width, height)
    return ValidatedImage(
        data=data,
        media_type=media_type,
        extension=extension,
        sha256=actual_sha256,
        width=width,
        height=height,
    )
