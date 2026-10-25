"""Empty catalogs may start the pinned AdaFace space; legacy state stays fenced."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from sqlmodel import create_engine

from app.config import FaceEmbeddingBackend, settings
from app.processors.faces import FaceProcessor
from app.services.face_inference import FACE_MODEL_FINGERPRINT

MODEL_NAME = "cvlface_adaface_vit_base_kprpe_webface12m"


class FaceModelBootstrapTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        self.addCleanup(self.engine.dispose)
        with self.engine.begin() as connection:
            for table in ("face", "person", "face_embeddings", "person_embeddings"):
                connection.exec_driver_sql(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY, embedding BLOB)")
            connection.exec_driver_sql("CREATE TABLE model_fingerprints (component TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, status TEXT NOT NULL, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
        self.enterContext(patch("app.database.engine", self.engine))
        self.enterContext(patch.object(settings.face_recognition, "embedding_backend", FaceEmbeddingBackend.ADAFACE_KPRPE))
        self.client = Mock()
        self.client.health.return_value = {"ok": True, "protocol": "omoide-ml/v1", "model": MODEL_NAME, "runtime": {"actualCompute": "cpu"}}
        self.factory = self.enterContext(patch("app.services.face_inference.AdaFaceSocketAnalysis", return_value=self.client))
        self.demographics = Mock()
        self.demographics_factory = Mock(return_value=self.demographics)
        self.enterContext(patch.dict("sys.modules", {"insightface.app": SimpleNamespace(FaceAnalysis=self.demographics_factory)}))
        self.processor = FaceProcessor()

    def marker(self):
        with self.engine.connect() as connection:
            return connection.exec_driver_sql("SELECT fingerprint, status FROM model_fingerprints WHERE component = 'face_embeddings'").first()

    def mark(self, fingerprint=FACE_MODEL_FINGERPRINT, status="ready"):
        with self.engine.begin() as connection:
            connection.exec_driver_sql("INSERT INTO model_fingerprints(component, fingerprint, status) VALUES ('face_embeddings', ?, ?)", (fingerprint, status))

    def test_empty_unmarked_catalog_bootstraps_pinned_ready_marker(self):
        self.processor.load_model()
        self.assertEqual(tuple(self.marker()), (FACE_MODEL_FINGERPRINT, "ready"))
        self.assertIs(self.processor.model, self.client)
        self.demographics.prepare.assert_called_once()

    def test_absent_state_table_is_created_only_for_empty_catalog(self):
        with self.engine.begin() as connection:
            connection.exec_driver_sql("DROP TABLE model_fingerprints")
        self.processor.load_model()
        self.assertEqual(tuple(self.marker()), (FACE_MODEL_FINGERPRINT, "ready"))

    def test_any_existing_face_person_or_vector_requires_migration(self):
        for table in ("face", "person", "face_embeddings", "person_embeddings"):
            with self.subTest(table=table):
                with self.engine.begin() as connection:
                    connection.exec_driver_sql(f"INSERT INTO {table}(id, embedding) VALUES (1, x'1234')")
                with self.assertRaisesRegex(RuntimeError, "pinned model migration"):
                    FaceProcessor().load_model()
                self.assertIsNone(self.marker())
                with self.engine.begin() as connection:
                    self.assertEqual(connection.exec_driver_sql(f"SELECT COUNT(*) FROM {table}").scalar_one(), 1)
                    connection.exec_driver_sql(f"DELETE FROM {table}")

    def test_mismatched_or_incomplete_marker_is_never_replaced(self):
        for fingerprint, status in (("legacy-space", "ready"), (FACE_MODEL_FINGERPRINT, "migrating"), (FACE_MODEL_FINGERPRINT, "failed")):
            with self.subTest(fingerprint=fingerprint, status=status):
                self.mark(fingerprint, status)
                with self.assertRaisesRegex(RuntimeError, "pinned model migration"):
                    FaceProcessor().load_model()
                self.assertEqual(tuple(self.marker()), (fingerprint, status))
                with self.engine.begin() as connection:
                    connection.exec_driver_sql("DELETE FROM model_fingerprints")

    def test_matching_ready_marker_allows_populated_catalog(self):
        self.mark()
        with self.engine.begin() as connection:
            connection.exec_driver_sql("INSERT INTO face_embeddings(id, embedding) VALUES (1, x'1234')")
        self.processor.load_model()
        self.assertIs(self.processor.model, self.client)
        self.assertEqual(tuple(self.marker()), (FACE_MODEL_FINGERPRINT, "ready"))

    def test_failed_health_leaves_no_model_or_marker_and_retry_checks_again(self):
        self.client.health.side_effect = [OSError("socket unavailable"), self.client.health.return_value]
        with self.assertRaisesRegex(OSError, "socket unavailable"):
            self.processor.load_model()
        self.assertIsNone(getattr(self.processor, "model", None))
        self.assertIsNone(self.marker())
        self.processor.load_model()
        self.assertEqual(self.client.health.call_count, 2)
        self.assertEqual(tuple(self.marker()), (FACE_MODEL_FINGERPRINT, "ready"))

    def test_failed_demographics_init_leaves_no_model_marker_and_can_retry(self):
        self.demographics.prepare.side_effect = [RuntimeError("prepare failed"), None]
        with self.assertRaisesRegex(RuntimeError, "prepare failed"):
            self.processor.load_model()
        self.assertIsNone(getattr(self.processor, "model", None))
        self.assertIsNone(getattr(self.processor, "demographics_model", None))
        self.assertIsNone(self.marker())
        self.processor.load_model()
        self.assertEqual(self.demographics.prepare.call_count, 2)
        self.assertEqual(tuple(self.marker()), (FACE_MODEL_FINGERPRINT, "ready"))

    def test_existing_ready_catalog_failed_health_still_retries_initialization(self):
        self.mark()
        self.client.health.side_effect = [OSError("socket unavailable"), self.client.health.return_value]
        with self.assertRaises(OSError):
            self.processor.load_model()
        self.assertIsNone(getattr(self.processor, "model", None))
        self.assertEqual(tuple(self.marker()), (FACE_MODEL_FINGERPRINT, "ready"))
        self.processor.load_model()
        self.assertEqual(self.client.health.call_count, 2)
        self.demographics.prepare.assert_called_once()

    def test_existing_ready_catalog_failed_prepare_still_retries_initialization(self):
        self.mark()
        self.demographics.prepare.side_effect = [RuntimeError("prepare failed"), None]
        with self.assertRaisesRegex(RuntimeError, "prepare failed"):
            self.processor.load_model()
        self.assertIsNone(getattr(self.processor, "model", None))
        self.assertIsNone(getattr(self.processor, "demographics_model", None))
        self.processor.load_model()
        self.assertEqual(self.client.health.call_count, 2)
        self.assertEqual(self.demographics.prepare.call_count, 2)

    def test_unmarked_catalog_with_migration_history_stays_fenced(self):
        with self.engine.begin() as connection:
            connection.exec_driver_sql("CREATE TABLE face_embedding_migration (face_id INTEGER PRIMARY KEY, outcome TEXT)")
            connection.exec_driver_sql("INSERT INTO face_embedding_migration VALUES (1, 'migrated')")
        with self.assertRaisesRegex(RuntimeError, "pinned model migration"):
            self.processor.load_model()
        self.assertIsNone(self.marker())
        self.factory.assert_not_called()

    def test_missing_vector_schema_is_not_treated_as_empty(self):
        with self.engine.begin() as connection:
            connection.exec_driver_sql("DROP TABLE person_embeddings")
        with self.assertRaisesRegex(RuntimeError, "pinned model migration"):
            self.processor.load_model()
        self.assertIsNone(self.marker())
        self.factory.assert_not_called()

    def test_ready_initialization_is_reused_without_repeating_model_setup(self):
        self.processor.load_model()
        self.processor.load_model()
        self.client.health.assert_called_once()
        self.demographics.prepare.assert_called_once()

    def test_wrong_service_model_cannot_bootstrap_the_pinned_marker(self):
        self.client.health.return_value["model"] = "different-recognizer"
        with self.assertRaisesRegex(RuntimeError, "model"):
            self.processor.load_model()
        self.assertIsNone(self.marker())
        self.assertIsNone(getattr(self.processor, "model", None))

    def test_final_transaction_rechecks_new_vectors_after_initialization(self):
        def prepare(**kwargs):
            with self.engine.begin() as connection:
                connection.exec_driver_sql("INSERT INTO face_embeddings(id, embedding) VALUES (1, x'1234')")
        self.demographics.prepare.side_effect = prepare
        with self.assertRaisesRegex(RuntimeError, "pinned model migration"):
            self.processor.load_model()
        self.assertIsNone(self.marker())
        self.assertIsNone(getattr(self.processor, "model", None))

    def test_final_transaction_preserves_migration_started_during_initialization(self):
        self.demographics.prepare.side_effect = lambda **kwargs: self.mark(status="migrating")
        with self.assertRaisesRegex(RuntimeError, "pinned model migration"):
            self.processor.load_model()
        self.assertEqual(tuple(self.marker()), (FACE_MODEL_FINGERPRINT, "migrating"))
        self.assertIsNone(getattr(self.processor, "model", None))


if __name__ == "__main__":
    unittest.main()
