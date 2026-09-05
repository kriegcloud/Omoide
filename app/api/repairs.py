"""Image repair job API."""

from __future__ import annotations

import json
import random
import secrets
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlmodel import Session, col, select

from app.config import settings
from app.database import get_session, safe_commit
from app.models import Face, ImageRepairJob, ImageRepairStatus, Media
from app.schemas.repair import (
    BulkRepairRequest,
    ImageRepairJobRead,
    RepairHealthRead,
    RepairJobPage,
    RepairParams,
    RepairRequest,
)
from app.services.comfy_annotation import ComfyAnnotationError
from app.services.background_prompts import load_prompts
from app.services.comfy_repair import SUPPORTED_REPAIR_PROFILES
from app.tasks.image_repair import repair_client, run_repair_job

router = APIRouter()


def _ensure_mutation_allowed() -> None:
    if settings.general.presentation_mode:
        raise HTTPException(status_code=403, detail="Repairs are disabled in presentation mode.")
    if not settings.repairs.enabled:
        raise HTTPException(status_code=503, detail="The repair backend is disabled.")


def _configured_profiles() -> set[str]:
    return {
        settings.repairs.remove_text_profile_id,
        settings.repairs.upscale_profile_id,
        settings.repairs.remove_people_profile_id,
        settings.repairs.background_swap_profile_id,
    }


def _bridge_ready(profile: str | None = None) -> list[str]:
    try:
        health = repair_client().health()
    except ComfyAnnotationError as exc:
        raise HTTPException(
            status_code=503, detail=f"Repair bridge unavailable: {exc.code}"
        ) from exc
    profiles = list(health.profiles)
    if not health.ready or (profile is not None and profile not in profiles):
        raise HTTPException(status_code=503, detail="Requested repair profile is unavailable.")
    return profiles


def _validate_profile(profile: str) -> None:
    if profile not in SUPPORTED_REPAIR_PROFILES or profile not in _configured_profiles():
        raise HTTPException(status_code=422, detail="Unsupported repair profile")


def _params_size(params: dict) -> None:
    try:
        size = len(json.dumps(params, allow_nan=False, separators=(",", ":")).encode())
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="Repair params must be JSON-compatible") from exc
    if size > 8 * 1024:
        raise HTTPException(status_code=422, detail="Repair params exceed 8 KB")


def _randomized_background_params(media_id: int, params: RepairParams) -> RepairParams:
    prompts = list(load_prompts())
    random.Random(media_id).shuffle(prompts)
    return params.model_copy(update={"prompt": prompts[0], "seed": secrets.randbits(63)})


def _params_for_media(
    session: Session,
    media: Media,
    request: RepairRequest,
) -> dict:
    params = request.params.model_dump(exclude_none=True)
    subject_profiles = {
        settings.repairs.remove_people_profile_id,
        settings.repairs.background_swap_profile_id,
    }
    if request.profile not in subject_profiles:
        _params_size(params)
        return params
    person_id = request.person_id
    if person_id is None and isinstance(params.get("person_id"), int):
        person_id = params.pop("person_id")
    if "subject_box" not in params:
        if person_id is None:
            raise HTTPException(
                status_code=422,
                detail="This repair requires a person_id or subject_box.",
            )
        face = session.exec(
            select(Face)
            .where(Face.media_id == media.id, Face.person_id == person_id)
            .order_by(col(Face.det_score).desc(), Face.id)
        ).first()
        if face is None:
            raise HTTPException(status_code=422, detail="Person has no face in this media")
        if not media.width or not media.height:
            raise HTTPException(status_code=409, detail="Media dimensions are unavailable")
        scale = max(media.width, media.height) / min(max(media.width, media.height), 1280)
        x, y, width, height = face.bbox
        params["subject_box"] = {
            "x": round(x * scale),
            "y": round(y * scale),
            "width": round(width * scale),
            "height": round(height * scale),
        }
    if request.profile == settings.repairs.background_swap_profile_id:
        prompt = params.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            raise HTTPException(status_code=422, detail="Background swap requires a prompt")
        params["prompt"] = prompt.strip()
        params.setdefault("seed", secrets.randbits(63))
    _params_size(params)
    return params


def _create_job(
    session: Session,
    background_tasks: BackgroundTasks,
    media: Media,
    request: RepairRequest,
) -> ImageRepairJob:
    if media.duration is not None:
        raise HTTPException(status_code=422, detail="Videos cannot be repaired")
    params = _params_for_media(session, media, request)
    job = ImageRepairJob(
        media_id=int(media.id),
        profile=request.profile,
        params=params,
        status=ImageRepairStatus.QUEUED,
    )
    session.add(job)
    safe_commit(session)
    session.refresh(job)
    background_tasks.add_task(run_repair_job, job.id)
    return job


@router.get("/health", response_model=RepairHealthRead)
def health() -> RepairHealthRead:
    if not settings.repairs.enabled:
        return RepairHealthRead(enabled=False, ready=False, detail="Repair backend disabled")
    try:
        profiles = _bridge_ready()
        repair_profiles = sorted(_configured_profiles().intersection(profiles))
        return RepairHealthRead(
            enabled=True,
            ready=bool(repair_profiles),
            profiles=repair_profiles,
        )
    except HTTPException as exc:
        return RepairHealthRead(enabled=True, ready=False, detail=str(exc.detail))


@router.post(
    "/media/{media_id}",
    response_model=ImageRepairJobRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def start_repair(
    media_id: int,
    request: RepairRequest,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
) -> ImageRepairJob:
    _ensure_mutation_allowed()
    _validate_profile(request.profile)
    _bridge_ready(request.profile)
    media = session.get(Media, media_id)
    if media is None:
        raise HTTPException(status_code=404, detail="Media not found")
    return _create_job(session, background_tasks, media, request)


@router.post("/bulk", response_model=list[ImageRepairJobRead], status_code=202)
def start_bulk_repair(
    request: BulkRepairRequest,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
) -> list[ImageRepairJob]:
    _ensure_mutation_allowed()
    _validate_profile(request.profile)
    _bridge_ready(request.profile)
    jobs: list[ImageRepairJob] = []
    prepared: list[tuple[Media, dict]] = []
    for media_id in dict.fromkeys(request.media_ids):
        media = session.get(Media, media_id)
        if media is None:
            continue
        if media.duration is not None:
            continue
        media_request: RepairRequest = request
        if (
            request.profile == settings.repairs.background_swap_profile_id
            and request.randomize_prompts
        ):
            randomized_params = _randomized_background_params(int(media.id), request.params)
            media_request = RepairRequest(
                profile=request.profile,
                params=randomized_params,
                person_id=request.person_id,
            )
        prepared.append((media, _params_for_media(session, media, media_request)))
    for media, params in prepared:
        job = ImageRepairJob(
            media_id=int(media.id),
            profile=request.profile,
            params=params,
            status=ImageRepairStatus.QUEUED,
        )
        session.add(job)
        jobs.append(job)
    safe_commit(session)
    for job in jobs:
        session.refresh(job)
        background_tasks.add_task(run_repair_job, job.id)
    return jobs


@router.get("/background-prompts", response_model=list[str])
def background_prompts() -> list[str]:
    return list(load_prompts())


@router.get("/", response_model=RepairJobPage)
def list_repairs(
    cursor: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    media_id: int | None = None,
    result_media_id: int | None = None,
    job_status: ImageRepairStatus | None = Query(default=None, alias="status"),
    session: Session = Depends(get_session),
) -> RepairJobPage:
    statement = select(ImageRepairJob)
    if media_id is not None:
        statement = statement.where(ImageRepairJob.media_id == media_id)
    if result_media_id is not None:
        statement = statement.where(ImageRepairJob.result_media_id == result_media_id)
    if job_status is not None:
        statement = statement.where(ImageRepairJob.status == job_status)
    rows = list(
        session.exec(
            statement.order_by(ImageRepairJob.created_at.desc(), ImageRepairJob.id)
            .offset(cursor)
            .limit(limit + 1)
        ).all()
    )
    items = rows[:limit]
    return RepairJobPage(
        items=[ImageRepairJobRead.model_validate(item) for item in items],
        next_cursor=str(cursor + limit) if len(rows) > limit else None,
    )


@router.get("/{job_id}", response_model=ImageRepairJobRead)
def get_repair(job_id: str, session: Session = Depends(get_session)) -> ImageRepairJob:
    job = session.get(ImageRepairJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Repair job not found")
    return job


@router.post("/{job_id}/cancel", response_model=ImageRepairJobRead)
def cancel_repair(job_id: str, session: Session = Depends(get_session)) -> ImageRepairJob:
    _ensure_mutation_allowed()
    job = session.get(ImageRepairJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Repair job not found")
    if job.status not in {
        ImageRepairStatus.CREATED,
        ImageRepairStatus.QUEUED,
        ImageRepairStatus.RUNNING,
    }:
        return job
    if job.status == ImageRepairStatus.RUNNING and job.external_prompt_id:
        try:
            repair_client().cancel(attempt_id=UUID(job.external_prompt_id))
        except ComfyAnnotationError as exc:
            if not exc.retryable:
                raise HTTPException(status_code=409, detail=exc.message) from exc
    job.status = ImageRepairStatus.CANCELLED
    job.finished_at = datetime.utcnow()
    session.add(job)
    safe_commit(session)
    session.refresh(job)
    return job
