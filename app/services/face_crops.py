from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Literal

from app.schemas.media import CropEditOp


Framing = Literal["closeup", "portrait", "half_body", "full_body"]
CropAspect = Literal["1:1", "2:3", "3:4", "4:5", "9:16", "free"]

_FRAMING_EXTENTS: dict[Framing, tuple[float, float]] = {
    "closeup": (1.3, 2.2),
    "portrait": (2.2, 4.5),
    "half_body": (3.0, 8.0),
    "full_body": (4.0, 14.0),
}
_ASPECTS: dict[CropAspect, tuple[int, int] | None] = {
    "1:1": (1, 1),
    "2:3": (2, 3),
    "3:4": (3, 4),
    "4:5": (4, 5),
    "9:16": (9, 16),
    "free": None,
}
DEFAULT_BUCKETS = (512, 768, 1024)


def bbox_to_source_pixels(
    bbox: Sequence[float], width: int, height: int
) -> tuple[int, int, int, int]:
    """Convert a detector-space ``[x, y, w, h]`` box to source pixels."""
    if len(bbox) != 4:
        raise ValueError("Face bbox must contain x, y, width and height")
    if width <= 0 or height <= 0:
        raise ValueError("Image dimensions must be positive")
    longest = max(width, height)
    scale = longest / min(longest, 1280)
    x, y, face_width, face_height = (float(value) * scale for value in bbox)
    return (
        max(0, round(x)),
        max(0, round(y)),
        max(1, round(face_width)),
        max(1, round(face_height)),
    )


def bucket_for(
    width: int, height: int, buckets: Sequence[int]
) -> dict[str, int]:
    """Return export dimensions for the largest bucket that does not upscale."""
    if width <= 0 or height <= 0:
        raise ValueError("Crop dimensions must be positive")
    longest = max(width, height)
    eligible = [int(bucket) for bucket in buckets if 0 < int(bucket) <= longest]
    target = max(eligible) if eligible else longest
    scale = target / longest
    return {
        "width": max(1, round(width * scale)),
        "height": max(1, round(height * scale)),
    }


def _aspect_size(
    desired_height: float,
    face_width: float,
    face_height: float,
    image_width: int,
    image_height: int,
    aspect: tuple[int, int],
) -> tuple[int, int]:
    aspect_width, aspect_height = aspect
    desired_units = desired_height / aspect_height
    minimum_units = max(face_width / aspect_width, face_height / aspect_height)
    maximum_units = min(image_width / aspect_width, image_height / aspect_height)
    units = min(max(desired_units, minimum_units), maximum_units)
    integer_units = max(1, math.floor(units))
    return aspect_width * integer_units, aspect_height * integer_units


def suggest_crop(
    face_px: Sequence[float],
    image_w: int,
    image_h: int,
    framing: Framing,
    aspect: CropAspect,
) -> tuple[CropEditOp, dict[str, int]]:
    """Build a face-anchored source-pixel crop and its default bucket size."""
    if framing not in _FRAMING_EXTENTS:
        raise ValueError(f"Unsupported framing: {framing}")
    if aspect not in _ASPECTS:
        raise ValueError(f"Unsupported aspect: {aspect}")
    if len(face_px) != 4 or image_w <= 0 or image_h <= 0:
        raise ValueError("Face and image dimensions must be positive")

    face_x, face_y, face_width, face_height = map(float, face_px)
    if face_width <= 0 or face_height <= 0:
        raise ValueError("Face dimensions must be positive")
    face_x = min(max(0.0, face_x), float(image_w - 1))
    face_y = min(max(0.0, face_y), float(image_h - 1))
    face_width = min(face_width, image_w - face_x)
    face_height = min(face_height, image_h - face_y)
    if face_width <= 0 or face_height <= 0:
        raise ValueError("Face lies outside the image")

    above, below = _FRAMING_EXTENTS[framing]
    desired_height = (above + 1.0 + below) * face_height
    requested_aspect = _ASPECTS[aspect]
    if requested_aspect is None:
        crop_height = min(image_h, max(1, round(desired_height)))
        crop_width = min(
            image_w,
            max(round(face_width), round(crop_height * image_w / image_h)),
        )
    else:
        crop_width, crop_height = _aspect_size(
            desired_height,
            face_width,
            face_height,
            image_w,
            image_h,
            requested_aspect,
        )

    face_center_x = face_x + face_width / 2
    crop_x = round(face_center_x - crop_width / 2)
    crop_y = round(face_y - above * face_height)
    crop_x = min(max(0, crop_x), image_w - crop_width)
    crop_y = min(max(0, crop_y), image_h - crop_height)

    # Integer rounding can put the far face edge a pixel outside a crop. Shift
    # where possible without changing the requested aspect.
    crop_x = min(crop_x, math.floor(face_x))
    crop_y = min(crop_y, math.floor(face_y))
    crop_x = max(crop_x, math.ceil(face_x + face_width - crop_width))
    crop_y = max(crop_y, math.ceil(face_y + face_height - crop_height))
    crop_x = min(max(0, crop_x), image_w - crop_width)
    crop_y = min(max(0, crop_y), image_h - crop_height)

    crop = CropEditOp(
        op="crop",
        x=int(crop_x),
        y=int(crop_y),
        width=int(crop_width),
        height=int(crop_height),
    )
    return crop, bucket_for(crop.width, crop.height, DEFAULT_BUCKETS)
