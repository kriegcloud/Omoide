import subprocess
import sys
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import ffmpeg
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import update
from sqlmodel import Session, select

import app.database as db
from app.accelerators import get_ffmpeg_accel_config
from app.config import settings
from app.database import get_session
from app.ffmpeg import ensure_ffmpeg_available
from app.logger import logger
from app.models import Media, ProcessingTask
from app.processor_registry import load_processors
from app.subprocess_helpers import popen_silent
from app.tasks.common import _finish_task, _start_task
from app.tasks.state import clear_task_progress, set_task_progress

router = APIRouter()


@router.get("/media/{media_id}/processors", summary="List all processors")
def list_processors():
    return [p.name for p in load_processors()]


@router.get(
    "/media/{media_id}/processors/{processor_name}",
    summary="Get a processor’s output",
)
def get_processor(
    media_id: int,
    processor_name: str,
    session: Session = Depends(get_session),
):
    for p in load_processors():
        if p.name == processor_name:
            return p.get_results(media_id, session)
    raise HTTPException(404, f"Processor {processor_name} not found")


@router.post(
    "/media/{media_id}/converter",
    summary="Converts video to web compatible format",
)
def start_conversion(
    media_id: int,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
):
    if settings.general.presentation_mode:
        raise HTTPException(
            status_code=403,
            detail="Not allowed in settings.general.presentation_mode mode.",
        )
    media = session.get(Media, media_id)
    if not media:
        raise HTTPException(404, "Media not found")
    try:
        settings.general.ensure_media_path_writable(Path(media.path))
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    task = ProcessingTask(
        task_type="convert",
        status="pending",
        total=100,  # we’ll treat this as a percentage 0–100
        processed=0,
        created_at=datetime.now(timezone.utc),
    )
    session.add(task)
    session.commit()
    session.refresh(task)
    background_tasks.add_task(_run_conversion, task.id, str(media.path), media.id)
    return task


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

        media_path_obj = Path(media_path)
        temp_output_path: Path | None = None
        progress_path: Path | None = None
        preserve_output = False
        proc = None

        def _is_stopped() -> bool:
            return session.exec(
                select(ProcessingTask.status).where(ProcessingTask.id == task_id)
            ).first() != "running"

        try:
            settings.general.ensure_media_path_writable(media_path_obj)
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
                stderr=subprocess.PIPE,
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
            if media is None or not temp_output_path.exists():
                raise RuntimeError("Converted output or media record no longer exists")
            preserve_output = True
            new_file = temp_output_path.replace(media_path_obj)
            preserve_output = False
            media.path = str(new_file)
            media.filename = new_file.name
            session.add(media)
            _finish_task(session, task, "completed")
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
            clear_task_progress(task_id)
