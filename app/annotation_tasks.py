"""Durable Omoide annotation orchestration outside the eager task package."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any
from uuid import UUID

from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import func, update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

import app.database as db
from app.annotation_coordination import (
    MEDIA_ANNOTATION_MUTATION_LOCK,
    lock_media_annotation_mutation,
)
from app.config import settings
from app.database import safe_commit
from app.logger import logger
from app.models import (
    AnnotationAttempt,
    AnnotationAttemptStatus,
    AnnotationAuthor,
    AnnotationKind,
    AnnotationReviewStatus,
    Media,
    MediaAnnotation,
)
from app.schemas.annotation import (
    AnnotationCaptionContent,
    AnnotationTagsContent,
)
from app.services.comfy_annotation import (
    MAX_IMAGE_DIMENSION,
    MAX_IMAGE_PIXELS,
    ComfyAnnotationClient,
    ComfyAnnotationError,
    ComfyAnnotationResult,
    ComfyAttemptState,
)

_LEASE_HOLDING_STATUSES = (
    AnnotationAttemptStatus.CREATED,
    AnnotationAttemptStatus.RUNNING,
    AnnotationAttemptStatus.LOST,
    AnnotationAttemptStatus.UNKNOWN,
)
_AMBIGUOUS_BRIDGE_ERROR_CODES = {
    "attempt-exists",
    "comfy-http-error",
    "comfy-protocol-error",
    "comfy-unavailable",
    "job-state-unknown",
    "protocol-error",
    "provenance-mismatch",
    "service-failed",
    "service-unavailable",
    "staging-cleanup-failed",
    "submit-unknown",
}
# Exact float32 representations used by the pinned WD executor. The node emits
# these values in provenance so preview and persisted selection share the same
# strict boundary even after JSON expands model scores to Python floats.
_TAG_GENERAL_THRESHOLD = 0.5296000242233276
_TAG_CHARACTER_THRESHOLD = 0.8500000238418579
_TAG_THRESHOLD_COMPARISON = "strictly-greater-than"
_RECOVERY_POLL_SECONDS = 1.0
_RECOVERY_UNKNOWN_GRACE_SECONDS = 30.0
_HISTORY_CLEANUP_LOCK = threading.RLock()
_HISTORY_SUPERVISOR_STATE_LOCK = threading.Lock()
_HISTORY_CLEANUP_STATUSES = (
    AnnotationAttemptStatus.SUCCEEDED,
    AnnotationAttemptStatus.FAILED,
    AnnotationAttemptStatus.CANCELLED,
)
_HISTORY_RETRY_INITIAL_SECONDS = 1.0
_HISTORY_RETRY_MAX_SECONDS = 60.0
_HISTORY_ACK_TIMEOUT_SECONDS = 10.0
_HISTORY_SHUTDOWN_TIMEOUT_SECONDS = _HISTORY_ACK_TIMEOUT_SECONDS + 1.0
_HISTORY_CLEANUP_BATCH_SIZE = 1


class AnnotationBusyError(RuntimeError):
    """Raised when another process won the single-attempt database lease."""


class AnnotationMediaUnavailable(RuntimeError):
    """Raised when media deletion won serialization before attempt admission."""


class AnnotationPersistenceConflict(RuntimeError):
    """Raised when a successful backend result cannot be durably revised."""


class AnnotationResultIdentityError(ValueError):
    """Raised when backend output cannot be proven to belong to its attempt."""


def _cas_attempt_status(
    session: Session,
    attempt_id: str,
    expected_statuses: tuple[AnnotationAttemptStatus, ...],
    target_status: AnnotationAttemptStatus,
    *,
    values: dict[str, Any] | None = None,
) -> bool:
    """Conditionally transition an attempt inside the caller's transaction."""

    transition_values: dict[str, Any] = {"status": target_status}
    if values:
        transition_values.update(values)
    result = session.execute(
        update(AnnotationAttempt)
        .where(
            AnnotationAttempt.id == attempt_id,
            AnnotationAttempt.status.in_(expected_statuses),
        )
        .values(**transition_values)
        .execution_options(synchronize_session=False)
    )
    return result.rowcount == 1


def _claim_annotation_attempt(
    session: Session,
    attempt_id: str,
) -> AnnotationAttempt | None:
    """Atomically claim a created attempt, returning None when another actor won."""

    claimed = _cas_attempt_status(
        session,
        attempt_id,
        (AnnotationAttemptStatus.CREATED,),
        AnnotationAttemptStatus.RUNNING,
        values={"started_at": datetime.utcnow()},
    )
    if not claimed:
        session.rollback()
        session.expire_all()
        return None
    session.commit()
    session.expire_all()
    return session.get(AnnotationAttempt, attempt_id)


def annotation_profile_id(kind: AnnotationKind) -> str:
    if kind == AnnotationKind.CAPTION:
        return settings.annotations.caption_profile_id
    if kind == AnnotationKind.TAGS:
        return settings.annotations.tags_profile_id
    raise ValueError(f"Unsupported annotation kind: {kind}")


def annotation_client() -> ComfyAnnotationClient:
    return ComfyAnnotationClient(
        settings.annotations.inference_socket_path,
        timeout_seconds=settings.annotations.inference_timeout_seconds,
    )


def _history_cleanup_client() -> ComfyAnnotationClient:
    """Create a cleanup-only client with a shutdown-safe socket deadline."""

    return ComfyAnnotationClient(
        settings.annotations.inference_socket_path,
        timeout_seconds=min(
            settings.annotations.inference_timeout_seconds,
            _HISTORY_ACK_TIMEOUT_SECONDS,
        ),
    )


def create_annotation_attempt(
    session: Session,
    *,
    media_id: int,
    kind: AnnotationKind,
    predecessor_attempt_id: str | None = None,
) -> AnnotationAttempt:
    with MEDIA_ANNOTATION_MUTATION_LOCK:
        return _create_annotation_attempt_locked(
            session,
            media_id=media_id,
            kind=kind,
            predecessor_attempt_id=predecessor_attempt_id,
        )


def _create_annotation_attempt_locked(
    session: Session,
    *,
    media_id: int,
    kind: AnnotationKind,
    predecessor_attempt_id: str | None = None,
) -> AnnotationAttempt:
    # Deletion takes the same in-process and database row/write lease. The
    # attempt therefore commits before a delete can check, or a completed
    # delete makes this admission fail without creating an orphan worker.
    if not lock_media_annotation_mutation(session, media_id):
        session.rollback()
        raise AnnotationMediaUnavailable("Media no longer exists")
    attempt = AnnotationAttempt(
        media_id=media_id,
        kind=kind,
        profile_id=annotation_profile_id(kind),
        predecessor_attempt_id=predecessor_attempt_id,
        external_prompt_id="pending",
    )
    attempt.external_prompt_id = attempt.id
    session.add(attempt)
    try:
        safe_commit(session)
    except IntegrityError as exc:
        session.rollback()
        raise AnnotationBusyError("Another annotation attempt is active") from exc
    session.refresh(attempt)
    return attempt


def validate_revision_content(
    kind: AnnotationKind, content: dict[str, Any]
) -> dict[str, Any]:
    if kind == AnnotationKind.CAPTION:
        return AnnotationCaptionContent.model_validate(content).model_dump()
    if kind == AnnotationKind.TAGS:
        validated = AnnotationTagsContent.model_validate(content)
        return validated.model_dump()
    raise ValueError(f"Unsupported annotation kind: {kind}")


def _unwrap_raw_result(raw: Any) -> Any:
    value = raw
    while isinstance(value, list) and len(value) == 1:
        value = value[0]
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("{") and stripped.endswith("}"):
            try:
                return json.loads(stripped)
            except json.JSONDecodeError:
                return value
    return value


def _normalize_result(
    *,
    kind: AnnotationKind,
    profile_id: str,
    raw: Any,
    profile_provenance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    value = _unwrap_raw_result(raw)

    if kind == AnnotationKind.CAPTION:
        text: str | None = None
        if isinstance(value, str):
            text = value
        elif isinstance(value, dict):
            caption = value.get("caption")
            if isinstance(caption, str):
                text = caption
            elif isinstance(caption, dict) and isinstance(
                caption.get("text"), str
            ):
                text = caption["text"]
            elif isinstance(value.get("text"), str):
                text = value["text"]
        if text is None:
            raise ValueError("Caption workflow returned no text")
        content = validate_revision_content(kind, {"text": text.strip()})
    else:
        if not isinstance(value, dict):
            raise ValueError("Tag workflow returned a non-object result")
        if value.get("schema") != "omoide.annotation/v1":
            raise ValueError("Tag workflow returned an unknown schema")
        if value.get("kind") != AnnotationKind.TAGS.value:
            raise ValueError("Tag workflow returned the wrong annotation kind")
        returned_profile = value.get("profile_id")
        if returned_profile not in (None, profile_id):
            raise ValueError("Tag workflow returned the wrong profile")
        tags = value.get("tags")
        if not isinstance(tags, dict):
            raise ValueError("Tag workflow returned no tag namespaces")
        validated_tags = AnnotationTagsContent.model_validate(tags)
        model = value.get("model")
        if not isinstance(model, dict):
            raise ValueError("Tag workflow returned no model provenance")
        if not isinstance(profile_provenance, dict):
            raise ValueError("Tag result has no trusted profile provenance")
        trusted_selection = profile_provenance.get("selection")
        trusted_model = profile_provenance.get("model")
        if not isinstance(trusted_selection, dict) or not isinstance(
            trusted_model, dict
        ):
            raise ValueError(
                "Tag profile provenance is missing selection or model data"
            )
        general_threshold = trusted_selection.get("general_threshold")
        character_threshold = trusted_selection.get("character_threshold")
        comparison = trusted_selection.get("comparison")
        if (
            general_threshold != _TAG_GENERAL_THRESHOLD
            or character_threshold != _TAG_CHARACTER_THRESHOLD
            or comparison != _TAG_THRESHOLD_COMPARISON
        ):
            raise ValueError(
                "Tag profile provenance has an unsupported selection policy"
            )
        trusted_revision = trusted_model.get("revision")
        if not isinstance(trusted_revision, str) or not trusted_revision:
            raise ValueError("Tag profile provenance has no model revision")
        if (
            model.get("general_threshold") != general_threshold
            or model.get("character_threshold") != character_threshold
            or model.get("threshold_comparison") != comparison
            or model.get("revision") != trusted_revision
        ):
            raise ValueError("Tag workflow model provenance does not match its profile")
        selected = AnnotationTagsContent(
            rating=validated_tags.rating,
            general=[
                entry
                for entry in validated_tags.general
                if entry.score > general_threshold
            ],
            character=[
                entry
                for entry in validated_tags.character
                if entry.score > character_threshold
            ],
        )
        content = selected.model_dump()

    normalized = {
        "schema": "omoide.annotation/v1",
        "kind": kind.value,
        "profile_id": profile_id,
        "content": content,
    }
    if kind == AnnotationKind.TAGS:
        normalized["selection"] = {
            "comparison": comparison,
            "general_threshold": general_threshold,
            "character_threshold": character_threshold,
            "raw_counts": {
                "rating": len(validated_tags.rating),
                "general": len(validated_tags.general),
                "character": len(validated_tags.character),
            },
            "selected_counts": {
                "rating": len(selected.rating),
                "general": len(selected.general),
                "character": len(selected.character),
            },
        }
    return normalized


def _raw_result_document(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    return {"value": raw}


def _next_revision(session: Session, media_id: int, kind: AnnotationKind) -> int:
    current = session.exec(
        select(func.max(MediaAnnotation.revision)).where(
            MediaAnnotation.media_id == media_id,
            MediaAnnotation.kind == kind,
        )
    ).one()
    return int(current or 0) + 1


def _persist_success(
    session: Session,
    attempt: AnnotationAttempt,
    result: ComfyAnnotationResult,
) -> MediaAnnotation:
    attempt_id = attempt.id
    media_id = attempt.media_id
    kind = attempt.kind
    profile_id = attempt.profile_id
    expected_id = UUID(attempt_id)
    if result.attempt_id != expected_id or result.prompt_id != expected_id:
        raise AnnotationResultIdentityError(
            "Bridge returned a mismatched attempt identity"
        )
    if result.profile_id != profile_id:
        raise AnnotationResultIdentityError("Bridge returned a mismatched profile")

    normalized = _normalize_result(
        kind=kind,
        profile_id=profile_id,
        raw=result.raw_result,
        profile_provenance=result.profile_provenance,
    )
    provenance: dict[str, Any] = {
        "backend": "comfy",
        "attempt_id": attempt_id,
        "prompt_id": str(result.prompt_id),
        "profile_id": result.profile_id,
        "input_sha256": result.image_sha256,
        "workflow_sha256": result.workflow_sha256,
        "profile": result.profile_provenance,
    }
    if isinstance(normalized.get("selection"), dict):
        provenance["selection"] = normalized["selection"]
    if (
        isinstance(result.profile_provenance, dict)
        and isinstance(result.profile_provenance.get("model"), dict)
    ):
        provenance["model"] = result.profile_provenance["model"]
    raw_value = _unwrap_raw_result(result.raw_result)
    if (
        "model" not in provenance
        and isinstance(raw_value, dict)
        and isinstance(raw_value.get("model"), dict)
    ):
        provenance["model"] = raw_value["model"]

    for _ in range(5):
        existing = session.exec(
            select(MediaAnnotation).where(MediaAnnotation.attempt_id == attempt_id)
        ).first()
        if existing is not None:
            return existing

        annotation = MediaAnnotation(
            media_id=media_id,
            attempt_id=attempt_id,
            revision=_next_revision(session, media_id, kind),
            kind=kind,
            author=AnnotationAuthor.MACHINE,
            review_status=AnnotationReviewStatus.CANDIDATE,
            content=normalized["content"],
            provenance=provenance,
        )
        transitioned = _cas_attempt_status(
            session,
            attempt_id,
            (
                AnnotationAttemptStatus.RUNNING,
                AnnotationAttemptStatus.UNKNOWN,
                AnnotationAttemptStatus.LOST,
            ),
            AnnotationAttemptStatus.SUCCEEDED,
            values={
                "active_slot": None,
                "input_sha256": result.image_sha256,
                "workflow_sha256": result.workflow_sha256,
                "raw_result": _raw_result_document(result.raw_result),
                "normalized_result": normalized,
                "provenance": provenance,
                "error_code": None,
                "error_message": None,
                "retryable": False,
                "finished_at": datetime.utcnow(),
            },
        )
        if not transitioned:
            session.rollback()
            session.expire_all()
            existing = session.exec(
                select(MediaAnnotation).where(MediaAnnotation.attempt_id == attempt_id)
            ).first()
            if existing is not None:
                return existing
            current_attempt = session.get(AnnotationAttempt, attempt_id)
            current_status = (
                current_attempt.status.value
                if current_attempt is not None
                else "missing"
            )
            raise AnnotationPersistenceConflict(
                f"Annotation attempt is no longer running (status={current_status})"
            )
        session.add(annotation)
        try:
            # The status CAS and immutable annotation insert commit together.
            session.commit()
        except IntegrityError:
            session.rollback()
            session.expire_all()
            continue
        session.refresh(annotation)
        return annotation
    raise AnnotationPersistenceConflict(
        "Could not allocate an annotation revision after concurrent writes"
    )


def _acknowledge_persisted_terminal(
    attempt_id: str,
    *,
    client: ComfyAnnotationClient | None = None,
    schedule_retry: bool = True,
) -> bool:
    """Acknowledge history only after a definitive terminal state is committed."""

    with _HISTORY_CLEANUP_LOCK:
        acknowledged = _acknowledge_persisted_terminal_locked(
            attempt_id,
            client=client,
        )
    if not acknowledged and schedule_retry:
        _notify_history_cleanup()
    return acknowledged


def _acknowledge_persisted_terminal_locked(
    attempt_id: str,
    *,
    client: ComfyAnnotationClient | None,
) -> bool:
    """Perform one serialized durable acknowledgement attempt."""

    with Session(db.engine) as session:
        attempt = session.get(AnnotationAttempt, attempt_id)
        if (
            attempt is None
            or attempt.backend != "comfy"
            or attempt.status not in _HISTORY_CLEANUP_STATUSES
        ):
            return False
        if attempt.history_acknowledged_at is not None:
            return True
        terminal_status = attempt.status
        if terminal_status == AnnotationAttemptStatus.SUCCEEDED:
            annotation_id = session.exec(
                select(MediaAnnotation.id).where(
                    MediaAnnotation.attempt_id == attempt_id
                )
            ).first()
            if annotation_id is None:
                return False

    try:
        bridge = client or annotation_client()
        bridge.ack_attempt(attempt_id=UUID(attempt_id))
    except ComfyAnnotationError as exc:
        logger.warning(
            "Committed annotation attempt %s is pending Comfy history cleanup: %s",
            attempt_id,
            exc.message,
        )
        return False
    except Exception:
        logger.exception(
            "Committed annotation attempt %s history acknowledgement failed",
            attempt_id,
        )
        return False

    try:
        with Session(db.engine) as session:
            result = session.execute(
                update(AnnotationAttempt)
                .where(
                    AnnotationAttempt.id == attempt_id,
                    AnnotationAttempt.status == terminal_status,
                    AnnotationAttempt.history_acknowledged_at.is_(None),
                )
                .values(history_acknowledged_at=datetime.utcnow())
                .execution_options(synchronize_session=False)
            )
            if result.rowcount == 1:
                session.commit()
                return True
            session.rollback()
            session.expire_all()
            current = session.get(AnnotationAttempt, attempt_id)
            return bool(
                current is not None
                and current.status == terminal_status
                and current.history_acknowledged_at is not None
            )
    except Exception:
        # Comfy may already have deleted the entry. Keeping this timestamp null
        # makes the next supervisor pass use the idempotent already-absent path.
        logger.exception(
            "Committed annotation attempt %s acknowledgement was not recorded",
            attempt_id,
        )
        return False


def _cleanup_history_acknowledgements() -> bool:
    """Retry committed terminal receipts once, returning whether work remains."""

    if not _HISTORY_CLEANUP_LOCK.acquire(blocking=False):
        return True
    try:
        with Session(db.engine) as session:
            attempt_ids = list(
                session.exec(
                    select(AnnotationAttempt.id)
                    .where(
                        AnnotationAttempt.backend == "comfy",
                        AnnotationAttempt.status.in_(_HISTORY_CLEANUP_STATUSES),
                        AnnotationAttempt.history_acknowledged_at.is_(None),
                    )
                    .order_by(AnnotationAttempt.finished_at.asc())
                    .limit(_HISTORY_CLEANUP_BATCH_SIZE + 1)
                ).all()
            )
        if not attempt_ids:
            return False
        try:
            client = _history_cleanup_client()
        except Exception:
            logger.exception("Comfy history cleanup client could not be created")
            return True
        pending = len(attempt_ids) > _HISTORY_CLEANUP_BATCH_SIZE
        for attempt_id in attempt_ids[:_HISTORY_CLEANUP_BATCH_SIZE]:
            if not _acknowledge_persisted_terminal(
                attempt_id,
                client=client,
                schedule_retry=False,
            ):
                pending = True
        return pending
    finally:
        _HISTORY_CLEANUP_LOCK.release()


def _history_cleanup_supervisor_loop(
    cleanup: Callable[[], bool],
    stop_event: threading.Event,
    wake_event: threading.Event,
    *,
    initial_delay_seconds: float = _HISTORY_RETRY_INITIAL_SECONDS,
    max_delay_seconds: float = _HISTORY_RETRY_MAX_SECONDS,
) -> None:
    """Run one cleanup lane with capped exponential retry and periodic scans."""

    if initial_delay_seconds <= 0 or max_delay_seconds < initial_delay_seconds:
        raise ValueError("history cleanup delays must be positive and ordered")
    retry_delay = initial_delay_seconds
    while not stop_event.is_set():
        # Clear before cleanup so a notification that arrives during the pass
        # remains set and makes the following wait return immediately.
        wake_event.clear()
        try:
            pending = cleanup()
        except Exception:
            logger.exception("Comfy history cleanup supervisor pass failed")
            pending = True
        if stop_event.is_set():
            break

        wait_seconds = retry_delay if pending else max_delay_seconds
        awakened = wake_event.wait(wait_seconds)
        if awakened:
            retry_delay = initial_delay_seconds
        elif pending:
            retry_delay = min(retry_delay * 2, max_delay_seconds)
        else:
            retry_delay = initial_delay_seconds


class _HistoryCleanupSupervisor:
    """Lifecycle owner for the process-local history acknowledgement outbox."""

    def __init__(
        self,
        cleanup: Callable[[], bool] = _cleanup_history_acknowledgements,
        *,
        initial_delay_seconds: float = _HISTORY_RETRY_INITIAL_SECONDS,
        max_delay_seconds: float = _HISTORY_RETRY_MAX_SECONDS,
        shutdown_timeout_seconds: float = _HISTORY_SHUTDOWN_TIMEOUT_SECONDS,
    ) -> None:
        if initial_delay_seconds <= 0 or max_delay_seconds < initial_delay_seconds:
            raise ValueError("history cleanup delays must be positive and ordered")
        if shutdown_timeout_seconds <= 0:
            raise ValueError("history cleanup shutdown timeout must be positive")
        self._cleanup = cleanup
        self._initial_delay_seconds = initial_delay_seconds
        self._max_delay_seconds = max_delay_seconds
        self._shutdown_timeout_seconds = shutdown_timeout_seconds
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._state_lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def start(self) -> bool:
        """Start this supervisor once, or wake its existing worker."""

        with self._state_lock:
            if self._thread is not None and self._thread.is_alive():
                self._wake_event.set()
                return False
            self._stop_event.clear()
            self._wake_event.clear()
            thread = threading.Thread(
                target=self._run,
                daemon=True,
                name="annotation-history-cleanup",
            )
            self._thread = thread
            try:
                thread.start()
            except Exception:
                if self._thread is thread:
                    self._thread = None
                raise
        return True

    def _run(self) -> None:
        _history_cleanup_supervisor_loop(
            self._cleanup,
            self._stop_event,
            self._wake_event,
            initial_delay_seconds=self._initial_delay_seconds,
            max_delay_seconds=self._max_delay_seconds,
        )

    def notify(self) -> None:
        """Wake the worker after a new or failed terminal receipt."""

        self._wake_event.set()

    def stop(self) -> bool:
        """Request shutdown and wait for the bounded cleanup call to return."""

        with self._state_lock:
            thread = self._thread
            if thread is None:
                return True
            self._stop_event.set()
            self._wake_event.set()
        thread.join(timeout=self._shutdown_timeout_seconds)
        stopped = not thread.is_alive()
        if stopped:
            with self._state_lock:
                if self._thread is thread:
                    self._thread = None
        return stopped

    def is_alive(self) -> bool:
        with self._state_lock:
            return self._thread is not None and self._thread.is_alive()


_history_cleanup_supervisor: _HistoryCleanupSupervisor | None = None


def _start_history_cleanup_supervisor() -> None:
    global _history_cleanup_supervisor

    with _HISTORY_SUPERVISOR_STATE_LOCK:
        if _history_cleanup_supervisor is None:
            _history_cleanup_supervisor = _HistoryCleanupSupervisor()
        supervisor = _history_cleanup_supervisor
        supervisor.start()


def _notify_history_cleanup() -> None:
    with _HISTORY_SUPERVISOR_STATE_LOCK:
        supervisor = _history_cleanup_supervisor
    if supervisor is not None:
        supervisor.notify()


def stop_annotation_reconciliation() -> None:
    """Stop the process-local history outbox during application shutdown."""

    global _history_cleanup_supervisor

    with _HISTORY_SUPERVISOR_STATE_LOCK:
        supervisor = _history_cleanup_supervisor
        if supervisor is None:
            return
        stopped = supervisor.stop()
        if stopped and _history_cleanup_supervisor is supervisor:
            _history_cleanup_supervisor = None
    if not stopped:
        logger.warning(
            "Comfy history cleanup supervisor did not stop within %.1f seconds",
            _HISTORY_SHUTDOWN_TIMEOUT_SECONDS,
        )


def _mark_failure(
    attempt_id: str,
    *,
    status: AnnotationAttemptStatus,
    code: str,
    message: str,
    retryable: bool,
) -> None:
    if status not in {
        AnnotationAttemptStatus.FAILED,
        AnnotationAttemptStatus.CANCELLED,
        AnnotationAttemptStatus.LOST,
        AnnotationAttemptStatus.UNKNOWN,
    }:
        raise ValueError("Failure transitions must target a failure terminal state")
    with Session(db.engine) as session:
        transitioned = _cas_attempt_status(
            session,
            attempt_id,
            _LEASE_HOLDING_STATUSES,
            status,
            values={
                "active_slot": (
                    1
                    if status
                    in {
                        AnnotationAttemptStatus.UNKNOWN,
                        AnnotationAttemptStatus.LOST,
                    }
                    else None
                ),
                "error_code": code[:128],
                "error_message": message[:2048],
                "retryable": retryable,
                "finished_at": datetime.utcnow(),
            },
        )
        if transitioned:
            session.commit()
        else:
            session.rollback()
    if status in _HISTORY_CLEANUP_STATUSES:
        # This uses a fresh session, so the terminal transition above is visible
        # before the bridge is authorized to delete the exact history UUID.
        _acknowledge_persisted_terminal(attempt_id)


def _bridge_failure_transition(
    exc: ComfyAnnotationError,
) -> tuple[AnnotationAttemptStatus, str, bool]:
    """Map bridge certainty to a durable state without authorizing a resubmit."""

    if exc.code == "cancelled":
        return (
            AnnotationAttemptStatus.CANCELLED,
            "annotation_cancelled",
            True,
        )
    if exc.code in _AMBIGUOUS_BRIDGE_ERROR_CODES:
        return AnnotationAttemptStatus.UNKNOWN, exc.code, False
    if exc.code == "job-lost":
        return AnnotationAttemptStatus.LOST, exc.code, False
    return AnnotationAttemptStatus.FAILED, exc.code, exc.retryable


def _validate_source_dimensions(source: Image.Image) -> None:
    """Reject hostile image headers before orientation or pixel decode."""

    width, height = source.size
    if width <= 0 or height <= 0:
        raise ValueError("Annotation image dimensions must be positive")
    if width > MAX_IMAGE_DIMENSION or height > MAX_IMAGE_DIMENSION:
        raise ValueError("Annotation image dimensions exceed the supported limit")
    if width * height > MAX_IMAGE_PIXELS:
        raise ValueError("Annotation image pixel count exceeds the supported limit")


def run_annotation_attempt(attempt_id: str) -> None:
    """Run one already-persisted attempt without ever resubmitting its UUID."""

    try:
        with Session(db.engine) as session:
            attempt = _claim_annotation_attempt(session, attempt_id)
            if attempt is None:
                return
            media = session.get(Media, attempt.media_id)
            if media is None:
                raise ValueError("Media no longer exists")
            if media.duration is not None:
                raise ValueError("Video annotation is not supported in v1")
            media_path = media.path
            profile_id = attempt.profile_id

        with Image.open(media_path) as source:
            _validate_source_dimensions(source)
            image = ImageOps.exif_transpose(source)
            client = annotation_client()
            result = client.annotate(
                attempt_id=UUID(attempt_id),
                profile_id=profile_id,
                image=image,
            )

        with Session(db.engine) as session:
            attempt = session.get(AnnotationAttempt, attempt_id)
            if attempt is None:
                return
            _persist_success(session, attempt, result)
        _acknowledge_persisted_terminal(attempt_id, client=client)
    except ComfyAnnotationError as exc:
        failure_status, failure_code, retryable = _bridge_failure_transition(exc)
        _mark_failure(
            attempt_id,
            status=failure_status,
            code=failure_code,
            message=exc.message,
            retryable=retryable,
        )
        if failure_status in {
            AnnotationAttemptStatus.UNKNOWN,
            AnnotationAttemptStatus.LOST,
        }:
            _recover_attempt(attempt_id)
    except AnnotationPersistenceConflict as exc:
        _mark_failure(
            attempt_id,
            status=AnnotationAttemptStatus.UNKNOWN,
            code="annotation_persistence_conflict",
            message=str(exc),
            retryable=False,
        )
        _recover_attempt(attempt_id)
    except AnnotationResultIdentityError as exc:
        _mark_failure(
            attempt_id,
            status=AnnotationAttemptStatus.UNKNOWN,
            code="annotation_result_identity_unknown",
            message=str(exc),
            retryable=False,
        )
        _recover_attempt(attempt_id)
    except (OSError, UnidentifiedImageError, ValueError) as exc:
        _mark_failure(
            attempt_id,
            status=AnnotationAttemptStatus.FAILED,
            code="invalid_annotation_input_or_output",
            message=str(exc),
            retryable=False,
        )
    except Exception as exc:  # pragma: no cover - defensive boundary
        logger.exception("Annotation attempt %s failed", attempt_id)
        _mark_failure(
            attempt_id,
            status=AnnotationAttemptStatus.UNKNOWN,
            code="annotation_internal_error",
            message=str(exc),
            retryable=False,
        )
        _recover_attempt(attempt_id)


def _recover_attempt(attempt_id: str) -> None:
    deadline = time.monotonic() + settings.annotations.inference_timeout_seconds
    unknown_since: float | None = None
    try:
        client = annotation_client()
    except Exception as exc:  # pragma: no cover - validated configuration boundary
        _mark_failure(
            attempt_id,
            status=AnnotationAttemptStatus.UNKNOWN,
            code="annotation_recovery_client_error",
            message=str(exc),
            retryable=False,
        )
        return
    while time.monotonic() < deadline:
        try:
            recovered = client.get_attempt(UUID(attempt_id))
        except ComfyAnnotationError as exc:
            if exc.retryable:
                time.sleep(_RECOVERY_POLL_SECONDS)
                continue
            failure_status, failure_code, retryable = _bridge_failure_transition(exc)
            _mark_failure(
                attempt_id,
                status=failure_status,
                code=failure_code,
                message=exc.message,
                retryable=retryable,
            )
            return
        except Exception as exc:  # pragma: no cover - defensive transport boundary
            logger.exception("Annotation attempt %s recovery lookup failed", attempt_id)
            _mark_failure(
                attempt_id,
                status=AnnotationAttemptStatus.UNKNOWN,
                code="annotation_recovery_lookup_error",
                message=str(exc),
                retryable=False,
            )
            return

        if isinstance(recovered, ComfyAnnotationResult):
            try:
                with Session(db.engine) as session:
                    attempt = session.get(AnnotationAttempt, attempt_id)
                    if attempt is not None:
                        _persist_success(session, attempt, recovered)
                _acknowledge_persisted_terminal(attempt_id, client=client)
            except AnnotationPersistenceConflict as exc:
                _mark_failure(
                    attempt_id,
                    status=AnnotationAttemptStatus.UNKNOWN,
                    code="annotation_persistence_conflict",
                    message=str(exc),
                    retryable=False,
                )
            except AnnotationResultIdentityError as exc:
                _mark_failure(
                    attempt_id,
                    status=AnnotationAttemptStatus.UNKNOWN,
                    code="annotation_result_identity_unknown",
                    message=str(exc),
                    retryable=False,
                )
            except (OSError, UnidentifiedImageError, ValueError) as exc:
                _mark_failure(
                    attempt_id,
                    status=AnnotationAttemptStatus.FAILED,
                    code="invalid_recovered_annotation_result",
                    message=str(exc),
                    retryable=False,
                )
            except Exception as exc:  # pragma: no cover - defensive DB boundary
                logger.exception(
                    "Annotation attempt %s recovery persistence failed",
                    attempt_id,
                )
                _mark_failure(
                    attempt_id,
                    status=AnnotationAttemptStatus.UNKNOWN,
                    code="annotation_recovery_persistence_error",
                    message=str(exc),
                    retryable=False,
                )
            return

        if not isinstance(recovered, ComfyAttemptState):
            _mark_failure(
                attempt_id,
                status=AnnotationAttemptStatus.UNKNOWN,
                code="annotation_recovery_protocol_error",
                message="Bridge recovery returned an unsupported result type",
                retryable=False,
            )
            return

        if recovered.status in {"queued", "running", "output-pending"}:
            unknown_since = None
            time.sleep(_RECOVERY_POLL_SECONDS)
            continue
        if recovered.status == "cancelled":
            _mark_failure(
                attempt_id,
                status=AnnotationAttemptStatus.CANCELLED,
                code="annotation_cancelled",
                message="Annotation was cancelled",
                retryable=True,
            )
            return
        if recovered.status == "failed":
            _mark_failure(
                attempt_id,
                status=AnnotationAttemptStatus.FAILED,
                code=recovered.error_code or "comfy_execution_failed",
                message=recovered.error_message or "ComfyUI execution failed",
                retryable=True,
            )
            return
        if recovered.status == "unknown":
            now = time.monotonic()
            if unknown_since is None:
                unknown_since = now
            grace_deadline = min(
                deadline,
                unknown_since + _RECOVERY_UNKNOWN_GRACE_SECONDS,
            )
            if now < grace_deadline:
                time.sleep(min(_RECOVERY_POLL_SECONDS, grace_deadline - now))
                continue
            _mark_failure(
                attempt_id,
                status=AnnotationAttemptStatus.LOST,
                code="annotation_history_lost",
                message="ComfyUI no longer has queue or history for this attempt",
                retryable=False,
            )
            return

    _mark_failure(
        attempt_id,
        status=AnnotationAttemptStatus.UNKNOWN,
        code="annotation_recovery_timeout",
        message="Timed out while reconciling the ComfyUI attempt",
        retryable=False,
    )


def start_annotation_reconciliation() -> None:
    """Resume in-flight observation and sequential committed-history cleanup."""

    if not settings.annotations.enabled:
        return
    _start_history_cleanup_supervisor()
    with Session(db.engine) as session:
        attempts = list(
            session.exec(
                select(AnnotationAttempt)
                .where(AnnotationAttempt.active_slot == 1)
                .order_by(AnnotationAttempt.created_at.asc())
            ).all()
        )
    for attempt in attempts:
        target = (
            run_annotation_attempt
            if attempt.status == AnnotationAttemptStatus.CREATED
            else _recover_attempt
        )
        threading.Thread(
            target=target,
            args=(attempt.id,),
            daemon=True,
            name=f"annotation-reconcile-{attempt.id[:8]}",
        ).start()
