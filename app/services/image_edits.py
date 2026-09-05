from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Literal

import piexif
from PIL import Image, ImageEnhance, ImageOps
from sqlmodel import Session, select

from app.config import settings
from app.database import safe_commit
from app.logger import logger
from app.models import Face, Media
from app.schemas.media import EditOp
from app.services.media_files import MediaRoot, require_writable_media_file
from app.utils import generate_perceptual_hash, generate_thumbnail


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


def write_repaired(
    original_path: str | Path,
    image: Image.Image,
    profile: str,
    *,
    media_roots: list[MediaRoot],
) -> Path:
    """Write a repair copy beside its source with a stable, collision-safe name."""
    original = require_writable_media_file(original_path, media_roots)
    short_profile = profile.removeprefix("omoide-").removesuffix("-v1")
    if not short_profile or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789-" for char in short_profile):
        raise ValueError("Invalid repair profile name")
    target = original.with_name(
        f"{original.stem}_repaired-{short_profile}{original.suffix}"
    )
    index = 2
    while target.exists():
        target = original.with_name(
            f"{original.stem}_repaired-{short_profile}-{index}{original.suffix}"
        )
        index += 1
    _save_image(image, target, original, rotated=True)
    return target


def edit_media_record(
    session: Session,
    media: Media,
    ops: list[EditOp],
    mode: Literal["copy", "overwrite"],
    *,
    design_state: dict | None = None,
) -> Media:
    """Apply the Phase 6 edit path and persist the resulting media record."""
    with Image.open(media.path) as opened:
        orientation = opened.getexif().get(0x0112, 1)
        source = ImageOps.exif_transpose(opened) if orientation not in (None, 1) else opened
        edited = apply_edit_ops(source, ops)
    target = write_edited(
        media.path,
        edited,
        mode,
        media_roots=settings.general.resolved_media_dirs(),
        rotated=any(op.op == "rotate" for op in ops) or orientation not in (None, 1),
    )

    if mode == "copy":
        result_media = Media(
            path=os.fspath(target),
            filename=target.name,
            size=target.stat().st_size,
            width=edited.width,
            height=edited.height,
            created_at=media.created_at,
            edit_design_state=design_state,
        )
        session.add(result_media)
        safe_commit(session)
        session.refresh(result_media)
        result_media.phash = generate_perceptual_hash(result_media, type="image")
        result_media.thumbnail_path, thumbnail_error = generate_thumbnail(result_media)
        if thumbnail_error:
            logger.warning(
                "Edited media %s thumbnail failed: %s",
                result_media.id,
                thumbnail_error,
            )
        session.add(result_media)
        safe_commit(session)
        return result_media

    from app.api.face import delete_faces

    old_thumbnail = (
        settings.general.thumb_dir / media.thumbnail_path
        if media.thumbnail_path
        else None
    )
    face_ids = session.exec(select(Face.id).where(Face.media_id == media.id)).all()
    if face_ids:
        delete_faces(face_ids=face_ids, session=session)
        refreshed = session.get(Media, media.id)
        if refreshed is None:
            raise ValueError("Media disappeared while resetting faces")
        media = refreshed

    media.size = target.stat().st_size
    media.width = edited.width
    media.height = edited.height
    media.phash = generate_perceptual_hash(media, type="image")
    media.faces_extracted = False
    media.embeddings_created = False
    media.ran_auto_tagging = False
    media.edit_design_state = design_state
    media.thumbnail_path, thumbnail_error = generate_thumbnail(media)
    if thumbnail_error:
        logger.warning("Edited media %s thumbnail failed: %s", media.id, thumbnail_error)
    new_thumbnail = (
        settings.general.thumb_dir / media.thumbnail_path
        if media.thumbnail_path
        else None
    )
    if old_thumbnail and old_thumbnail != new_thumbnail:
        old_thumbnail.unlink(missing_ok=True)
    session.add(media)
    safe_commit(session)
    return media
