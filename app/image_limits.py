"""Process-wide Pillow decoder limits derived from settings."""

from __future__ import annotations

from PIL import Image

from app.logger import logger


def apply_pillow_limits(max_image_pixels: int | None) -> int | None:
    """Set ``Image.MAX_IMAGE_PIXELS`` from the scan setting.

    Pillow raises ``DecompressionBombError`` above twice the limit and warns
    above it. ``None`` (or a non-positive value) disables the guard. Returns
    the value applied so callers can log it.
    """
    value: int | None
    if max_image_pixels is None or max_image_pixels <= 0:
        value = None
    else:
        value = int(max_image_pixels)
    if Image.MAX_IMAGE_PIXELS != value:
        logger.info("Pillow MAX_IMAGE_PIXELS set to %s", value)
        Image.MAX_IMAGE_PIXELS = value
    return value
