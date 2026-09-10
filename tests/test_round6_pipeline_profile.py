"""Profile activation and CLIP vector space compatibility regressions."""
import asyncio
import importlib
from pathlib import Path
from unittest.mock import Mock, patch

from sqlalchemy import text

from round6_support import DatabaseCase
from app import config, database

main = importlib.import_module("app.main")
config_api = importlib.import_module("app.api.config")


class ProfileRegressionTests(DatabaseCase):
    def test_disabled_scan_callback_never_opens_database(self):
        with patch.object(config.settings.scan, "auto_scan", False), patch.object(main, "Session") as session:
            main.scheduled_scan_job()
        session.assert_not_called()

    def test_profile_activation_reconfigures_scheduler(self):
        for action in ("switch", "create"):
            with self.subTest(action=action):
                dest = self.root / action
                dest.mkdir()
                with patch.object(config.settings.general, "is_docker", False), patch.object(
                    config_api, "_assert_no_active_tasks"
                ), patch.object(config_api, "read_bootstrap", return_value={}), patch.object(
                    config_api, "write_bootstrap"
                ), patch.object(config_api, "reload_settings"), patch.object(main, "configure_auto_scan_job") as configure:
                    if action == "switch":
                        config_api.switch_profile(config_api.SwitchProfileRequest(path=str(dest)))
                    else:
                        config_api.create_profile(config_api.CreateProfileRequest(path=str(dest)))
                    configure.assert_called_once_with()

    def test_ensure_vector_tables_refuses_existing_wrong_dimensions(self):
        with patch.object(database, "engine", self.engine), patch.object(
            config.settings.ai, "clip_model", config.ClipModel.VIT_L_14
        ):
            with self.assertRaisesRegex(ValueError, "rebuild"):
                database.ensure_vec_tables()
        definition = self.session.exec(text("SELECT sql FROM sqlite_master WHERE name='media_embeddings'")).one()[0]
        self.assertIn("float[512]", definition)

    def test_save_refuses_model_change_before_writing_config(self):
        incoming = config.settings.model_copy(deep=True)
        incoming.ai.clip_model = config.ClipModel.VIT_L_14
        with patch.object(database, "engine", self.engine), patch.object(
            config.settings.ai, "clip_model", config.ClipModel.VIT_B_32
        ), patch.object(config, "get_user_data_path", return_value=self.root):
            with self.assertRaisesRegex(ValueError, "rebuild"):
                config.save_settings(incoming)
        self.assertFalse((self.root / "config.yaml").exists())

    def test_same_dimension_model_change_rejected_with_existing_vectors(self):
        incoming = config.settings.model_copy(deep=True)
        incoming.ai.clip_model = config.ClipModel.ROBERTA_BASE_VIT_B_32
        self.session.exec(text("INSERT INTO media_embeddings VALUES (1, zeroblob(2048))"))
        self.session.commit()
        with patch.object(database, "engine", self.engine), patch.object(
            config.settings.ai, "clip_model", config.ClipModel.VIT_B_32
        ), patch.object(config, "get_user_data_path", return_value=self.root):
            with self.assertRaisesRegex(ValueError, "rebuild"):
                config.save_settings(incoming)

    def test_reload_rejects_dimension_change_before_mutating_runtime_settings(self):
        incoming = config.settings.model_copy(deep=True)
        incoming.ai.clip_model = config.ClipModel.VIT_L_14
        with patch.object(database, "engine", self.engine), patch.object(
            config.settings.ai, "clip_model", config.ClipModel.VIT_B_32
        ), patch.object(config, "load_settings", return_value=incoming), patch.object(
            config, "get_user_data_path", return_value=config.settings.general.data_dir
        ), patch.object(database, "reset_engine") as reset, patch.object(database, "run_migrations"), patch.object(
            config, "_reset_clip_after_settings_change"
        ), patch("app.processor_registry.reset_processors"):
            with self.assertRaisesRegex(ValueError, "rebuild"):
                config.reload_settings()
            self.assertEqual(config.settings.ai.clip_model, config.ClipModel.VIT_B_32)
            reset.assert_not_called()

    def test_save_guard_uses_active_profile_even_if_payload_has_another_data_dir(self):
        incoming = config.settings.model_copy(deep=True)
        incoming.ai.clip_model = config.ClipModel.VIT_L_14
        incoming.general.data_dir = self.root / "ignored-payload-profile"
        with patch.object(database, "engine", self.engine), patch.object(
            config.settings.ai, "clip_model", config.ClipModel.VIT_B_32
        ), patch.object(config, "get_user_data_path", return_value=self.root):
            with self.assertRaisesRegex(ValueError, "rebuild"):
                config.save_settings(incoming)

    def test_failed_profile_validation_restores_bootstrap_identity(self):
        before = {"active_profile": "previous", "profiles": []}
        dest = self.root / "bad-profile"
        with patch.object(config.settings.general, "is_docker", False), patch.object(
            config_api, "_assert_no_active_tasks"
        ), patch.object(config_api, "read_bootstrap", return_value=before.copy()), patch.object(
            config_api, "write_bootstrap"
        ) as write, patch.object(config_api, "reload_settings", side_effect=ValueError("vector rebuild required")):
            with self.assertRaises(Exception):
                config_api.switch_profile(config_api.SwitchProfileRequest(path=str(dest)))
        self.assertEqual(write.call_args.args[0]["active_profile"], "previous")

    def test_scheduled_pipeline_task_persists_chain_intent_for_resume(self):
        from app.models import ProcessingTask
        from sqlmodel import select
        with patch.object(config.settings.scan, "auto_scan", True), patch.object(database, "engine", self.engine), patch(
            "app.tasks.pipeline.run_cleanup_and_chain"
        ) as start:
            main.scheduled_scan_job()
        task = self.session.exec(select(ProcessingTask)).one()
        self.assertEqual(task.params, {"chain": True})
        start.assert_called_once_with(task.id)
