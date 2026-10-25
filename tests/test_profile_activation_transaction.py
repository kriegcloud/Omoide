"""A rejected profile activation retains the previously usable runtime identity."""
import asyncio
import importlib
import os
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock, patch

_CONFIG = tempfile.TemporaryDirectory(prefix="omoide-profile-activation-tests-")
os.environ["XDG_CONFIG_HOME"] = _CONFIG.name

import app.api  # noqa: E402,F401
from fastapi import HTTPException  # noqa: E402
from sqlalchemy import text  # noqa: E402
from app import config, database  # noqa: E402

api = importlib.import_module("app.api.config")
main = importlib.import_module("app.main")


class ProfileActivationTransactionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="omoide-profile-activation-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.active = self.root / "active"
        self.target = self.root / "candidate"
        self.enterContext(patch.object(config.settings.general, "data_dir", self.active))
        self.enterContext(patch.object(config.settings.general, "is_docker", False))
        self.enterContext(patch.object(config.settings.scan, "auto_scan", False))
        config.settings.general.database_dir.mkdir(parents=True)
        self.engine = database._make_engine(config.settings.general.database_url)
        with self.engine.begin() as connection:
            connection.execute(text("CREATE TABLE retained_catalog(value INTEGER)"))
            connection.execute(text("INSERT INTO retained_catalog VALUES(17)"))
        self.enterContext(patch.object(database, "engine", self.engine))
        self.enterContext(patch.object(database, "_migration_state", database.MigrationState.APPLIED))
        self.before = config.settings.model_copy(deep=True)
        self.incoming = config.settings.model_copy(deep=True)
        self.incoming.general.data_dir = self.target
        self.incoming.scan.auto_scan = True
        self.bootstrap = {"active_profile": str(self.active), "profiles": [{"name": "Active", "path": str(self.active)}]}
        self.enterContext(patch.object(api, "read_bootstrap", side_effect=lambda: deepcopy(self.bootstrap)))
        self.writes = self.enterContext(patch.object(api, "write_bootstrap", side_effect=self.write_bootstrap))
        self.enterContext(patch.object(api, "_assert_no_active_tasks"))
        self.enterContext(patch.object(config, "load_settings", return_value=self.incoming))
        self.enterContext(patch.object(config, "get_user_data_path", side_effect=lambda: Path(self.bootstrap["active_profile"])))
        self.scheduler = self.enterContext(patch.object(main, "configure_auto_scan_job"))
        self.clip = self.enterContext(patch.object(config, "_reset_clip_after_settings_change"))
        self.processors = self.enterContext(patch("app.processor_registry.reset_processors"))
        self.onnx = self.enterContext(patch("app.clip_onnx.reset_clip_encoder"))
        self.enterContext(patch.object(config, "configure_file_logging"))

    def tearDown(self):
        if database.engine is not self.engine:
            database.engine.dispose()
        self.engine.dispose()

    def write_bootstrap(self, value):
        self.bootstrap = deepcopy(value)

    def assert_retained(self, *, schema_ready=True):
        self.assertEqual(self.bootstrap["active_profile"], str(self.active))
        self.assertEqual(config.settings.model_dump(), self.before.model_dump())
        self.assertIs(database.engine, self.engine)
        self.assertEqual(database.get_migration_state(),
                         database.MigrationState.APPLIED if schema_ready else database.MigrationState.FAILED)
        with patch.object(main.app.state, "ready", True, create=True),              patch.object(main.app.state, "migration_head", "fixture-head", create=True):
            health = asyncio.run(main.get_health())
            if schema_ready:
                self.assertEqual(health["status"], "ok")
            else:
                self.assertEqual(health.status_code, 503)
        with self.engine.connect() as connection:
            self.assertEqual(connection.execute(text("SELECT value FROM retained_catalog")).scalar(), 17)
        self.scheduler.assert_not_called()
        self.clip.assert_not_called()
        self.processors.assert_not_called()
        self.onnx.assert_not_called()

    def test_switch_migration_failure_restores_identity(self):
        with patch.object(database, "run_migrations", side_effect=RuntimeError("fixture migration refusal")), \
             patch.object(database, "ensure_vec_tables") as vectors:
            with self.assertRaises(HTTPException) as error:
                api.switch_profile(api.SwitchProfileRequest(path=str(self.target)))
            self.assertEqual(error.exception.status_code, 409)
            vectors.assert_not_called()
        self.assert_retained()

    def test_create_vector_preparation_failure_restores_identity(self):
        with patch.object(database, "run_migrations"), \
             patch.object(database, "ensure_vec_tables", side_effect=RuntimeError("fixture vector failure")):
            with self.assertRaises(HTTPException) as error:
                api.create_profile(api.CreateProfileRequest(path=str(self.target)))
            self.assertEqual(error.exception.status_code, 409)
        self.assert_retained()

    def test_profile_load_failure_restores_bootstrap_without_touching_engine(self):
        with patch.object(config, "load_settings", side_effect=OSError("fixture config read failure")):
            with self.assertRaises(HTTPException) as error:
                api.switch_profile(api.SwitchProfileRequest(path=str(self.target)))
            self.assertEqual(error.exception.status_code, 409)
        self.assert_retained()

    def test_same_profile_reload_failure_retains_runtime_and_skips_scheduler(self):
        self.incoming.general.data_dir = self.active
        with patch.object(database, "run_migrations", side_effect=RuntimeError("fixture migration refusal")), \
             patch.object(database, "ensure_vec_tables"):
            with self.assertRaises(HTTPException) as error:
                asyncio.run(api.reload_settings_endpoint())
            self.assertEqual(error.exception.status_code, 409)
        self.assert_retained(schema_ready=False)

    def test_successful_switch_retains_old_engine_until_schema_is_ready(self):
        events = []
        with patch.object(self.engine, "dispose", wraps=self.engine.dispose) as dispose:
            def migrate():
                self.assertFalse(dispose.called)
                self.assertIsNot(database.engine, self.engine)
                database._set_migration_state(database.MigrationState.APPLIED)
                events.append("migrate")
            def vectors():
                self.assertFalse(dispose.called)
                events.append("vectors")
            with patch.object(database, "run_migrations", side_effect=migrate), \
                 patch.object(database, "ensure_vec_tables", side_effect=vectors):
                self.assertIsNone(api.switch_profile(api.SwitchProfileRequest(path=str(self.target))))
            dispose.assert_called_once_with()
        self.assertEqual(events, ["migrate", "vectors"])
        self.assertEqual(config.settings.general.data_dir, self.target)
        self.assertEqual(self.bootstrap["active_profile"], str(self.target))
        self.scheduler.assert_called_once_with()
        self.clip.assert_called_once_with()
        self.processors.assert_called_once_with()
        self.onnx.assert_called_once_with()

    def test_successful_same_profile_reload_keeps_public_return_contract(self):
        self.incoming.general.data_dir = self.active
        with patch.object(database, "run_migrations", side_effect=lambda:
                          database._set_migration_state(database.MigrationState.APPLIED)),              patch.object(database, "ensure_vec_tables"):
            self.assertIsNone(config.reload_settings())
        self.assertEqual(config.settings.general.data_dir, self.active)
        self.assertTrue(config.settings.scan.auto_scan)
        self.assertEqual(self.bootstrap["active_profile"], str(self.active))
        self.clip.assert_called_once_with()
        self.processors.assert_called_once_with()
        self.onnx.assert_called_once_with()


    def test_candidate_disposal_failure_does_not_mask_controlled_activation_failure(self):
        candidate = Mock(url=self.engine.url.set(database=str(self.target / "database" / "omoide.db")))
        candidate.dispose.side_effect = RuntimeError("fixture dispose failure")
        with patch.object(database, "_make_engine", return_value=candidate),              patch.object(database, "run_migrations", side_effect=RuntimeError("fixture migration refusal")):
            with self.assertRaises(HTTPException) as error:
                api.switch_profile(api.SwitchProfileRequest(path=str(self.target)))
            self.assertEqual(error.exception.status_code, 409)
        self.assert_retained()

    def test_engine_creation_failure_leaves_old_runtime_healthy(self):
        with patch.object(database, "_make_engine", side_effect=OSError("fixture connection creation failure")):
            with self.assertRaises(HTTPException) as error:
                api.switch_profile(api.SwitchProfileRequest(path=str(self.target)))
            self.assertEqual(error.exception.status_code, 409)
        self.assert_retained()
