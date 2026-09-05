import unittest
from datetime import datetime
from types import SimpleNamespace
from uuid import UUID
from unittest.mock import patch

from fastapi import BackgroundTasks, HTTPException
from PIL import Image
from pydantic import ValidationError
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.models import (
    AnnotationAttempt,
    AnnotationAttemptStatus,
    AnnotationAuthor,
    AnnotationKind,
    AnnotationReviewStatus,
    Media,
    MediaAnnotation,
)
from app.services.comfy_annotation import (
    ComfyAckResult,
    ComfyAnnotationError,
    ComfyAnnotationResult,
    ComfyAttemptState,
)
from app.annotation_tasks import (
    AnnotationBusyError,
    AnnotationPersistenceConflict,
    _TAG_CHARACTER_THRESHOLD,
    _TAG_GENERAL_THRESHOLD,
    _bridge_failure_transition,
    _claim_annotation_attempt,
    _cleanup_history_acknowledgements,
    _mark_failure,
    _normalize_result,
    _persist_success,
    _recover_attempt,
    _validate_source_dimensions,
    create_annotation_attempt,
    run_annotation_attempt,
    start_annotation_reconciliation,
    validate_revision_content,
)
from app.api.annotations import (
    approve_annotation,
    cancel_annotation,
    create_annotation_revision,
    retry_annotation,
)
from app.schemas.annotation import (
    MAX_ANNOTATION_TAGS_PER_NAMESPACE,
    AnnotationRevisionCreate,
)
from app.utils import delete_record


class AnnotationPersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SQLModel.metadata.create_all(self.engine)
        with Session(self.engine) as session:
            session.add(
                Media(
                    id=1,
                    path="/fixture/rights-cleared.jpg",
                    filename="rights-cleared.jpg",
                    size=1234,
                )
            )
            session.commit()

    def tearDown(self) -> None:
        self.engine.dispose()

    @staticmethod
    def _caption_result(attempt: AnnotationAttempt) -> ComfyAnnotationResult:
        return ComfyAnnotationResult(
            attempt_id=UUID(attempt.id),
            prompt_id=UUID(attempt.id),
            profile_id=attempt.profile_id,
            image_sha256="a" * 64,
            workflow_sha256="b" * 64,
            raw_result="A person beside a window.",
        )

    @staticmethod
    def _tags_profile_provenance() -> dict:
        return {
            "profile_id": "omoide-tags-v1",
            "workflow_sha256": "b" * 64,
            "model": {
                "revision": "c5303bb7139430db980e4c680a778fe79d72b541"
            },
            "selection": {
                "comparison": "strictly-greater-than",
                "general_threshold": _TAG_GENERAL_THRESHOLD,
                "character_threshold": _TAG_CHARACTER_THRESHOLD,
            },
        }

    def test_tag_result_preserves_namespaces_scores_and_provenance(self) -> None:
        with Session(self.engine) as session:
            attempt = create_annotation_attempt(
                session,
                media_id=1,
                kind=AnnotationKind.TAGS,
            )
            attempt = _claim_annotation_attempt(session, attempt.id)
            self.assertIsNotNone(attempt)
            raw_result = {
                "schema": "omoide.annotation/v1",
                "kind": "tags",
                "profile_id": "omoide-tags-v1",
                "model": {
                    "revision": "c5303bb7139430db980e4c680a778fe79d72b541",
                    "general_threshold": _TAG_GENERAL_THRESHOLD,
                    "character_threshold": _TAG_CHARACTER_THRESHOLD,
                    "threshold_comparison": "strictly-greater-than",
                },
                "tags": {
                    "rating": [{"name": "general", "score": 0.91}],
                    "general": [
                        {"name": "outdoors", "score": 0.88},
                        {"name": "1girl", "score": 0.73},
                        {"name": "low_confidence", "score": 0.20},
                    ],
                    "character": [],
                },
            }
            result = ComfyAnnotationResult(
                attempt_id=UUID(attempt.id),
                prompt_id=UUID(attempt.id),
                profile_id=attempt.profile_id,
                image_sha256="a" * 64,
                workflow_sha256="b" * 64,
                raw_result=raw_result,
                profile_provenance=self._tags_profile_provenance(),
            )

            annotation = _persist_success(session, attempt, result)
            repeated = _persist_success(session, attempt, result)
            persisted_attempt = session.get(AnnotationAttempt, attempt.id)

            self.assertEqual(annotation.id, repeated.id)
            self.assertEqual(annotation.review_status, AnnotationReviewStatus.CANDIDATE)
            self.assertEqual(annotation.content["general"][0]["name"], "outdoors")
            self.assertEqual(annotation.content["general"][0]["score"], 0.88)
            self.assertEqual(len(annotation.content["general"]), 2)
            self.assertEqual(
                persisted_attempt.provenance["selection"]["raw_counts"]["general"],
                3,
            )
            self.assertEqual(
                annotation.provenance["model"]["revision"],
                "c5303bb7139430db980e4c680a778fe79d72b541",
            )
            self.assertEqual(
                persisted_attempt.status, AnnotationAttemptStatus.SUCCEEDED
            )
            self.assertEqual(persisted_attempt.input_sha256, "a" * 64)
            self.assertEqual(
                len(
                    session.exec(
                        select(MediaAnnotation).where(
                            MediaAnnotation.attempt_id == attempt.id
                        )
                    ).all()
                ),
                1,
            )

    def test_history_ack_happens_after_commit_and_pending_ack_is_retried(
        self,
    ) -> None:
        with Session(self.engine) as session:
            attempt = create_annotation_attempt(
                session,
                media_id=1,
                kind=AnnotationKind.CAPTION,
            )
            attempt_id = attempt.id
            result = self._caption_result(attempt)

        observed_committed_revision: list[bool] = []

        def lose_first_ack_response(*, attempt_id: UUID) -> None:
            with Session(self.engine) as verification:
                persisted = verification.get(AnnotationAttempt, str(attempt_id))
                revision = verification.exec(
                    select(MediaAnnotation).where(
                        MediaAnnotation.attempt_id == str(attempt_id)
                    )
                ).first()
                observed_committed_revision.append(
                    persisted is not None
                    and persisted.status == AnnotationAttemptStatus.SUCCEEDED
                    and revision is not None
                )
            raise ComfyAnnotationError(
                "service-unavailable",
                "ack response was lost",
                retryable=True,
            )

        image = Image.new("RGB", (8, 8), "white")
        with (
            patch("app.annotation_tasks.db.engine", self.engine),
            patch("app.annotation_tasks.Image.open", return_value=image),
            patch("app.annotation_tasks.annotation_client") as client_factory,
        ):
            client = client_factory.return_value
            client.annotate.return_value = result
            client.ack_attempt.side_effect = lose_first_ack_response
            run_annotation_attempt(attempt_id)

        self.assertEqual(observed_committed_revision, [True])
        with Session(self.engine) as session:
            persisted = session.get(AnnotationAttempt, attempt_id)
            self.assertEqual(persisted.status, AnnotationAttemptStatus.SUCCEEDED)
            self.assertIsNone(persisted.history_acknowledged_at)

        client.ack_attempt.side_effect = None
        client.ack_attempt.return_value = ComfyAckResult(
            attempt_id=UUID(attempt_id),
            status="already-absent",
        )
        with (
            patch("app.annotation_tasks.db.engine", self.engine),
            patch(
                "app.annotation_tasks._history_cleanup_client",
                return_value=client,
            ),
        ):
            _cleanup_history_acknowledgements()

        with Session(self.engine) as session:
            persisted = session.get(AnnotationAttempt, attempt_id)
            self.assertEqual(persisted.status, AnnotationAttemptStatus.SUCCEEDED)
            self.assertIsNotNone(persisted.history_acknowledged_at)
        self.assertEqual(client.ack_attempt.call_count, 2)

    def test_failed_history_ack_happens_after_commit_and_startup_retries(
        self,
    ) -> None:
        with Session(self.engine) as session:
            created = create_annotation_attempt(
                session,
                media_id=1,
                kind=AnnotationKind.CAPTION,
            )
            claimed = _claim_annotation_attempt(session, created.id)
            attempt_id = claimed.id

        observed_committed_failure: list[bool] = []

        def lose_first_ack_response(*, attempt_id: UUID) -> None:
            with Session(self.engine) as verification:
                persisted = verification.get(AnnotationAttempt, str(attempt_id))
                observed_committed_failure.append(
                    persisted is not None
                    and persisted.status == AnnotationAttemptStatus.FAILED
                    and persisted.active_slot is None
                )
            raise ComfyAnnotationError(
                "service-unavailable",
                "ack response was lost",
                retryable=True,
            )

        with (
            patch("app.annotation_tasks.db.engine", self.engine),
            patch("app.annotation_tasks.annotation_client") as client_factory,
        ):
            client = client_factory.return_value
            client.ack_attempt.side_effect = lose_first_ack_response
            _mark_failure(
                attempt_id,
                status=AnnotationAttemptStatus.FAILED,
                code="execution-failed",
                message="Comfy execution failed",
                retryable=True,
            )

        self.assertEqual(observed_committed_failure, [True])
        with Session(self.engine) as session:
            persisted = session.get(AnnotationAttempt, attempt_id)
            self.assertEqual(persisted.status, AnnotationAttemptStatus.FAILED)
            self.assertIsNone(persisted.history_acknowledged_at)

        client.ack_attempt.side_effect = None
        client.ack_attempt.return_value = ComfyAckResult(
            attempt_id=UUID(attempt_id),
            status="already-absent",
        )
        with (
            patch("app.annotation_tasks.db.engine", self.engine),
            patch(
                "app.annotation_tasks._history_cleanup_client",
                return_value=client,
            ),
        ):
            _cleanup_history_acknowledgements()

        with Session(self.engine) as session:
            persisted = session.get(AnnotationAttempt, attempt_id)
            self.assertEqual(persisted.status, AnnotationAttemptStatus.FAILED)
            self.assertIsNotNone(persisted.history_acknowledged_at)
        self.assertEqual(client.ack_attempt.call_count, 2)

    def test_database_lease_allows_only_one_active_attempt(self) -> None:
        with Session(self.engine) as session:
            first = create_annotation_attempt(
                session,
                media_id=1,
                kind=AnnotationKind.TAGS,
            )
            with self.assertRaises(AnnotationBusyError):
                create_annotation_attempt(
                    session,
                    media_id=1,
                    kind=AnnotationKind.CAPTION,
                )

            first.status = AnnotationAttemptStatus.FAILED
            first.active_slot = None
            session.add(first)
            session.commit()
            second = create_annotation_attempt(
                session,
                media_id=1,
                kind=AnnotationKind.CAPTION,
            )
            self.assertEqual(second.active_slot, 1)

    def test_created_attempt_cancels_without_contacting_bridge(self) -> None:
        with Session(self.engine) as session:
            attempt = create_annotation_attempt(
                session,
                media_id=1,
                kind=AnnotationKind.TAGS,
            )
            with (
                patch("app.api.annotations._ensure_annotation_mutation_allowed"),
                patch("app.api.annotations.annotation_client") as client,
            ):
                cancelled = cancel_annotation(attempt.id, session)

            client.assert_not_called()
            self.assertEqual(cancelled.status, AnnotationAttemptStatus.CANCELLED)
            self.assertIsNone(cancelled.active_slot)
            self.assertIsNotNone(cancelled.history_acknowledged_at)

    def test_cancel_losing_created_cas_to_start_contacts_bridge(self) -> None:
        with Session(self.engine) as session:
            attempt = create_annotation_attempt(
                session,
                media_id=1,
                kind=AnnotationKind.CAPTION,
            )
            attempt_id = attempt.id

        with Session(self.engine, expire_on_commit=False) as cancel_session:
            stale_created = cancel_session.get(AnnotationAttempt, attempt_id)
            self.assertEqual(stale_created.status, AnnotationAttemptStatus.CREATED)
            cancel_session.commit()

            with Session(self.engine) as worker_session:
                claimed = _claim_annotation_attempt(worker_session, attempt_id)
                self.assertEqual(claimed.status, AnnotationAttemptStatus.RUNNING)

            with (
                patch("app.api.annotations._ensure_annotation_mutation_allowed"),
                patch("app.api.annotations.annotation_client") as client_factory,
            ):
                client_factory.return_value.cancel.return_value = SimpleNamespace(
                    status="cancel-requested"
                )
                cancelled = cancel_annotation(attempt_id, cancel_session)

            client_factory.return_value.cancel.assert_called_once_with(
                attempt_id=UUID(attempt_id)
            )
            self.assertEqual(cancelled.status, AnnotationAttemptStatus.CANCELLED)
            self.assertIsNone(cancelled.active_slot)

    def test_cancelled_attempt_cannot_be_claimed_after_cancel_wins(self) -> None:
        with Session(self.engine) as session:
            attempt = create_annotation_attempt(
                session,
                media_id=1,
                kind=AnnotationKind.CAPTION,
            )
            with patch("app.api.annotations._ensure_annotation_mutation_allowed"):
                cancel_annotation(attempt.id, session)

        with Session(self.engine) as worker_session:
            self.assertIsNone(
                _claim_annotation_attempt(worker_session, attempt.id)
            )

    def test_success_winning_cancel_race_is_not_overwritten(self) -> None:
        with Session(self.engine) as session:
            created = create_annotation_attempt(
                session,
                media_id=1,
                kind=AnnotationKind.CAPTION,
            )
            claimed = _claim_annotation_attempt(session, created.id)
            result = self._caption_result(claimed)
            attempt_id = claimed.id

        with Session(self.engine, expire_on_commit=False) as cancel_session:
            stale_running = cancel_session.get(AnnotationAttempt, attempt_id)
            self.assertEqual(stale_running.status, AnnotationAttemptStatus.RUNNING)
            cancel_session.commit()

            with Session(self.engine) as completion_session:
                running = completion_session.get(AnnotationAttempt, attempt_id)
                _persist_success(completion_session, running, result)

            with (
                patch("app.api.annotations._ensure_annotation_mutation_allowed"),
                patch("app.api.annotations.annotation_client") as client_factory,
            ):
                client_factory.return_value.cancel.return_value = SimpleNamespace(
                    status="cancel-requested"
                )
                persisted = cancel_annotation(attempt_id, cancel_session)

            self.assertEqual(persisted.status, AnnotationAttemptStatus.SUCCEEDED)
            self.assertEqual(
                len(
                    cancel_session.exec(
                        select(MediaAnnotation).where(
                            MediaAnnotation.attempt_id == attempt_id
                        )
                    ).all()
                ),
                1,
            )

    def test_cancel_winning_success_race_rolls_back_annotation_insert(self) -> None:
        with Session(self.engine) as session:
            created = create_annotation_attempt(
                session,
                media_id=1,
                kind=AnnotationKind.CAPTION,
            )
            claimed = _claim_annotation_attempt(session, created.id)
            result = self._caption_result(claimed)
            attempt_id = claimed.id

        with Session(self.engine, expire_on_commit=False) as completion_session:
            stale_running = completion_session.get(AnnotationAttempt, attempt_id)
            completion_session.commit()

            with Session(self.engine) as cancel_session:
                with (
                    patch("app.api.annotations._ensure_annotation_mutation_allowed"),
                    patch("app.api.annotations.annotation_client") as client_factory,
                ):
                    client_factory.return_value.cancel.return_value = SimpleNamespace(
                        status="cancel-requested"
                    )
                    cancel_annotation(attempt_id, cancel_session)

            with self.assertRaises(AnnotationPersistenceConflict):
                _persist_success(completion_session, stale_running, result)

            persisted = completion_session.get(AnnotationAttempt, attempt_id)
            self.assertEqual(persisted.status, AnnotationAttemptStatus.CANCELLED)
            self.assertEqual(
                completion_session.exec(
                    select(func.count(MediaAnnotation.id)).where(
                        MediaAnnotation.attempt_id == attempt_id
                    )
                ).one(),
                0,
            )

    def test_retry_accepts_only_resolved_retryable_failure(self) -> None:
        with Session(self.engine) as session:
            previous = create_annotation_attempt(
                session,
                media_id=1,
                kind=AnnotationKind.CAPTION,
            )
            previous.status = AnnotationAttemptStatus.FAILED
            previous.active_slot = None
            previous.retryable = True
            session.add(previous)
            session.commit()

            with patch("app.api.annotations._ensure_annotation_mutation_allowed"):
                retried = retry_annotation(
                    previous.id,
                    BackgroundTasks(),
                    session,
                )

            self.assertEqual(retried.predecessor_attempt_id, previous.id)
            self.assertEqual(retried.status, AnnotationAttemptStatus.CREATED)

    def test_retry_rejects_unresolved_succeeded_and_nonretryable_attempts(self) -> None:
        cases = (
            (AnnotationAttemptStatus.UNKNOWN, True, 1),
            (AnnotationAttemptStatus.LOST, True, 1),
            (AnnotationAttemptStatus.SUCCEEDED, True, None),
            (AnnotationAttemptStatus.FAILED, False, None),
            (AnnotationAttemptStatus.CANCELLED, False, None),
        )
        for attempt_status, retryable, active_slot in cases:
            with self.subTest(status=attempt_status, retryable=retryable):
                with Session(self.engine) as session:
                    previous = AnnotationAttempt(
                        media_id=1,
                        kind=AnnotationKind.CAPTION,
                        profile_id="omoide-caption-v1",
                        external_prompt_id="pending",
                        status=attempt_status,
                        active_slot=active_slot,
                        retryable=retryable,
                    )
                    previous.external_prompt_id = previous.id
                    session.add(previous)
                    session.commit()

                    with (
                        patch(
                            "app.api.annotations._ensure_annotation_mutation_allowed"
                        ),
                        self.assertRaises(HTTPException) as raised,
                    ):
                        retry_annotation(previous.id, BackgroundTasks(), session)

                    self.assertEqual(raised.exception.status_code, 409)
                    self.assertEqual(
                        raised.exception.detail["code"],
                        "annotation_retry_not_allowed",
                    )
                    session.delete(previous)
                    session.commit()

    def test_unresolved_failure_retains_lease_until_bridge_cancel(self) -> None:
        with Session(self.engine) as session:
            attempt = create_annotation_attempt(
                session,
                media_id=1,
                kind=AnnotationKind.CAPTION,
            )
            attempt_id = attempt.id

        with patch("app.annotation_tasks.db.engine", self.engine):
            _mark_failure(
                attempt_id,
                status=AnnotationAttemptStatus.UNKNOWN,
                code="submit-unknown",
                message="delivery was ambiguous",
                retryable=False,
            )

        with Session(self.engine) as session:
            unresolved = session.get(AnnotationAttempt, attempt_id)
            self.assertEqual(unresolved.status, AnnotationAttemptStatus.UNKNOWN)
            self.assertEqual(unresolved.active_slot, 1)
            with self.assertRaises(AnnotationBusyError):
                create_annotation_attempt(
                    session,
                    media_id=1,
                    kind=AnnotationKind.TAGS,
                )

            with (
                patch("app.api.annotations._ensure_annotation_mutation_allowed"),
                patch("app.api.annotations.annotation_client") as client_factory,
            ):
                client_factory.return_value.cancel.return_value = SimpleNamespace(
                    status="cancel-requested"
                )
                cancelled = cancel_annotation(attempt_id, session)

            self.assertEqual(cancelled.status, AnnotationAttemptStatus.CANCELLED)
            self.assertIsNone(cancelled.active_slot)

    def test_cancel_already_succeeded_persists_exact_result(self) -> None:
        with Session(self.engine) as session:
            created = create_annotation_attempt(
                session,
                media_id=1,
                kind=AnnotationKind.CAPTION,
            )
            claimed = _claim_annotation_attempt(session, created.id)
            result = self._caption_result(claimed)
            attempt_id = claimed.id
            ack_observed_committed_revision: list[bool] = []

            def acknowledge(*, attempt_id: UUID) -> ComfyAckResult:
                with Session(self.engine) as verification:
                    durable_attempt = verification.get(
                        AnnotationAttempt,
                        str(attempt_id),
                    )
                    durable_revision = verification.exec(
                        select(MediaAnnotation).where(
                            MediaAnnotation.attempt_id == str(attempt_id)
                        )
                    ).first()
                    ack_observed_committed_revision.append(
                        durable_attempt is not None
                        and durable_attempt.status
                        == AnnotationAttemptStatus.SUCCEEDED
                        and durable_revision is not None
                    )
                return ComfyAckResult(attempt_id=attempt_id, status="deleted")

            with (
                patch("app.api.annotations._ensure_annotation_mutation_allowed"),
                patch("app.api.annotations.annotation_client") as client_factory,
                patch("app.annotation_tasks.db.engine", self.engine),
            ):
                client_factory.return_value.cancel.return_value = SimpleNamespace(
                    status="already-succeeded"
                )
                client_factory.return_value.get_attempt.return_value = result
                client_factory.return_value.ack_attempt.side_effect = acknowledge
                persisted = cancel_annotation(attempt_id, session)

            self.assertEqual(ack_observed_committed_revision, [True])
            self.assertEqual(persisted.status, AnnotationAttemptStatus.SUCCEEDED)
            self.assertIsNone(persisted.active_slot)
            self.assertIsNotNone(persisted.history_acknowledged_at)
            self.assertIsNotNone(
                session.exec(
                    select(MediaAnnotation).where(
                        MediaAnnotation.attempt_id == attempt_id
                    )
                ).first()
            )
            client_factory.return_value.get_attempt.assert_called_once_with(
                UUID(attempt_id)
            )
            client_factory.return_value.ack_attempt.assert_called_once_with(
                attempt_id=UUID(attempt_id)
            )

    def test_cancel_already_failed_releases_as_definitive_failure(self) -> None:
        with Session(self.engine) as session:
            created = create_annotation_attempt(
                session,
                media_id=1,
                kind=AnnotationKind.CAPTION,
            )
            claimed = _claim_annotation_attempt(session, created.id)
            ack_observed_committed_failure: list[bool] = []

            def acknowledge(*, attempt_id: UUID) -> ComfyAckResult:
                with Session(self.engine) as verification:
                    durable_attempt = verification.get(
                        AnnotationAttempt,
                        str(attempt_id),
                    )
                    ack_observed_committed_failure.append(
                        durable_attempt is not None
                        and durable_attempt.status == AnnotationAttemptStatus.FAILED
                        and durable_attempt.active_slot is None
                    )
                return ComfyAckResult(attempt_id=attempt_id, status="deleted")

            with (
                patch("app.api.annotations._ensure_annotation_mutation_allowed"),
                patch("app.api.annotations.annotation_client") as client_factory,
                patch("app.annotation_tasks.db.engine", self.engine),
            ):
                client_factory.return_value.cancel.return_value = SimpleNamespace(
                    status="already-failed"
                )
                client_factory.return_value.ack_attempt.side_effect = acknowledge
                persisted = cancel_annotation(claimed.id, session)

            self.assertEqual(ack_observed_committed_failure, [True])
            self.assertEqual(persisted.status, AnnotationAttemptStatus.FAILED)
            self.assertEqual(persisted.error_code, "comfy_execution_failed")
            self.assertTrue(persisted.retryable)
            self.assertIsNone(persisted.active_slot)
            self.assertIsNotNone(persisted.history_acknowledged_at)

    def test_cancel_pending_history_is_retried_after_worker_settles(self) -> None:
        with Session(self.engine) as session:
            created = create_annotation_attempt(
                session,
                media_id=1,
                kind=AnnotationKind.CAPTION,
            )
            claimed = _claim_annotation_attempt(session, created.id)
            attempt_id = claimed.id

            with (
                patch("app.api.annotations._ensure_annotation_mutation_allowed"),
                patch("app.api.annotations.annotation_client") as client_factory,
                patch("app.annotation_tasks.db.engine", self.engine),
            ):
                client_factory.return_value.cancel.return_value = SimpleNamespace(
                    status="cancel-requested"
                )
                client_factory.return_value.ack_attempt.side_effect = (
                    ComfyAnnotationError(
                        "ack-pending",
                        "the exact attempt is still active",
                        retryable=True,
                    )
                )
                cancelled = cancel_annotation(attempt_id, session)

            self.assertEqual(cancelled.status, AnnotationAttemptStatus.CANCELLED)
            self.assertIsNone(cancelled.history_acknowledged_at)

        with (
            patch("app.annotation_tasks.db.engine", self.engine),
            patch("app.annotation_tasks.annotation_client") as client_factory,
        ):
            client_factory.return_value.ack_attempt.return_value = ComfyAckResult(
                attempt_id=UUID(attempt_id),
                status="deleted",
            )
            _mark_failure(
                attempt_id,
                status=AnnotationAttemptStatus.CANCELLED,
                code="annotation_cancelled",
                message="Annotation was cancelled",
                retryable=True,
            )

        with Session(self.engine) as session:
            persisted = session.get(AnnotationAttempt, attempt_id)
            self.assertEqual(persisted.status, AnnotationAttemptStatus.CANCELLED)
            self.assertIsNotNone(persisted.history_acknowledged_at)
        client_factory.return_value.ack_attempt.assert_called_once_with(
            attempt_id=UUID(attempt_id)
        )

    def test_media_delete_is_blocked_while_annotation_is_active(self) -> None:
        with Session(self.engine) as session:
            attempt = create_annotation_attempt(
                session,
                media_id=1,
                kind=AnnotationKind.TAGS,
            )

            with self.assertRaises(HTTPException) as raised:
                delete_record(1, session)

            self.assertEqual(raised.exception.status_code, 409)
            self.assertEqual(
                raised.exception.detail["attempt_id"],
                attempt.id,
            )
            self.assertIsNotNone(session.get(Media, 1))

    def test_media_delete_cannot_cascade_unacknowledged_success(self) -> None:
        with Session(self.engine) as session:
            created = create_annotation_attempt(
                session,
                media_id=1,
                kind=AnnotationKind.CAPTION,
            )
            claimed = _claim_annotation_attempt(session, created.id)
            _persist_success(session, claimed, self._caption_result(claimed))

            with self.assertRaises(HTTPException) as raised:
                delete_record(1, session)

            self.assertEqual(raised.exception.status_code, 409)
            self.assertEqual(
                raised.exception.detail["code"],
                "annotation_history_cleanup_pending",
            )
            self.assertEqual(
                raised.exception.detail["attempt_id"],
                claimed.id,
            )
            self.assertIsNotNone(session.get(Media, 1))
            self.assertIsNotNone(session.get(AnnotationAttempt, claimed.id))
            self.assertIsNotNone(
                session.exec(
                    select(MediaAnnotation).where(
                        MediaAnnotation.attempt_id == claimed.id
                    )
                ).first()
            )

    def test_media_delete_cannot_cascade_unacknowledged_failure_or_cancel(self) -> None:
        with Session(self.engine) as session:
            for terminal_status in (
                AnnotationAttemptStatus.FAILED,
                AnnotationAttemptStatus.CANCELLED,
            ):
                with self.subTest(status=terminal_status):
                    created = create_annotation_attempt(
                        session,
                        media_id=1,
                        kind=AnnotationKind.CAPTION,
                    )
                    claimed = _claim_annotation_attempt(session, created.id)
                    claimed.status = terminal_status
                    claimed.active_slot = None
                    claimed.finished_at = datetime.utcnow()
                    session.add(claimed)
                    session.commit()

                    with self.assertRaises(HTTPException) as raised:
                        delete_record(1, session)

                    self.assertEqual(raised.exception.status_code, 409)
                    self.assertEqual(
                        raised.exception.detail["code"],
                        "annotation_history_cleanup_pending",
                    )
                    self.assertEqual(
                        raised.exception.detail["attempt_id"],
                        claimed.id,
                    )
                    self.assertIsNotNone(session.get(Media, 1))
                    self.assertIsNotNone(
                        session.get(AnnotationAttempt, claimed.id)
                    )

                    claimed.history_acknowledged_at = datetime.utcnow()
                    session.add(claimed)
                    session.commit()

    def test_model_enforces_unique_revision_numbers(self) -> None:
        with Session(self.engine) as session:
            for name in ("first", "second"):
                session.add(
                    MediaAnnotation(
                        media_id=1,
                        revision=1,
                        kind=AnnotationKind.CAPTION,
                        author="human",
                        content={"text": name},
                    )
                )
            with self.assertRaises(IntegrityError):
                session.commit()
            session.rollback()

    def test_approval_moves_the_unique_approval_key(self) -> None:
        with Session(self.engine) as session:
            first = MediaAnnotation(
                media_id=1,
                revision=1,
                kind=AnnotationKind.CAPTION,
                author="human",
                content={"text": "first"},
            )
            second = MediaAnnotation(
                media_id=1,
                revision=2,
                kind=AnnotationKind.CAPTION,
                author="human",
                content={"text": "second"},
            )
            session.add(first)
            session.add(second)
            session.commit()
            with patch("app.api.annotations._ensure_annotation_mutation_allowed"):
                approve_annotation(first.id, session)
                approved = approve_annotation(second.id, session)

            session.refresh(first)
            self.assertEqual(first.review_status, AnnotationReviewStatus.SUPERSEDED)
            self.assertIsNone(first.approved_key)
            self.assertEqual(approved.review_status, AnnotationReviewStatus.APPROVED)
            self.assertEqual(approved.approved_key, "1:caption")

    def test_tag_result_rejects_wrong_profile(self) -> None:
        with self.assertRaisesRegex(ValueError, "wrong profile"):
            _normalize_result(
                kind=AnnotationKind.TAGS,
                profile_id="omoide-tags-v1",
                raw={
                    "schema": "omoide.annotation/v1",
                    "kind": "tags",
                    "profile_id": "untrusted-profile",
                    "tags": {"rating": [], "general": [], "character": []},
                },
            )

    def test_tag_result_rejects_forged_raw_selection_thresholds(self) -> None:
        with self.assertRaisesRegex(ValueError, "does not match its profile"):
            _normalize_result(
                kind=AnnotationKind.TAGS,
                profile_id="omoide-tags-v1",
                profile_provenance=self._tags_profile_provenance(),
                raw={
                    "schema": "omoide.annotation/v1",
                    "kind": "tags",
                    "profile_id": "omoide-tags-v1",
                    "model": {
                        "revision": (
                            "c5303bb7139430db980e4c680a778fe79d72b541"
                        ),
                        "general_threshold": 0.01,
                        "character_threshold": _TAG_CHARACTER_THRESHOLD,
                        "threshold_comparison": "strictly-greater-than",
                    },
                    "tags": {
                        "rating": [],
                        "general": [{"name": "forged", "score": 0.02}],
                        "character": [],
                    },
                },
            )

    def test_tag_projection_uses_the_exact_emitted_threshold_boundary(self) -> None:
        above_general = 0.5296000838279724
        normalized = _normalize_result(
            kind=AnnotationKind.TAGS,
            profile_id="omoide-tags-v1",
            profile_provenance=self._tags_profile_provenance(),
            raw={
                "schema": "omoide.annotation/v1",
                "kind": "tags",
                "profile_id": "omoide-tags-v1",
                "model": {
                    "revision": "c5303bb7139430db980e4c680a778fe79d72b541",
                    "general_threshold": _TAG_GENERAL_THRESHOLD,
                    "character_threshold": _TAG_CHARACTER_THRESHOLD,
                    "threshold_comparison": "strictly-greater-than",
                },
                "tags": {
                    "rating": [],
                    "general": [
                        {"name": "equal", "score": _TAG_GENERAL_THRESHOLD},
                        {"name": "above", "score": above_general},
                    ],
                    "character": [
                        {"name": "equal_character", "score": _TAG_CHARACTER_THRESHOLD}
                    ],
                },
            },
        )

        self.assertEqual(
            normalized["content"]["general"],
            [{"name": "above", "score": above_general}],
        )
        self.assertEqual(normalized["content"]["character"], [])

    def test_revision_validation_bounds_tag_namespaces(self) -> None:
        with self.assertRaisesRegex(ValidationError, "too_long"):
            validate_revision_content(
                AnnotationKind.TAGS,
                {
                    "rating": [],
                    "general": [
                        {"name": f"tag_{index}", "score": 0.5}
                        for index in range(MAX_ANNOTATION_TAGS_PER_NAMESPACE + 1)
                    ],
                    "character": [],
                },
            )

    def test_revision_api_rejects_duplicate_normalized_tag_names(self) -> None:
        with Session(self.engine) as session:
            parent = MediaAnnotation(
                media_id=1,
                revision=1,
                kind=AnnotationKind.TAGS,
                author=AnnotationAuthor.MACHINE,
                review_status=AnnotationReviewStatus.CANDIDATE,
                content={"rating": [], "general": [], "character": []},
            )
            session.add(parent)
            session.commit()
            session.refresh(parent)

            with (
                patch("app.api.annotations._ensure_annotation_mutation_allowed"),
                self.assertRaises(HTTPException) as raised,
            ):
                create_annotation_revision(
                    parent.id,
                    AnnotationRevisionCreate(
                        content={
                            "rating": [],
                            "general": [
                                {"name": "Blue_Hair", "score": 0.8},
                                {"name": "  blue_hair  ", "score": 0.7},
                            ],
                            "character": [],
                        }
                    ),
                    session,
                )

            self.assertEqual(raised.exception.status_code, 422)
            self.assertIn("duplicate normalized tag name", raised.exception.detail)

    def test_caption_string_normalizes_to_versioned_document(self) -> None:
        normalized = _normalize_result(
            kind=AnnotationKind.CAPTION,
            profile_id="omoide-caption-v1",
            raw=["  A person in a red coat beside a window.  "],
        )

        self.assertEqual(normalized["schema"], "omoide.annotation/v1")
        self.assertEqual(normalized["kind"], "caption")
        self.assertEqual(
            normalized["content"]["text"],
            "A person in a red coat beside a window.",
        )

    def test_revision_validation_rejects_invalid_scores(self) -> None:
        with self.assertRaises(ValidationError):
            validate_revision_content(
                AnnotationKind.TAGS,
                {
                    "rating": [],
                    "general": [{"name": "outdoors", "score": 1.5}],
                    "character": [],
                },
            )

    def test_revision_validation_rejects_unknown_fields(self) -> None:
        with self.assertRaises(ValidationError):
            validate_revision_content(
                AnnotationKind.CAPTION,
                {"text": "A grounded caption.", "prompt": "untrusted"},
            )

    def test_ambiguous_bridge_errors_never_become_retryable_failures(self) -> None:
        for code in (
            "submit-unknown",
            "job-state-unknown",
            "service-unavailable",
            "protocol-error",
            "staging-cleanup-failed",
        ):
            with self.subTest(code=code):
                status, persisted_code, retryable = _bridge_failure_transition(
                    ComfyAnnotationError(code, "ambiguous delivery", retryable=True)
                )
                self.assertEqual(status, AnnotationAttemptStatus.UNKNOWN)
                self.assertEqual(persisted_code, code)
                self.assertFalse(retryable)

        status, _, retryable = _bridge_failure_transition(
            ComfyAnnotationError("job-lost", "history disappeared")
        )
        self.assertEqual(status, AnnotationAttemptStatus.LOST)
        self.assertFalse(retryable)

    def test_run_ambiguity_immediately_enters_recovery_supervision(self) -> None:
        with Session(self.engine) as session:
            attempt = create_annotation_attempt(
                session,
                media_id=1,
                kind=AnnotationKind.CAPTION,
            )
            attempt_id = attempt.id

        image = Image.new("RGB", (8, 8), "white")
        with (
            patch("app.annotation_tasks.db.engine", self.engine),
            patch("app.annotation_tasks.Image.open", return_value=image),
            patch("app.annotation_tasks.annotation_client") as client_factory,
            patch("app.annotation_tasks._recover_attempt") as recover,
        ):
            client_factory.return_value.annotate.side_effect = ComfyAnnotationError(
                "submit-unknown",
                "bridge response was lost",
            )
            run_annotation_attempt(attempt_id)

        recover.assert_called_once_with(attempt_id)
        with Session(self.engine) as session:
            persisted = session.get(AnnotationAttempt, attempt_id)
            self.assertEqual(persisted.status, AnnotationAttemptStatus.UNKNOWN)
            self.assertEqual(persisted.active_slot, 1)

    def test_recovery_mismatched_result_becomes_unresolved_without_stranding_running(
        self,
    ) -> None:
        with Session(self.engine) as session:
            created = create_annotation_attempt(
                session,
                media_id=1,
                kind=AnnotationKind.CAPTION,
            )
            claimed = _claim_annotation_attempt(session, created.id)
            malformed = ComfyAnnotationResult(
                attempt_id=UUID(int=0),
                prompt_id=UUID(int=0),
                profile_id=claimed.profile_id,
                image_sha256="a" * 64,
                workflow_sha256="b" * 64,
                raw_result="A result with the wrong identity.",
            )
            attempt_id = claimed.id

        with (
            patch("app.annotation_tasks.db.engine", self.engine),
            patch("app.annotation_tasks.annotation_client") as client_factory,
        ):
            client_factory.return_value.get_attempt.return_value = malformed
            _recover_attempt(attempt_id)

        with Session(self.engine) as session:
            persisted = session.get(AnnotationAttempt, attempt_id)
            self.assertEqual(persisted.status, AnnotationAttemptStatus.UNKNOWN)
            self.assertEqual(
                persisted.error_code,
                "annotation_result_identity_unknown",
            )
            self.assertEqual(persisted.active_slot, 1)

    def test_recovery_unknown_grace_persists_late_exact_success(self) -> None:
        with Session(self.engine) as session:
            created = create_annotation_attempt(
                session,
                media_id=1,
                kind=AnnotationKind.CAPTION,
            )
            claimed = _claim_annotation_attempt(session, created.id)
            attempt_id = claimed.id
            result = self._caption_result(claimed)

        with patch("app.annotation_tasks.db.engine", self.engine):
            _mark_failure(
                attempt_id,
                status=AnnotationAttemptStatus.UNKNOWN,
                code="service-unavailable",
                message="socket response was lost",
                retryable=False,
            )

        unknown = ComfyAttemptState(
            attempt_id=UUID(attempt_id),
            prompt_id=UUID(attempt_id),
            status="unknown",
            profile_id=claimed.profile_id,
        )
        with (
            patch("app.annotation_tasks.db.engine", self.engine),
            patch("app.annotation_tasks.annotation_client") as client_factory,
            patch("app.annotation_tasks.time.sleep"),
        ):
            client_factory.return_value.get_attempt.side_effect = [unknown, result]
            _recover_attempt(attempt_id)

        with Session(self.engine) as session:
            persisted = session.get(AnnotationAttempt, attempt_id)
            self.assertEqual(persisted.status, AnnotationAttemptStatus.SUCCEEDED)
            self.assertIsNone(persisted.active_slot)
            self.assertEqual(
                session.exec(
                    select(func.count(MediaAnnotation.id)).where(
                        MediaAnnotation.attempt_id == attempt_id
                    )
                ).one(),
                1,
            )
        self.assertEqual(client_factory.return_value.get_attempt.call_count, 2)

    def test_startup_routes_created_to_claim_and_unresolved_to_recovery(self) -> None:
        with Session(self.engine) as session:
            created = create_annotation_attempt(
                session,
                media_id=1,
                kind=AnnotationKind.CAPTION,
            )

        with (
            patch("app.annotation_tasks.db.engine", self.engine),
            patch("app.annotation_tasks.settings.annotations.enabled", True),
            patch(
                "app.annotation_tasks._start_history_cleanup_supervisor"
            ) as start_supervisor,
            patch("app.annotation_tasks.threading.Thread") as thread_type,
        ):
            start_annotation_reconciliation()
            start_supervisor.assert_called_once_with()
            self.assertEqual(
                [
                    call.kwargs["target"].__name__
                    for call in thread_type.call_args_list
                ],
                ["run_annotation_attempt"],
            )
            self.assertEqual(thread_type.return_value.start.call_count, 1)

        with Session(self.engine) as session:
            session.delete(session.get(AnnotationAttempt, created.id))
            session.commit()
            unresolved = AnnotationAttempt(
                media_id=1,
                kind=AnnotationKind.CAPTION,
                profile_id="omoide-caption-v1",
                external_prompt_id="pending",
                status=AnnotationAttemptStatus.UNKNOWN,
                active_slot=1,
                retryable=False,
            )
            unresolved.external_prompt_id = unresolved.id
            session.add(unresolved)
            session.commit()

        with (
            patch("app.annotation_tasks.db.engine", self.engine),
            patch("app.annotation_tasks.settings.annotations.enabled", True),
            patch(
                "app.annotation_tasks._start_history_cleanup_supervisor"
            ) as start_supervisor,
            patch("app.annotation_tasks.threading.Thread") as thread_type,
        ):
            start_annotation_reconciliation()
            start_supervisor.assert_called_once_with()
            self.assertEqual(
                [
                    call.kwargs["target"].__name__
                    for call in thread_type.call_args_list
                ],
                ["_recover_attempt"],
            )
            self.assertEqual(thread_type.return_value.start.call_count, 1)

    def test_source_dimensions_are_checked_before_decode(self) -> None:
        for size in ((0, 100), (16_385, 1), (10_000, 4_001)):
            with self.subTest(size=size), self.assertRaises(ValueError):
                _validate_source_dimensions(SimpleNamespace(size=size))


if __name__ == "__main__":
    unittest.main()
