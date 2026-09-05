from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from pydantic import ValidationError
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import defer
from sqlmodel import Session, select

from app.config import settings
from app.database import (
    MigrationState,
    get_migration_state,
    get_session,
    safe_commit,
)
from app.logger import logger
from app.models import (
    AnnotationAttempt,
    AnnotationAttemptStatus,
    AnnotationAuthor,
    AnnotationReviewStatus,
    Media,
    MediaAnnotation,
)
from app.schemas.annotation import (
    AnnotationAttemptRead,
    AnnotationAttemptDetailRead,
    AnnotationGenerateRequest,
    AnnotationHealthRead,
    AnnotationRevisionCreate,
    MediaAnnotationRead,
    MediaAnnotationState,
)
from app.services.comfy_annotation import ComfyAnnotationError, ComfyAnnotationResult
from app.annotation_tasks import (
    AnnotationBusyError,
    AnnotationMediaUnavailable,
    AnnotationPersistenceConflict,
    _acknowledge_persisted_terminal,
    _cas_attempt_status,
    _persist_success,
    annotation_client,
    create_annotation_attempt,
    run_annotation_attempt,
    validate_revision_content,
)

router = APIRouter()

_ACTIVE_ATTEMPT_STATUSES = {
    AnnotationAttemptStatus.CREATED,
    AnnotationAttemptStatus.RUNNING,
}
_CANCELLABLE_ATTEMPT_STATUSES = (
    AnnotationAttemptStatus.CREATED,
    AnnotationAttemptStatus.RUNNING,
    AnnotationAttemptStatus.UNKNOWN,
    AnnotationAttemptStatus.LOST,
)
_RETRYABLE_ATTEMPT_STATUSES = {
    AnnotationAttemptStatus.FAILED,
    AnnotationAttemptStatus.CANCELLED,
}
_ANNOTATION_SCHEMA_UNAVAILABLE = "Annotation database migration unavailable"


def _ensure_annotation_schema_available() -> None:
    if get_migration_state() == MigrationState.FAILED:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "annotation_schema_unavailable",
                "message": _ANNOTATION_SCHEMA_UNAVAILABLE,
            },
        )


def _ensure_annotation_mutation_allowed() -> None:
    if settings.general.presentation_mode:
        raise HTTPException(
            status_code=403,
            detail="Annotation mutations are disabled in presentation mode.",
        )
    if not settings.annotations.enabled:
        raise HTTPException(
            status_code=503,
            detail="The annotation backend is disabled.",
        )
    _ensure_annotation_schema_available()


def _media_or_404(session: Session, media_id: int) -> Media:
    media = session.get(Media, media_id)
    if media is None:
        raise HTTPException(status_code=404, detail="Media not found")
    return media


def _ensure_still_image(media: Media) -> None:
    if media.duration is not None:
        raise HTTPException(
            status_code=422,
            detail="The first annotation slice supports still images only.",
        )


def _ensure_no_active_attempt(session: Session) -> None:
    active = session.exec(
        select(AnnotationAttempt).where(AnnotationAttempt.active_slot == 1)
    ).first()
    if active is not None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "annotation_busy",
                "message": "Another Omoide annotation attempt is active.",
                "attempt_id": active.id,
            },
        )


@router.get("/health", response_model=AnnotationHealthRead)
def annotation_health(
    session: Session = Depends(get_session),
) -> AnnotationHealthRead:
    if not settings.annotations.enabled:
        return AnnotationHealthRead(
            enabled=False,
            ready=False,
            configured_profiles=[
                settings.annotations.caption_profile_id,
                settings.annotations.tags_profile_id,
            ],
            detail="Annotation backend disabled",
        )
    if get_migration_state() == MigrationState.FAILED:
        return AnnotationHealthRead(
            enabled=True,
            ready=False,
            configured_profiles=[
                settings.annotations.caption_profile_id,
                settings.annotations.tags_profile_id,
            ],
            detail=_ANNOTATION_SCHEMA_UNAVAILABLE,
        )
    try:
        lease_holder = session.exec(
            select(AnnotationAttempt).where(AnnotationAttempt.active_slot == 1)
        ).first()
    except SQLAlchemyError:
        logger.exception("Annotation health database readiness check failed")
        return AnnotationHealthRead(
            enabled=True,
            ready=False,
            configured_profiles=[
                settings.annotations.caption_profile_id,
                settings.annotations.tags_profile_id,
            ],
            detail=_ANNOTATION_SCHEMA_UNAVAILABLE,
        )
    try:
        health = annotation_client().health()
        return AnnotationHealthRead(
            enabled=True,
            ready=health.ready,
            profiles=list(health.profiles),
            configured_profiles=list(health.configured_profiles),
            unavailable_profiles=health.unavailable_profiles,
            active_attempt_id=(
                lease_holder.id
                if lease_holder is not None
                else (
                    str(health.active_attempt_id)
                    if health.active_attempt_id is not None
                    else None
                )
            ),
        )
    except ComfyAnnotationError as exc:
        return AnnotationHealthRead(
            enabled=True,
            ready=False,
            active_attempt_id=(lease_holder.id if lease_holder is not None else None),
            detail=f"{exc.code}: {exc.message}",
        )
    except OSError as exc:
        return AnnotationHealthRead(
            enabled=True,
            ready=False,
            active_attempt_id=(lease_holder.id if lease_holder is not None else None),
            detail=str(exc),
        )


@router.get(
    "/media/{media_id}",
    response_model=MediaAnnotationState,
)
def get_media_annotations(
    media_id: int,
    history_limit: int = Query(default=25, ge=1, le=100),
    session: Session = Depends(get_session),
) -> MediaAnnotationState:
    _ensure_annotation_schema_available()
    _media_or_404(session, media_id)
    attempts = list(
        session.exec(
            select(AnnotationAttempt)
            .where(AnnotationAttempt.media_id == media_id)
            .options(
                defer(AnnotationAttempt.raw_result),
                defer(AnnotationAttempt.normalized_result),
            )
            .order_by(AnnotationAttempt.created_at.desc())
            .limit(history_limit)
        ).all()
    )
    annotations = list(
        session.exec(
            select(MediaAnnotation)
            .where(MediaAnnotation.media_id == media_id)
            .order_by(MediaAnnotation.revision.desc())
            .limit(history_limit)
        ).all()
    )
    return MediaAnnotationState(
        media_id=media_id,
        attempts=[AnnotationAttemptRead.model_validate(item) for item in attempts],
        annotations=[MediaAnnotationRead.model_validate(item) for item in annotations],
    )


@router.get(
    "/attempts/{attempt_id}",
    response_model=AnnotationAttemptDetailRead,
)
def get_annotation_attempt(
    attempt_id: str,
    session: Session = Depends(get_session),
) -> AnnotationAttempt:
    _ensure_annotation_schema_available()
    attempt = session.get(AnnotationAttempt, attempt_id)
    if attempt is None:
        raise HTTPException(status_code=404, detail="Annotation attempt not found")
    return attempt


@router.post(
    "/media/{media_id}/attempts",
    response_model=AnnotationAttemptRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def start_annotation(
    media_id: int,
    request: AnnotationGenerateRequest,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
) -> AnnotationAttempt:
    _ensure_annotation_mutation_allowed()
    media = _media_or_404(session, media_id)
    _ensure_still_image(media)
    _ensure_no_active_attempt(session)
    try:
        attempt = create_annotation_attempt(
            session,
            media_id=media_id,
            kind=request.kind,
        )
    except AnnotationMediaUnavailable as exc:
        raise HTTPException(status_code=404, detail="Media not found") from exc
    except AnnotationBusyError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "annotation_busy",
                "message": "Another Omoide annotation attempt is active.",
            },
        ) from exc
    background_tasks.add_task(run_annotation_attempt, attempt.id)
    return attempt


@router.post(
    "/attempts/{attempt_id}/retry",
    response_model=AnnotationAttemptRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def retry_annotation(
    attempt_id: str,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
) -> AnnotationAttempt:
    _ensure_annotation_mutation_allowed()
    previous = session.get(AnnotationAttempt, attempt_id)
    if previous is None:
        raise HTTPException(status_code=404, detail="Annotation attempt not found")
    if previous.status in _ACTIVE_ATTEMPT_STATUSES:
        raise HTTPException(status_code=409, detail="Attempt is still active")
    if (
        previous.status not in _RETRYABLE_ATTEMPT_STATUSES
        or previous.retryable is not True
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "annotation_retry_not_allowed",
                "message": (
                    "Only resolved retryable failed or cancelled attempts may be "
                    "retried."
                ),
                "attempt_id": previous.id,
                "status": previous.status.value,
            },
        )
    media = _media_or_404(session, previous.media_id)
    _ensure_still_image(media)
    _ensure_no_active_attempt(session)
    try:
        attempt = create_annotation_attempt(
            session,
            media_id=previous.media_id,
            kind=previous.kind,
            predecessor_attempt_id=previous.id,
        )
    except AnnotationMediaUnavailable as exc:
        raise HTTPException(status_code=404, detail="Media not found") from exc
    except AnnotationBusyError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "annotation_busy",
                "message": "Another Omoide annotation attempt is active.",
            },
        ) from exc
    background_tasks.add_task(run_annotation_attempt, attempt.id)
    return attempt


@router.post(
    "/attempts/{attempt_id}/cancel",
    response_model=AnnotationAttemptRead,
)
def cancel_annotation(
    attempt_id: str,
    session: Session = Depends(get_session),
) -> AnnotationAttempt:
    _ensure_annotation_mutation_allowed()
    attempt = session.get(AnnotationAttempt, attempt_id)
    if attempt is None:
        raise HTTPException(status_code=404, detail="Annotation attempt not found")
    if attempt.status not in _CANCELLABLE_ATTEMPT_STATUSES:
        return attempt
    if attempt.status == AnnotationAttemptStatus.CREATED:
        now = datetime.utcnow()
        cancelled = _cas_attempt_status(
            session,
            attempt_id,
            (AnnotationAttemptStatus.CREATED,),
            AnnotationAttemptStatus.CANCELLED,
            values={
                "active_slot": None,
                "error_code": "annotation_cancelled",
                "error_message": "Cancelled by user before submission",
                "retryable": True,
                "finished_at": now,
                "history_acknowledged_at": now,
            },
        )
        if cancelled:
            session.commit()
            session.expire_all()
            persisted = session.get(AnnotationAttempt, attempt_id)
            if persisted is None:  # pragma: no cover - foreign deletion race
                raise HTTPException(
                    status_code=404,
                    detail="Annotation attempt not found",
                )
            return persisted

        # The worker may have atomically claimed the attempt after our read.
        # End the stale transaction before deciding whether the exact bridge
        # prompt now needs a cancellation tombstone.
        session.rollback()
        session.expire_all()
        attempt = session.get(AnnotationAttempt, attempt_id)
        if attempt is None:
            raise HTTPException(status_code=404, detail="Annotation attempt not found")
        if attempt.status not in _CANCELLABLE_ATTEMPT_STATUSES:
            return attempt
    client = annotation_client()
    try:
        result = client.cancel(attempt_id=UUID(attempt.id))
    except ComfyAnnotationError as exc:
        session.rollback()
        session.expire_all()
        latest = session.get(AnnotationAttempt, attempt_id)
        if latest is None:
            raise HTTPException(status_code=404, detail="Annotation attempt not found")
        if latest.status not in _CANCELLABLE_ATTEMPT_STATUSES:
            return latest
        raise HTTPException(
            status_code=503 if exc.retryable else 409,
            detail={"code": exc.code, "message": exc.message},
        ) from exc
    if result.status == "already-succeeded":
        try:
            recovered = client.get_attempt(UUID(attempt_id))
            if not isinstance(recovered, ComfyAnnotationResult):
                raise AnnotationPersistenceConflict(
                    "Bridge reported success without returning the exact result"
                )
            current = session.get(AnnotationAttempt, attempt_id)
            if current is None:
                raise HTTPException(
                    status_code=404,
                    detail="Annotation attempt not found",
                )
            _persist_success(session, current, recovered)
            # _persist_success commits the status CAS and immutable revision
            # together, then refreshes the revision. End that follow-up read
            # transaction before the independently retryable history cleanup.
            session.rollback()
            session.expire_all()
            _acknowledge_persisted_terminal(attempt_id, client=client)
        except ComfyAnnotationError as exc:
            session.rollback()
            session.expire_all()
            latest = session.get(AnnotationAttempt, attempt_id)
            if (
                latest is not None
                and latest.status not in _CANCELLABLE_ATTEMPT_STATUSES
            ):
                return latest
            raise HTTPException(
                status_code=503 if exc.retryable else 409,
                detail={"code": exc.code, "message": exc.message},
            ) from exc
        except (AnnotationPersistenceConflict, ValueError) as exc:
            session.rollback()
            session.expire_all()
            latest = session.get(AnnotationAttempt, attempt_id)
            if (
                latest is not None
                and latest.status not in _CANCELLABLE_ATTEMPT_STATUSES
            ):
                return latest
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "annotation_reconciliation_failed",
                    "message": str(exc),
                },
            ) from exc
        session.expire_all()
        persisted = session.get(AnnotationAttempt, attempt_id)
        if persisted is None:  # pragma: no cover - foreign deletion race
            raise HTTPException(status_code=404, detail="Annotation attempt not found")
        return persisted

    if result.status == "already-failed":
        failed = _cas_attempt_status(
            session,
            attempt_id,
            (
                AnnotationAttemptStatus.RUNNING,
                AnnotationAttemptStatus.UNKNOWN,
                AnnotationAttemptStatus.LOST,
            ),
            AnnotationAttemptStatus.FAILED,
            values={
                "active_slot": None,
                "error_code": "comfy_execution_failed",
                "error_message": "ComfyUI reports that this exact prompt failed",
                "retryable": True,
                "finished_at": datetime.utcnow(),
            },
        )
        if failed:
            session.commit()
    elif result.status in {"cancel-requested", "already-cancelled"}:
        cancelled = _cas_attempt_status(
            session,
            attempt_id,
            (
                AnnotationAttemptStatus.RUNNING,
                AnnotationAttemptStatus.UNKNOWN,
                AnnotationAttemptStatus.LOST,
            ),
            AnnotationAttemptStatus.CANCELLED,
            values={
                "active_slot": None,
                "error_code": "annotation_cancelled",
                "error_message": "Cancelled by user",
                "retryable": True,
                "finished_at": datetime.utcnow(),
            },
        )
        if cancelled:
            session.commit()
    # End the transition transaction before authorizing exact history cleanup.
    # This is also safe after a lost CAS: the helper rereads the durable state
    # and only acknowledges a definitive terminal attempt.
    session.rollback()
    session.expire_all()
    _acknowledge_persisted_terminal(attempt_id, client=client)
    session.expire_all()
    persisted = session.get(AnnotationAttempt, attempt_id)
    if persisted is None:  # pragma: no cover - foreign deletion race
        raise HTTPException(status_code=404, detail="Annotation attempt not found")
    return persisted


@router.post(
    "/annotations/{annotation_id}/revisions",
    response_model=MediaAnnotationRead,
    status_code=status.HTTP_201_CREATED,
)
def create_annotation_revision(
    annotation_id: str,
    request: AnnotationRevisionCreate,
    session: Session = Depends(get_session),
) -> MediaAnnotation:
    _ensure_annotation_mutation_allowed()
    parent = session.get(MediaAnnotation, annotation_id)
    if parent is None:
        raise HTTPException(status_code=404, detail="Annotation not found")
    try:
        content = validate_revision_content(parent.kind, request.content)
    except (ValidationError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    parent_id = parent.id
    media_id = parent.media_id
    kind = parent.kind
    source_attempt_id = parent.attempt_id
    for _ in range(5):
        latest_revision = session.exec(
            select(func.max(MediaAnnotation.revision)).where(
                MediaAnnotation.media_id == media_id,
                MediaAnnotation.kind == kind,
            )
        ).one()
        revision = MediaAnnotation(
            media_id=media_id,
            parent_id=parent_id,
            revision=int(latest_revision or 0) + 1,
            kind=kind,
            author=AnnotationAuthor.HUMAN,
            review_status=AnnotationReviewStatus.CANDIDATE,
            content=content,
            provenance={
                "source_annotation_id": parent_id,
                "source_attempt_id": source_attempt_id,
                "edit": "human_revision",
            },
        )
        session.add(revision)
        try:
            safe_commit(session)
        except IntegrityError:
            session.rollback()
            session.expire_all()
            continue
        session.refresh(revision)
        return revision
    raise HTTPException(
        status_code=409,
        detail="Annotation revision changed concurrently; retry the edit.",
    )


@router.post(
    "/annotations/{annotation_id}/approve",
    response_model=MediaAnnotationRead,
)
def approve_annotation(
    annotation_id: str,
    session: Session = Depends(get_session),
) -> MediaAnnotation:
    _ensure_annotation_mutation_allowed()
    annotation = session.get(MediaAnnotation, annotation_id)
    if annotation is None:
        raise HTTPException(status_code=404, detail="Annotation not found")

    target_id = annotation.id
    media_id = annotation.media_id
    kind = annotation.kind
    approval_key = f"{media_id}:{kind.value}"
    for _ in range(5):
        target = session.get(MediaAnnotation, target_id)
        if target is None:
            raise HTTPException(status_code=404, detail="Annotation not found")
        previously_approved = list(
            session.exec(
                select(MediaAnnotation).where(
                    MediaAnnotation.media_id == media_id,
                    MediaAnnotation.kind == kind,
                    MediaAnnotation.review_status
                    == AnnotationReviewStatus.APPROVED,
                    MediaAnnotation.id != target_id,
                )
            ).all()
        )
        for previous in previously_approved:
            previous.review_status = AnnotationReviewStatus.SUPERSEDED
            previous.approved_key = None
            session.add(previous)
        session.flush()

        target.review_status = AnnotationReviewStatus.APPROVED
        target.approved_at = datetime.utcnow()
        target.approved_key = approval_key
        session.add(target)
        try:
            safe_commit(session)
        except IntegrityError:
            session.rollback()
            session.expire_all()
            continue
        session.refresh(target)
        return target
    raise HTTPException(
        status_code=409,
        detail="Annotation approval changed concurrently; retry approval.",
    )
