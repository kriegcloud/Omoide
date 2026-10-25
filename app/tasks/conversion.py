from __future__ import annotations

from datetime import UTC, datetime, timezone
import os
import subprocess
import sys
import tempfile
import time
import ffmpeg
from sqlalchemy import update
from app.accelerators import get_ffmpeg_accel_config
from app.ffmpeg import ensure_ffmpeg_available
from app.subprocess_helpers import popen_silent
from .common import _start_task, _finish_task

from pathlib import Path

from sqlmodel import Session, col, select

import app.database as db
from app.config import settings
from app.conversion import (
    ConversionError,
    ConversionMode,
    _file_identity,
    convert_media,
    existing_converted_copy,
    is_browser_playable,
)
from app.database import safe_commit
from app.logger import logger
from app.models import Media, ProcessingTask

from .state import clear_task_progress, record_task_failure, set_task_progress

__all__ = ["run_convert_media", "run_convert_unplayable_videos"]


def _cancelled(task_id: str) -> bool:
    with Session(db.engine) as s:
        task = s.get(ProcessingTask, task_id)
        return not task or task.status in {"cancelled", "interrupted", "failed"}


def _start(session: Session, task: ProcessingTask, total: int) -> bool:
    if not _start_task(session, task):
        return False
    task.total = total
    task.processed = 0
    session.add(task)
    safe_commit(session)
    return True


def _finish(session: Session, task: ProcessingTask, status: str) -> None:
    _finish_task(session, task, status)


def _claim_active(session: Session, task_id: str) -> None:
    """Serialize final publication against cancellation/shutdown in SQLite."""
    result = session.exec(update(ProcessingTask).where(
        ProcessingTask.id == task_id, ProcessingTask.status == "running",
    ).values(status="running").execution_options(synchronize_session=False))
    if not result.rowcount:
        session.rollback()
        raise ConversionError("Conversion task is no longer running.")


def run_convert_media(task_id: str, media_id: int, mode: ConversionMode) -> None:
    """Convert one video; progress is reported in percent (total = 100)."""
    if mode == "replace_original":
        with Session(db.engine) as lookup:
            media = lookup.get(Media, media_id)
            if media is None:
                return
            media_path = str(media.path)
        return _run_conversion(task_id, media_path, media_id)
    with Session(db.engine) as session:
        task = session.get(ProcessingTask, task_id)
        media = session.get(Media, media_id)
        if not task or not media:
            logger.error("Conversion task %s: task or media missing", task_id)
            return
        if not _start(session, task, total=100):
            return

        def progress(pct: int) -> None:
            if pct > task.processed:
                task.processed = pct
                session.add(task)
                safe_commit(session)
            set_task_progress(
                task_id, current_step="converting video", current_item=f"{pct}%"
            )

        try:
            convert_media(
                session,
                media,
                mode,
                on_progress=progress,
                cancelled=lambda: _cancelled(task_id),
                before_publish=lambda: _claim_active(session, task_id),
            )
            task.processed = 100
            _finish(session, task, "completed")
        except (ConversionError, PermissionError, OSError) as exc:
            logger.warning("Conversion of %s failed: %s", media.path, exc)
            record_task_failure(task_id, media.path, str(exc))
            _finish(session, task, "failed")
    clear_task_progress(task_id)


def run_convert_unplayable_videos(
    task_id: str, mode: ConversionMode | None = None
) -> None:
    """Convert every video the browser cannot play, using `mode` (defaults to
    the configured automatic conversion mode)."""
    mode = mode or settings.video.auto_convert
    if mode == "off":
        logger.info("Automatic video conversion is off; nothing to do.")
        return
    with Session(db.engine) as session:
        task = session.get(ProcessingTask, task_id)
        if not task:
            return
        videos = session.exec(
            select(Media).where(
                col(Media.duration).is_not(None),
                col(Media.missing_since).is_(None),
                col(Media.processing_error).is_(None),
            )
        ).all()
        if not _start(session, task, total=len(videos)):
            return
        converted = 0
        for media in videos:
            if _cancelled(task_id):
                break
            set_task_progress(
                task_id, current_step="checking videos", current_item=media.filename
            )
            try:
                needs_work = (
                    not existing_converted_copy(media.id)
                    and not is_browser_playable(Path(media.path))
                )
                if needs_work:
                    set_task_progress(
                        task_id,
                        current_step="converting video",
                        current_item=media.filename,
                    )
                    convert_media(
                        session, media, mode, cancelled=lambda: _cancelled(task_id),
                        before_publish=lambda: _claim_active(session, task_id)
                    )
                    converted += 1
            except (ConversionError, PermissionError, OSError) as exc:
                logger.warning("Conversion of %s failed: %s", media.path, exc)
                record_task_failure(task_id, media.path, str(exc))
            task.processed += 1
            session.add(task)
            safe_commit(session)
        logger.info("Converted %d of %d videos (%s).", converted, len(videos), mode)
        _finish(session, task, "completed")
    clear_task_progress(task_id)


def _stop_conversion_process(proc) -> None:
    """Stop only this task's FFmpeg child, with bounded termination and reaping."""
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


def _run_conversion(task_id: str, media_path: str, media_id: int):
    with Session(db.engine) as session:
        task = session.get(ProcessingTask, task_id)
        if not task:
            logger.error(f"Task {task_id} not found.")
            return

        if not _start_task(session, task):
            return

        media = session.get(Media, media_id)
        if media is None or str(media.path) != str(media_path):
            _finish_task(session, task, "failed")
            return
        media_path_obj = Path(media_path)
        temp_output_path: Path | None = None
        progress_path: Path | None = None
        preserve_output = False
        proc = None
        error_log = tempfile.TemporaryFile()

        def _is_stopped() -> bool:
            return session.exec(
                select(ProcessingTask.status).where(ProcessingTask.id == task_id)
            ).first() != "running"

        try:
            settings.general.ensure_media_path_writable(media_path_obj)
            source_identity = _file_identity(media_path_obj)
            if source_identity is None:
                raise ConversionError(f"File not found: {media_path_obj}")
            # Reserve both files exclusively in the source filesystem. FFmpeg may
            # overwrite only these task-owned files, never a guessed sibling name.
            descriptor, filename = tempfile.mkstemp(
                prefix=f".omoide-convert-{task_id}-", suffix=".mp4", dir=media_path_obj.parent
            )
            os.close(descriptor)
            temp_output_path = Path(filename)
            descriptor, filename = tempfile.mkstemp(
                prefix=f".omoide-convert-{task_id}-", suffix=".progress", dir=media_path_obj.parent
            )
            os.close(descriptor)
            progress_path = Path(filename)
            ffmpeg_bin = ensure_ffmpeg_available()
            if not ffmpeg_bin:
                raise RuntimeError(
                    "ffmpeg is required to convert videos but could not be located."
                )
            ffprobe_name = (
                "ffprobe.exe" if sys.platform.startswith("win") else "ffprobe"
            )
            ffprobe_path = ffmpeg_bin.with_name(ffprobe_name)
            ffprobe_cmd = str(ffprobe_path) if ffprobe_path.exists() else "ffprobe"

            info = ffmpeg.probe(str(media_path_obj), cmd=ffprobe_cmd)
            try:
                dur_s = float(info.get("format", {}).get("duration") or 0.0)
            except Exception:
                dur_s = 0.0
            dur_us = dur_s * 1000000
            accel = get_ffmpeg_accel_config(settings.processors.prefer_gpu)
            video_encoder = accel.video_encoder or "libx264"
            # run ffmpeg with stderr piped so we can parse “progress=…”
            # Here’s one way using the “-progress” flag:
            cmd = [
                str(ffmpeg_bin),
                "-nostdin",
                *accel.hwaccel_args,
                "-i",
                str(media_path_obj),
                "-c:v",
                video_encoder,
                "-filter:v",
                "fps=30",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-movflags",
                "use_metadata_tags+faststart",
                "-progress",
                str(progress_path),
                "-nostats",
                "-y",
                str(temp_output_path),
            ]
            if _is_stopped():
                return
            logger.info(f"Running FFmpeg command: {' '.join(cmd)}")
            proc = popen_silent(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=error_log,
                stdin=subprocess.DEVNULL,
                text=True,
            )

            def _parse_out_time(value: str) -> int | None:
                raw = value.strip()
                if not raw or raw.upper() == "N/A":
                    return None
                if raw.isnumeric():
                    return int(raw)
                parts = raw.split(":")
                if len(parts) != 3:
                    return None
                try:
                    hours = int(parts[0])
                    minutes = int(parts[1])
                    seconds = float(parts[2])
                except Exception:
                    return None
                return int((hours * 3600 + minutes * 60 + seconds) * 1_000_000)

            def _read_progress() -> tuple[int | None, bool]:
                try:
                    content = progress_path.read_text(errors="ignore")
                except FileNotFoundError:
                    return None, False
                except OSError:
                    return None, False
                out_candidates: list[int] = []
                progress_end = False
                for line in content.splitlines():
                    if line.startswith(("out_time_us=", "out_time_ms=")):
                        value = line.split("=", 1)[1].strip()
                        parsed = _parse_out_time(value)
                        if parsed is not None:
                            out_candidates.append(parsed)
                    elif line.startswith("out_time="):
                        value = line.split("=", 1)[1].strip()
                        parsed = _parse_out_time(value)
                        if parsed is not None:
                            out_candidates.append(parsed)
                    elif line.startswith("progress="):
                        if line.split("=", 1)[1].strip() == "end":
                            progress_end = True
                out_us = max(out_candidates) if out_candidates else None
                return out_us, progress_end

            try:
                while True:
                    if _is_stopped():
                        return
                    out_us, progress_end = _read_progress()
                    if out_us is not None and dur_us > 0:
                        pct = min(99, int(out_us / dur_us * 100))
                        if pct > task.processed:
                            task.processed = pct
                            session.add(task)
                            session.commit()
                        set_task_progress(
                            task_id,
                            current_step="converting video",
                            current_item=f"Progress: {pct}%",
                        )
                    if proc.poll() is not None:
                        break
                    if progress_end and task.processed < 99:
                        task.processed = 99
                        session.add(task)
                        session.commit()
                    time.sleep(0.5)
                stdout, stderr = proc.communicate()
            finally:
                if progress_path.exists():
                    progress_path.unlink()
            if _is_stopped():
                return
            if proc.returncode != 0:
                error_log.seek(0)
                stderr = stderr or error_log.read().decode("utf-8", "replace")[-2000:]
                logger.error(
                    f"FFmpeg failed for {media_path} with exit code {proc.returncode}"
                )
                logger.error(f"FFmpeg stderr: {stderr}")
                raise Exception(f"FFmpeg conversion failed: {stderr}")

            # Claim the final write while this task is still running. SQLite's
            # writer lock then serializes interruption with the replacement and
            # completion, so a previously interrupted task cannot replace media.
            claimed = session.exec(
                update(ProcessingTask)
                .where(ProcessingTask.id == task_id, ProcessingTask.status == "running")
                .values(processed=100)
                .execution_options(synchronize_session=False)
            )
            if not claimed.rowcount:
                session.rollback()
                return
            media = session.get(Media, media_id)
            if media is None or not temp_output_path.exists() or temp_output_path.stat().st_size == 0:
                raise RuntimeError("Converted output or media record no longer exists")
            preserve_output = True
            from app.conversion import _available_name
            destination = media_path_obj.with_suffix(".mp4")
            if destination != media_path_obj:
                destination = _available_name(destination)
            from app.conversion import publish_replacement
            publish_replacement(
                session, media, temp_output_path, destination,
                commit=lambda: _finish_task(session, task, "completed"),
                expected_source_identity=source_identity,
            )
            # Publication owns encoded cleanup after handoff. Any encoded file
            # it retains is recovery data (for example, a postcommit conflict),
            # even when the catalog/task was durably completed.
        except Exception as e:
            logger.error(f"Conversion task {task_id} failed: {e}")
            session.rollback()
            if _is_stopped():
                return
            task.result = {"error": str(e)}
            if preserve_output and temp_output_path is not None:
                task.result = {**task.result, "recovery_path": str(temp_output_path)}
            session.add(task)
            _finish_task(session, task, "failed")
        finally:
            if proc is not None:
                _stop_conversion_process(proc)
            if progress_path is not None:
                progress_path.unlink(missing_ok=True)
            if temp_output_path is not None and not preserve_output:
                temp_output_path.unlink(missing_ok=True)
            error_log.close()
            clear_task_progress(task_id)
