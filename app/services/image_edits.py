from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Literal

import piexif
from PIL import Image, ImageEnhance, ImageOps

from app.schemas.media import EditOp
from app.services.media_files import MediaRoot, require_writable_media_file


def apply_edit_ops(image: Image.Image, ops: list[EditOp]) -> Image.Image:
    """Apply normalized image edits in request order without mutating the input."""
    result = image.copy()
    rotations = {
        90: Image.Transpose.ROTATE_270,
        180: Image.Transpose.ROTATE_180,
        270: Image.Transpose.ROTATE_90,
    }
    for edit in ops:
        if edit.op == "rotate":
            result = result.transpose(rotations[edit.degrees])
        elif edit.op == "flip":
            result = ImageOps.mirror(result) if edit.axis == "horizontal" else ImageOps.flip(result)
        elif edit.op == "crop":
            right = edit.x + edit.width
            bottom = edit.y + edit.height
            if right > result.width or bottom > result.height:
                raise ValueError("Crop rectangle extends outside the image")
            result = result.crop((edit.x, edit.y, right, bottom))
        elif edit.op == "resize":
            result = result.resize((edit.width, edit.height), Image.Resampling.LANCZOS)
        elif edit.op == "adjust":
            if edit.brightness is not None:
                result = ImageEnhance.Brightness(result).enhance(1 + edit.brightness / 100)
            if edit.contrast is not None:
                result = ImageEnhance.Contrast(result).enhance(1 + edit.contrast / 100)
            if edit.saturation is not None:
                result = ImageEnhance.Color(result).enhance(1 + edit.saturation / 100)
    return result


def _copy_target(original: Path) -> Path:
    candidate = original.with_name(f"{original.stem}_edited{original.suffix}")
    index = 2
    while candidate.exists():
        candidate = original.with_name(f"{original.stem}_edited-{index}{original.suffix}")
        index += 1
    return candidate


def _jpeg_exif(original: Path, *, strip_orientation: bool) -> bytes | None:
    if original.suffix.lower() not in {".jpg", ".jpeg"}:
        return None
    try:
        exif = piexif.load(os.fspath(original))
    except (ValueError, piexif.InvalidImageDataError):
        return None
    if not any(exif.get(ifd) for ifd in ("0th", "Exif", "GPS", "1st")):
        return None
    if strip_orientation:
        exif["0th"].pop(piexif.ImageIFD.Orientation, None)
    exif.pop("thumbnail", None)
    return piexif.dump(exif)


def _save_image(image: Image.Image, target: Path, original: Path, *, rotated: bool) -> None:
    save_image = image
    kwargs: dict[str, object] = {}
    if target.suffix.lower() in {".jpg", ".jpeg"}:
        if save_image.mode not in {"RGB", "L", "CMYK"}:
            save_image = save_image.convert("RGB")
        exif = _jpeg_exif(original, strip_orientation=rotated)
        if exif:
            kwargs["exif"] = exif
        kwargs["quality"] = 95
    save_image.save(target, **kwargs)


def write_edited(
    original_path: str | Path,
    image: Image.Image,
    mode: Literal["copy", "overwrite"],
    *,
    media_roots: list[MediaRoot],
    rotated: bool = False,
) -> Path:
    """Write an edited image beside its writable source, atomically if replacing."""
    original = require_writable_media_file(original_path, media_roots)
    if mode == "copy":
        target = _copy_target(original)
        _save_image(image, target, original, rotated=rotated)
        return target
    if mode != "overwrite":
        raise ValueError(f"Unsupported edit mode: {mode}")

    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{original.stem}-edit-", suffix=original.suffix, dir=original.parent
    )
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        _save_image(image, temporary, original, rotated=rotated)
        os.replace(temporary, original)
    finally:
        temporary.unlink(missing_ok=True)
    return original
