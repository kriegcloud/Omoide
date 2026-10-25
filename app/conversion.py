"""Making videos playable in the browser.

Browsers (and the desktop app's web view) only decode a few video codecs, so
e.g. HEVC phone videos play as audio over a black picture and AVI/WMV files not
at all. This module decides which files need converting and runs ffmpeg in one
of three ways:

* streamed on the fly while the video plays (nothing is written),
* into a playable copy in Omoide's data folder (the original is kept),
* replacing the original file with the converted MP4.
"""

from __future__ import annotations

import os
import errno
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Literal

from sqlmodel import Session

from app.accelerators import get_ffmpeg_accel_config
from app.config import settings
from app.ffmpeg import ensure_ffmpeg_available
from app.logger import logger
from app.media_files import _ffprobe_json
from app.models import Media
from app.subprocess_helpers import popen_silent

ConversionMode = Literal["keep_original", "replace_original"]

# What Chromium-based browsers decode without extra OS codecs.
_PLAYABLE_VIDEO_CODECS = {"h264", "vp8", "vp9", "av1"}
_PLAYABLE_AUDIO_CODECS = {"aac", "mp3", "opus", "vorbis", "flac"}
_PLAYABLE_CONTAINERS = {".mp4", ".m4v", ".mov", ".webm", ".mkv"}

# Shared by file conversion and streaming so both produce the same output.
_OUTPUT_ARGS = [
    "-map", "0:v:0", "-map", "0:a:0?",
    "-pix_fmt", "yuv420p",
    "-c:a", "aac",
]


class ConversionError(RuntimeError):
    pass


def is_browser_playable(path: Path) -> bool:
    """Best-effort check whether the browser can show this video as is."""
    if path.suffix.lower() not in _PLAYABLE_CONTAINERS:
        return False
    info = _ffprobe_json(path)
    if not info:
        return False
    streams = info.get("streams") or []
    video = [s for s in streams if s.get("codec_type") == "video"]
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    if not video or video[0].get("codec_name") not in _PLAYABLE_VIDEO_CODECS:
        return False
    return not audio or audio[0].get("codec_name") in _PLAYABLE_AUDIO_CODECS


def converted_copy_path(media_id: int) -> Path:
    """Where the playable copy of a video lives when the original is kept."""
    return settings.general.omoide_dir / "converted" / f"{media_id}.mp4"


def existing_converted_copy(media_id: int) -> Path | None:
    path = converted_copy_path(media_id)
    return path if path.is_file() else None


def _ffmpeg() -> Path:
    ffmpeg_bin = ensure_ffmpeg_available()
    if not ffmpeg_bin:
        raise ConversionError(
            "ffmpeg is required to convert videos but could not be located."
        )
    return ffmpeg_bin


def _duration_seconds(path: Path) -> float:
    info = _ffprobe_json(path) or {}
    try:
        return float((info.get("format") or {}).get("duration") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _stop_process(proc) -> None:
    """Bound termination and reap only the child owned by this conversion."""
    if proc.poll() is not None:
        return
    try:
        proc.terminate()
        try:
            proc.communicate(timeout=2.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate(timeout=2.0)
    except (OSError, subprocess.TimeoutExpired):
        logger.warning("Conversion child did not exit within the shutdown deadline")


def convert_file(
    source: Path, target: Path, *,
    on_progress: Callable[[int], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
    before_publish: Callable[[], None] | None = None,
) -> None:
    """Decode into exclusively owned files beside the target, then publish atomically.

    Progress uses a file so a stalled FFmpeg stdout cannot hide cancellation;
    stderr uses a file so verbose failures cannot fill a pipe and deadlock.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".omoide-convert-", suffix=".mp4", dir=target.parent)
    os.close(descriptor)
    partial = Path(name)
    descriptor, name = tempfile.mkstemp(prefix=".omoide-convert-", suffix=".progress", dir=target.parent)
    os.close(descriptor)
    progress = Path(name)
    errors = tempfile.TemporaryFile()
    proc = None
    try:
        if cancelled and cancelled():
            raise ConversionError("Conversion cancelled.")
        accel = get_ffmpeg_accel_config(settings.processors.prefer_gpu)
        duration_us = _duration_seconds(source) * 1_000_000
        cmd = [str(_ffmpeg()), "-nostdin", *accel.hwaccel_args, "-i", str(source),
               "-c:v", accel.video_encoder or "libx264", *_OUTPUT_ARGS,
               "-movflags", "use_metadata_tags+faststart", "-progress", str(progress),
               "-nostats", "-y", str(partial)]
        proc = popen_silent(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=errors, text=True)
        while True:
            if cancelled and cancelled():
                raise ConversionError("Conversion cancelled.")
            if on_progress and duration_us > 0:
                for line in progress.read_text(errors="replace").splitlines():
                    key, _, value = line.partition("=")
                    if key in {"out_time_us", "out_time_ms"} and value.isdigit():
                        on_progress(min(99, int(int(value) / duration_us * 100)))
            if proc.poll() is not None:
                break
            time.sleep(0.2)
        proc.communicate()
        if cancelled and cancelled():
            raise ConversionError("Conversion cancelled.")
        if proc.returncode != 0:
            errors.seek(0)
            tail = errors.read().decode("utf-8", "replace")[-2000:]
            raise ConversionError(f"ffmpeg failed (exit {proc.returncode}): {tail}")
        if not partial.is_file() or partial.stat().st_size == 0:
            raise ConversionError("FFmpeg produced no converted output.")
        if before_publish:
            before_publish()
        os.replace(partial, target)
    finally:
        if proc is not None:
            _stop_process(proc)
        errors.close()
        progress.unlink(missing_ok=True)
        partial.unlink(missing_ok=True)


def _available_name(path: Path) -> Path:
    """`path`, or `name (1).mp4` etc. if another file already uses it."""
    if not path.exists():
        return path
    index = 1
    while True:
        candidate = path.with_name(f"{path.stem} ({index}){path.suffix}")
        if not candidate.exists():
            return candidate
        index += 1


def _file_identity(path: Path) -> tuple[int, ...] | None:
    """Bind a pathname to its inode and detect in-place content/metadata edits."""
    try:
        stat = path.lstat()
    except FileNotFoundError:
        return None
    return (stat.st_dev, stat.st_ino, stat.st_mode, stat.st_size,
            stat.st_mtime_ns, stat.st_ctime_ns)


def publish_replacement(
    session: Session, media: Media, encoded: Path, destination: Path,
    *, commit: Callable[[], object] | None = None,
    expected_source_identity: tuple[int, ...] | None = None,
) -> Path:
    """Keep independent recovery versions until catalog commit and owned cleanup.

    Encoding callers supply the identity observed before reading the source, so
    publication rejects replacements and in-place edits made during encoding.
    Direct publication callers may bind the source at publication entry instead.
    Portable filesystems provide no compare-and-unlink/replace primitive: external
    writes between the last identity check and mutation remain a narrow TOCTOU
    limit. Recovery copies are independent inodes, so newer writes cannot mutate
    the retained original or encoded version.
    """
    source = Path(media.path)
    original = (media.path, media.filename, media.size)
    source_identity = (expected_source_identity if expected_source_identity is not None
                       else _file_identity(source))
    backup: Path | None = None
    swap: Path | None = None
    published = False
    published_identity = None
    try:
        if source_identity is None or _file_identity(source) != source_identity:
            raise ConversionError("Source identity changed during encoding")
        descriptor, name = tempfile.mkstemp(prefix=".omoide-original-", suffix=".backup", dir=source.parent)
        os.close(descriptor)
        backup = Path(name)
        shutil.copy2(source, backup)
        if source_identity is None or _file_identity(source) != source_identity:
            raise ConversionError("Source identity changed while retaining original")
        descriptor, name = tempfile.mkstemp(prefix=".omoide-publish-", suffix=".tmp", dir=source.parent)
        os.close(descriptor)
        swap = Path(name)
        shutil.copy2(encoded, swap)
        if _file_identity(source) != source_identity:
            raise ConversionError("Source identity changed before publication")
        swap_identity = _file_identity(swap)
        if destination == source:
            swap.replace(destination)
            published = True
            published_identity = swap_identity[:2]
        else:
            try:
                os.link(swap, destination)
                published = True
                published_identity = swap_identity[:2]
            except OSError as exc:
                if exc.errno == errno.EEXIST:
                    raise
                # Unsupported hard links: exclusive creation still refuses an
                # unrelated destination, with identity-bound partial cleanup.
                with destination.open("xb") as target, swap.open("rb") as data:
                    stat = os.fstat(target.fileno())
                    published_identity = (stat.st_dev, stat.st_ino)
                    published = True
                    shutil.copyfileobj(data, target)
                    target.flush()
                    os.fsync(target.fileno())
            try:
                swap.unlink()
            except OSError as exc:
                logger.warning("Conversion retained publication temporary %s: %s", swap, exc)
        published = True
        published_identity = _file_identity(destination)
        media.path = str(destination)
        media.filename = destination.name
        media.size = destination.stat().st_size
        session.add(media)
        if commit is None:
            session.commit()
        elif commit() is False:
            raise ConversionError("Conversion task is no longer running.")
    except Exception as exc:
        session.rollback()
        media.path, media.filename, media.size = original
        if published:
            try:
                identity = _file_identity(destination)
            except OSError as identity_exc:
                raise ConversionError(
                    f"Cannot verify conversion publication identity; original backup: {backup}; "
                    f"encoded recovery: {encoded}: {identity_exc}"
                ) from exc
            matches = (identity == published_identity if len(published_identity or ()) != 2
                       else identity is not None and identity[:2] == published_identity)
            if not matches:
                raise ConversionError(
                    f"Conversion identity conflict; newer file preserved: {destination}; "
                    f"original backup: {backup}; encoded recovery: {encoded}"
                ) from exc
            if destination == source:
                try:
                    backup.replace(source)
                except OSError as restore_exc:
                    raise ConversionError(
                        f"Original recovery backup: {backup}; encoded recovery: {encoded}; "
                        f"could not restore {source}: {restore_exc}"
                    ) from exc
            else:
                destination.unlink()
        # Retain independent recovery bytes on every failed publication.
        raise ConversionError(
            f"Conversion publication failed; original backup: {backup}; "
            f"encoded recovery: {encoded}: {exc}"
        ) from exc
    finally:
        if swap is not None:
            try:
                swap.unlink(missing_ok=True)
            except OSError as exc:
                logger.warning("Conversion retained publication temporary %s: %s", swap, exc)

    expected = published_identity if destination == source else source_identity
    if (_file_identity(source) != expected
            or _file_identity(destination) != published_identity):
        # Commit already succeeded: keep that durable catalog/task state, but
        # retain all recovery versions when either pathname changed during it.
        logger.warning(
            "Conversion identity conflict after commit; newer source/output preserved: %s / %s; "
            "original backup: %s; encoded recovery: %s", source, destination, backup, encoded,
        )
        return destination
    # Only observed-owned originals are removed. Cleanup failures retain safe
    # extra recovery versions without reversing a durable catalog publication.
    for obsolete in (source if destination != source else None, backup, encoded):
        if obsolete is not None:
            try:
                obsolete.unlink(missing_ok=True)
            except OSError as exc:
                logger.warning("Converted media committed; retained recovery file %s: %s", obsolete, exc)
    return destination


def convert_media(
    session: Session,
    media: Media,
    mode: ConversionMode,
    *,
    on_progress: Callable[[int], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
    before_publish: Callable[[], None] | None = None,
) -> Path:
    """Make `media` playable using `mode` and return the playable file.

    keep_original: the copy goes to Omoide's data folder (not the library, so
    it is never indexed as a duplicate); the original stays untouched.
    replace_original: the original is replaced by `<name>.mp4` in the same
    folder; it is deleted only after the conversion succeeded. Commits.
    """
    source = Path(media.path)
    if not source.is_file():
        raise ConversionError(f"File not found: {source}")

    if mode == "keep_original":
        target = converted_copy_path(media.id)
        convert_file(source, target, on_progress=on_progress, cancelled=cancelled, before_publish=before_publish)
        return target

    settings.general.ensure_media_path_writable(source)
    source_identity = _file_identity(source)
    if source_identity is None:
        raise ConversionError(f"File not found: {source}")
    descriptor, staging_name = tempfile.mkstemp(prefix=".omoide-replace-", suffix=".mp4", dir=source.parent)
    os.close(descriptor)
    staging = Path(staging_name)
    try:
        convert_file(source, staging, on_progress=on_progress, cancelled=cancelled)
    except Exception:
        staging.unlink(missing_ok=True)
        raise
    final = source.with_suffix(".mp4")
    if final != source:
        final = _available_name(final)
    try:
        if cancelled and cancelled():
            raise ConversionError("Conversion cancelled.")
        if before_publish:
            before_publish()
        settings.general.ensure_media_path_writable(source)
    except Exception:
        staging.unlink(missing_ok=True)
        raise
    result = publish_replacement(
        session, media, staging, final, expected_source_identity=source_identity,
    )
    try:
        converted_copy_path(media.id).unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("Converted media committed; stale playback copy could not be removed: %s", exc)
    return result


def stream_converted(source: Path, start_seconds: float = 0.0) -> Iterator[bytes]:
    """Yield a fragmented MP4 of `source` converted on the fly from
    `start_seconds`. Seeking means starting a new stream; the ffmpeg process
    is killed when the consumer stops reading."""
    cmd = [
        str(_ffmpeg()),
        "-nostdin", "-hide_banner", "-loglevel", "error",
        "-ss", f"{max(0.0, start_seconds):.3f}",
        "-i", str(source),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        *_OUTPUT_ARGS,
        "-movflags", "frag_keyframe+empty_moov+default_base_moof",
        "-f", "mp4", "pipe:1",
    ]
    proc = popen_silent(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    try:
        assert proc.stdout is not None
        while chunk := proc.stdout.read(256 * 1024):
            yield chunk
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait()
