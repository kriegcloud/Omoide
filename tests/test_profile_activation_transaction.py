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
_real_assert_no_active_tasks = api._assert_no_active_tasks


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
            def migrate(**kwargs):
                self.assertFalse(dispose.called)
                self.assertIs(database.engine, self.engine)
                self.assertEqual(config.settings.general.data_dir, self.active)
                self.assertEqual(kwargs["database_url"], self.incoming.general.database_url)
                database._set_migration_state(database.MigrationState.APPLIED)
                events.append("migrate")
            def vectors(**kwargs):
                self.assertFalse(dispose.called)
                self.assertIs(database.engine, self.engine)
                self.assertIsNot(kwargs["target_engine"], self.engine)
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
        with patch.object(database, "run_migrations", side_effect=lambda **kwargs:
                          database._set_migration_state(database.MigrationState.APPLIED)),              patch.object(database, "ensure_vec_tables"):
            self.assertIsNone(config.reload_settings())
        self.assertEqual(config.settings.general.data_dir, self.active)
        self.assertTrue(config.settings.scan.auto_scan)
        self.assertEqual(self.bootstrap["active_profile"], str(self.active))
        self.clip.assert_called_once_with()
        self.processors.assert_called_once_with()
        self.onnx.assert_called_once_with()


    def test_ordinary_requests_never_observe_preparing_or_rejected_candidate(self):
        from fastapi.testclient import TestClient
        from sqlmodel import SQLModel
        SQLModel.metadata.create_all(self.engine)
        for stage in ("migration", "vectors"):
            with self.subTest(stage=stage):
                observed = []
                def preparing(**kwargs):
                    candidate = kwargs.get("target_engine")
                    owned = candidate is None and "database_url" in kwargs
                    if owned:
                        candidate = database._make_engine(kwargs["database_url"])
                    if candidate is None:
                        candidate = database.engine
                    try:
                        SQLModel.metadata.create_all(candidate)
                        client = TestClient(main.app, raise_server_exceptions=False, base_url="http://localhost")
                        try:
                            read = client.get("/api/tags/")
                            write = client.post("/api/tags/", json={"name": "during-" + stage})
                            config_read = client.get("/api/config/")
                            config_write = client.post("/api/config/reload")
                        finally:
                            client.close()
                        with self.engine.connect() as connection:
                            active_count = connection.exec_driver_sql("SELECT COUNT(*) FROM tag WHERE name=?", ("during-" + stage,)).scalar_one()
                        with candidate.connect() as connection:
                            candidate_count = connection.exec_driver_sql("SELECT COUNT(*) FROM tag WHERE name=?", ("during-" + stage,)).scalar_one()
                        observed.append((read.status_code, write.status_code, active_count, candidate_count,
                                         config.settings.general.data_dir, database.engine is self.engine,
                                         config_read.status_code, config_write.status_code))
                    finally:
                        if owned:
                            candidate.dispose()
                    raise RuntimeError("fixture preparation failure")
                with patch.object(database, "run_migrations", side_effect=preparing if stage == "migration" else None), patch.object(database, "ensure_vec_tables", side_effect=preparing if stage == "vectors" else None):
                    with self.assertRaises(HTTPException):
                        api.switch_profile(api.SwitchProfileRequest(path=str(self.target)))
                self.assertEqual(observed, [(200, 201, 1, 0, self.active, True, 503, 409)])

    def test_same_database_preparation_marks_health_unavailable_until_ready(self):
        self.incoming.general.data_dir = self.active
        observed = []
        def migrate(**kwargs):
            health = asyncio.run(main.get_health())
            observed.append(getattr(health, "status_code", 200))
            raise RuntimeError("fixture migration refusal")
        with patch.object(main.app.state, "ready", True, create=True), patch.object(main.app.state, "migration_head", "fixture-head", create=True), patch.object(database, "run_migrations", side_effect=migrate), patch.object(database, "ensure_vec_tables"):
            with self.assertRaises(HTTPException):
                api._reload_and_reconfigure()
        self.assertEqual(observed, [503])
        self.assert_retained(schema_ready=False)

    def test_overlapping_switch_is_refused_before_bootstrap_mutation(self):
        nested = False
        secondary_status = []
        def migrate(**kwargs):
            nonlocal nested
            if not nested:
                nested = True
                try:
                    api.switch_profile(api.SwitchProfileRequest(path=str(self.root / "candidate-b")))
                except HTTPException as exc:
                    secondary_status.append(exc.status_code)
        with patch.object(config, "load_settings", side_effect=lambda: self.before.model_copy(deep=True)), patch.object(database, "run_migrations", side_effect=migrate), patch.object(database, "ensure_vec_tables"):
            api.switch_profile(api.SwitchProfileRequest(path=str(self.target)))
        self.assertEqual(secondary_status, [409])
        self.assertEqual(config.settings.general.data_dir, self.target)
        self.assertEqual(self.bootstrap["active_profile"], str(self.target))
        self.writes.assert_called_once()
        self.assertFalse((self.root / "candidate-b").exists())

    def test_new_running_task_during_preparation_aborts_profile_publication(self):
        from sqlmodel import SQLModel, Session
        from app.models import ProcessingTask
        SQLModel.metadata.create_all(self.engine)
        def migrate(**kwargs):
            with Session(self.engine) as session:
                session.add(ProcessingTask(task_type="scan", status="running"))
                session.commit()
        with patch.object(api, "_assert_no_active_tasks", side_effect=_real_assert_no_active_tasks), patch.object(database, "run_migrations", side_effect=migrate), patch.object(database, "ensure_vec_tables"):
            with self.assertRaises(HTTPException) as failure:
                api.switch_profile(api.SwitchProfileRequest(path=str(self.target)))
            self.assertEqual(failure.exception.status_code, 409)
        self.assert_retained()

    def test_same_database_admission_conflict_keeps_successfully_prepared_schema_ready(self):
        self.incoming.general.data_dir = self.active
        from sqlmodel import SQLModel, Session
        from app.models import ProcessingTask
        SQLModel.metadata.create_all(self.engine)
        def migrate(**kwargs):
            with Session(self.engine) as session:
                session.add(ProcessingTask(task_type="scan", status="running"))
                session.commit()
        with patch.object(api, "_assert_no_active_tasks", side_effect=_real_assert_no_active_tasks), patch.object(database, "run_migrations", side_effect=migrate), patch.object(database, "ensure_vec_tables"):
            with self.assertRaises(HTTPException) as failure:
                asyncio.run(api.reload_settings_endpoint())
            self.assertEqual(failure.exception.status_code, 409)
        self.assert_retained(schema_ready=True)

    def test_real_candidate_migration_uses_explicit_url_and_vector_dimensions(self):
        self.incoming.general.database_dir.mkdir(parents=True, exist_ok=True)
        candidate = database._make_engine(self.incoming.general.database_url)
        self.addCleanup(candidate.dispose)
        model = config.ClipModel.VIT_L_14
        database.run_migrations(database_url=self.incoming.general.database_url,
                                clip_embedding_size=model.embedding_size, track_state=False)
        database.ensure_vec_tables(target_engine=candidate, clip_model=model)
        with candidate.connect() as connection:
            self.assertEqual(connection.exec_driver_sql("SELECT COUNT(*) FROM media").scalar_one(), 0)
            for table in ("media_embeddings", "scene_embeddings"):
                definition = connection.exec_driver_sql("SELECT sql FROM sqlite_master WHERE name=?", (table,)).scalar_one()
                self.assertIn("float[768]", definition)
        self.assertIs(database.engine, self.engine)
        self.assertEqual(config.settings.model_dump(), self.before.model_dump())
        self.assertEqual(database.get_migration_state(), database.MigrationState.APPLIED)
        with self.engine.connect() as connection:
            self.assertIsNone(connection.exec_driver_sql("SELECT name FROM sqlite_master WHERE name='media'").first())
            self.assertEqual(connection.exec_driver_sql("SELECT value FROM retained_catalog").scalar_one(), 17)

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
