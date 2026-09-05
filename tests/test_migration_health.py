import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi import HTTPException

import app.database as database
from app.api.annotations import (
    annotation_health,
    get_annotation_attempt,
    get_media_annotations,
)


class MigrationStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.previous_state = database.get_migration_state()

    def tearDown(self) -> None:
        database._set_migration_state(self.previous_state)

    def test_alembic_failure_is_recorded_and_propagated(self) -> None:
        database._set_migration_state(database.MigrationState.NOT_ATTEMPTED)

        with (
            patch(
                "alembic.command.upgrade",
                side_effect=RuntimeError("private database path"),
            ),
            patch.object(database, "logger") as logger,
        ):
            with self.assertRaisesRegex(RuntimeError, "private database path"):
                database.run_migrations()

        self.assertEqual(
            database.get_migration_state(),
            database.MigrationState.FAILED,
        )
        logger.info.assert_not_called()
        logger.error.assert_called_once()

    def test_successful_alembic_upgrade_marks_schema_applied(self) -> None:
        database._set_migration_state(database.MigrationState.NOT_ATTEMPTED)

        with (
            patch("alembic.command.upgrade") as upgrade,
            patch.object(database, "logger") as logger,
        ):
            database.run_migrations()

        upgrade.assert_called_once()
        self.assertEqual(
            database.get_migration_state(),
            database.MigrationState.APPLIED,
        )
        logger.error.assert_not_called()
        logger.info.assert_called_once_with(
            "Alembic migrations applied successfully."
        )

    def test_main_does_not_keep_success_flag_when_retry_fails(self) -> None:
        import app.main as main

        previous_applied = main._migrations_applied
        try:
            main._migrations_applied = True
            database._set_migration_state(database.MigrationState.FAILED)

            with patch.object(
                database,
                "run_migrations",
                side_effect=RuntimeError("migration failed"),
            ):
                with self.assertRaisesRegex(RuntimeError, "migration failed"):
                    main._apply_migrations_once()

            self.assertFalse(main._migrations_applied)
        finally:
            main._migrations_applied = previous_applied


class AnnotationMigrationHealthTests(unittest.TestCase):
    def test_failed_migration_returns_degraded_health_without_queries(self) -> None:
        session = Mock()

        with (
            patch("app.api.annotations.settings.annotations.enabled", True),
            patch(
                "app.api.annotations.get_migration_state",
                return_value=database.MigrationState.FAILED,
            ),
            patch("app.api.annotations.annotation_client") as client,
        ):
            health = annotation_health(session)

        self.assertTrue(health.enabled)
        self.assertFalse(health.ready)
        self.assertEqual(
            health.detail,
            "Annotation database migration unavailable",
        )
        self.assertNotIn("path", health.detail.lower())
        session.exec.assert_not_called()
        client.assert_not_called()

    def test_failed_migration_rejects_annotation_reads_before_queries(self) -> None:
        session = Mock()

        with patch(
            "app.api.annotations.get_migration_state",
            return_value=database.MigrationState.FAILED,
        ):
            with self.assertRaises(HTTPException) as media_error:
                get_media_annotations(1, session=session)
            with self.assertRaises(HTTPException) as attempt_error:
                get_annotation_attempt("attempt-id", session=session)

        self.assertEqual(media_error.exception.status_code, 503)
        self.assertEqual(attempt_error.exception.status_code, 503)
        self.assertEqual(
            media_error.exception.detail["code"],
            "annotation_schema_unavailable",
        )
        session.get.assert_not_called()
        session.exec.assert_not_called()

    def test_applied_migration_preserves_ready_health_behavior(self) -> None:
        session = Mock()
        session.exec.return_value.first.return_value = None
        bridge_health = SimpleNamespace(
            ready=True,
            profiles=("omoide-caption-v1", "omoide-tags-v1"),
            configured_profiles=("omoide-caption-v1", "omoide-tags-v1"),
            unavailable_profiles={},
            active_attempt_id=None,
        )

        with (
            patch("app.api.annotations.settings.annotations.enabled", True),
            patch(
                "app.api.annotations.get_migration_state",
                return_value=database.MigrationState.APPLIED,
            ),
            patch("app.api.annotations.annotation_client") as client,
        ):
            client.return_value.health.return_value = bridge_health
            health = annotation_health(session)

        self.assertTrue(health.enabled)
        self.assertTrue(health.ready)
        self.assertEqual(
            health.profiles,
            ["omoide-caption-v1", "omoide-tags-v1"],
        )
        session.exec.assert_called_once()
        client.assert_called_once()


if __name__ == "__main__":
    unittest.main()
