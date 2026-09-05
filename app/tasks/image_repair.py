"""Durable execution of one image repair job."""

from __future__ import annotations

import io
import os
from datetime import datetime
from uuid import UUID

from PIL import Image, ImageOps
from sqlmodel import Session

import app.database as db
from app.config import settings
from app.database import safe_commit
from app.logger import logger
from app.models import ImageRepairJob, ImageRepairStatus, Media, ProcessingTask
from app.services.comfy_annotation import ComfyAnnotationError
from app.services.comfy_repair import ComfyRepairClient
from app.services.image_edits import write_repaired
from app.utils import generate_perceptual_hash, generate_thumbnail

_EDIT_PROCESSORS = ["faces", "embedding_extractor", "auto_tagger", "blur", "exif"]
_UPSCALE_PROFILE = "omoide-upscale-v1"


def repair_client() -> ComfyRepairClient:
    return ComfyRepairClient(
        settings.repairs.inference_socket_path,
        settings.repairs.timeout_seconds,
    )


def _finish_failure(job_id: str, code: str, message: str, retryable: bool) -> None:
    with Session(db.engine) as session:
        job = session.get(ImageRepairJob, job_id)
        if job is None or job.status == ImageRepairStatus.CANCELLED:
            return
        job.status = ImageRepairStatus.FAILED
        job.error_code = code
        job.error_message = message[:2000]
        job.retryable = retryable
        job.finished_at = datetime.utcnow()
        session.add(job)
        safe_commit(session)


def run_repair_job(job_id: str) -> None:
    """Load, transport, persist, and enqueue processing for a repair copy."""
    with Session(db.engine) as session:
        job = session.get(ImageRepairJob, job_id)
        if job is None or job.status == ImageRepairStatus.CANCELLED:
            return
        media = session.get(Media, job.media_id)
        if media is None:
            _finish_failure(job_id, "media-not-found", "Media record does not exist", False)
            return
        if media.duration is not None:
            _finish_failure(job_id, "video-not-supported", "Videos cannot be repaired", False)
            return
        if media.missing_since is not None or not os.path.isfile(media.path):
            _finish_failure(job_id, "media-missing", "Media file is missing", False)
            return
        job.status = ImageRepairStatus.RUNNING
        job.started_at = datetime.utcnow()
        job.external_prompt_id = job.id
        session.add(job)
        safe_commit(session)
        profile = job.profile
        params = dict(job.params or {})
        source_created_at = media.created_at
        source_path = media.path

    try:
        with Image.open(source_path) as opened:
            opened.load()
            source = ImageOps.exif_transpose(opened).copy()
        transport = source
        transport_metadata: dict[str, int | bool] = {
            "downscaled": False,
            "source_width": source.width,
            "source_height": source.height,
            "transport_width": source.width,
            "transport_height": source.height,
        }
        if profile != _UPSCALE_PROFILE and max(source.size) > 4096:
            scale = 4096 / max(source.size)
            transport = source.resize(
                (max(1, round(source.width * scale)), max(1, round(source.height * scale))),
                Image.Resampling.LANCZOS,
            )
            transport_metadata.update(
                downscaled=True,
                transport_width=transport.width,
                transport_height=transport.height,
            )
        result = repair_client().repair(
            attempt_id=UUID(job_id),
            profile_id=profile,
            image=transport,
            params=params or None,
        )
        with Image.open(io.BytesIO(result.image)) as opened_result:
            opened_result.load()
            repaired = opened_result.copy()
        target = write_repaired(
            source_path,
            repaired,
            profile,
            media_roots=settings.general.resolved_media_dirs(),
        )
        with Session(db.engine) as session:
            job = session.get(ImageRepairJob, job_id)
            if job is None:
                target.unlink(missing_ok=True)
                return
            if job.status == ImageRepairStatus.CANCELLED:
                target.unlink(missing_ok=True)
                return
            media = Media(
                path=os.fspath(target),
                filename=target.name,
                size=target.stat().st_size,
                width=repaired.width,
                height=repaired.height,
                created_at=source_created_at,
            )
            session.add(media)
            safe_commit(session)
            session.refresh(media)
            media.phash = generate_perceptual_hash(media, type="image")
            media.thumbnail_path, thumbnail_error = generate_thumbnail(media)
            if thumbnail_error:
                logger.warning("Repair media %s thumbnail failed: %s", media.id, thumbnail_error)
            session.add(media)
            job.result_media_id = media.id
            if transport_metadata["downscaled"]:
                job.params = {**dict(job.params or {}), "_transport": transport_metadata}
            job.status = ImageRepairStatus.SUCCEEDED
            job.finished_at = datetime.utcnow()
            session.add(job)
            processor_task = ProcessingTask(
                task_type="run_processor_for_media", total=1, processed=0
            )
            session.add(processor_task)
            safe_commit(session)
            session.refresh(processor_task)
            processor_task_id = processor_task.id
            media_id = int(media.id)
        try:
            repair_client().ack_attempt(attempt_id=UUID(job_id))
        except ComfyAnnotationError as exc:
            logger.warning(
                "Repair job %s succeeded but history acknowledgement failed: %s",
                job_id,
                exc.message,
            )
        from app.tasks.media_processing import run_processors_for_media

        run_processors_for_media(processor_task_id, _EDIT_PROCESSORS, [media_id])
    except ComfyAnnotationError as exc:
        _finish_failure(job_id, exc.code, exc.message, exc.retryable)
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        _finish_failure(job_id, "image-repair-failed", str(exc), False)
    except Exception as exc:  # noqa: BLE001 - durable background boundary
        logger.exception("Image repair job %s failed", job_id)
        _finish_failure(job_id, "repair-failed", str(exc), False)
