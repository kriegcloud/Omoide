import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import yaml
from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import HTTPException
from sqlmodel import Session, SQLModel, create_engine

from app.api.datasets import get_training_health, start_training_run
from app.config import TrainingSettings, settings
from app.models import (
    DatasetExport,
    DatasetExportLayout,
    DatasetExportStatus,
    TrainingDataset,
)
from app.schemas.dataset import TrainingRunRequest
from app.services.datasets import _ai_toolkit_config
from app.services.training_presets import PRESETS, apply_preset, launcher_health


class TrainingPresetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.previous_datasets_dir = settings.general.datasets_dir
        self.previous_z_image_path = settings.training.z_image_path
        self.previous_z_image_turbo_path = settings.training.z_image_turbo_path
        self.previous_stale_after = settings.training.launcher_stale_after_seconds
        settings.general.datasets_dir = self.root / "datasets"
        settings.training.z_image_path = None
        settings.training.z_image_turbo_path = None
        settings.training.launcher_stale_after_seconds = 120

    def tearDown(self):
        settings.general.datasets_dir = self.previous_datasets_dir
        settings.training.z_image_path = self.previous_z_image_path
        settings.training.z_image_turbo_path = self.previous_z_image_turbo_path
        settings.training.launcher_stale_after_seconds = self.previous_stale_after
        self.temp.cleanup()

    @staticmethod
    def _config() -> dict:
        return {
            "config": {
                "process": [{
                    "model": {"old": True},
                    "train": {"steps": 321, "keep_train": True},
                    "sample": {"prompts": ["keep me"], "keep_sample": True},
                }]
            }
        }

    def test_apply_each_preset_replaces_model_and_merges_overrides(self):
        for preset_id, preset in PRESETS.items():
            with self.subTest(preset_id=preset_id):
                config = self._config()
                apply_preset(config, preset)
                process = config["config"]["process"][0]
                self.assertNotIn("old", process["model"])
                self.assertEqual(process["model"]["name_or_path"], preset.hub_id)
                self.assertTrue(process["train"]["keep_train"])
                self.assertTrue(process["sample"]["keep_sample"])
                self.assertEqual(process["train"]["steps"], 321)
                self.assertEqual(process["sample"]["prompts"], ["keep me"])
                for key, value in preset.train_overrides.items():
                    self.assertEqual(process["train"][key], value)
                for key, value in preset.sample_overrides.items():
                    self.assertEqual(process["sample"][key], value)
                if preset_id.startswith("z-image"):
                    self.assertNotIn("is_flux", process["model"])

    def test_local_model_path_wins_over_hub_id(self):
        settings.training.z_image_path = "/host/models/z-image.safetensors"
        config = self._config()
        apply_preset(config, PRESETS["z-image"])
        model = config["config"]["process"][0]["model"]
        self.assertEqual(model["name_or_path"], settings.training.z_image_path)

    def test_empty_local_paths_are_none(self):
        training = TrainingSettings(
            z_image_path="",
            z_image_turbo_path="",
        )
        self.assertIsNone(training.z_image_path)
        self.assertIsNone(training.z_image_turbo_path)

    def _heartbeat(self, payload: dict) -> None:
        path = settings.general.resolved_datasets_dir() / ".launcher"
        path.mkdir(parents=True, exist_ok=True)
        (path / "heartbeat.json").write_text(json.dumps(payload), encoding="utf-8")

    def test_health_endpoint_missing_fresh_and_stale(self):
        missing = get_training_health()
        self.assertFalse(missing["launcher_ok"])
        self.assertIsNone(missing["launcher_seen_at"])
        self.assertIsNone(missing["hf_token_configured"])

        fresh_at = datetime.now(timezone.utc).isoformat()
        self._heartbeat({"seen_at": fresh_at, "hf_token_configured": True})
        fresh = launcher_health()
        self.assertTrue(fresh["launcher_ok"])
        self.assertTrue(fresh["hf_token_configured"])

        stale_at = (datetime.now(timezone.utc) - timedelta(seconds=121)).isoformat()
        self._heartbeat({"seen_at": stale_at, "hf_token_configured": False})
        stale = get_training_health()
        self.assertFalse(stale["launcher_ok"])
        self.assertEqual(stale["launcher_seen_at"], stale_at)
        self.assertFalse(stale["hf_token_configured"])

    def test_malformed_heartbeat_is_treated_as_missing(self):
        path = settings.general.resolved_datasets_dir() / ".launcher"
        path.mkdir(parents=True, exist_ok=True)
        (path / "heartbeat.json").write_text("not json", encoding="utf-8")
        self.assertEqual(
            launcher_health(),
            {
                "launcher_seen_at": None,
                "launcher_ok": False,
                "hf_token_configured": None,
                "stale_after_seconds": 120,
            },
        )


class TrainingPresetApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}
        )
        SQLModel.metadata.create_all(self.engine)
        self.previous_datasets_dir = settings.general.datasets_dir
        settings.general.datasets_dir = self.root / "datasets"

    def tearDown(self):
        settings.general.datasets_dir = self.previous_datasets_dir
        self.engine.dispose()
        self.temp.cleanup()

    def _dataset_export(self, session: Session):
        dataset = TrainingDataset(
            name="Ada Portraits",
            slug="ada-portraits",
            trigger_word="adax",
            class_token="woman",
        )
        session.add(dataset)
        session.commit()
        export_dir = self.root / "datasets" / dataset.slug / "export"
        export_dir.mkdir(parents=True)
        (export_dir / "config.yaml").write_text(
            yaml.safe_dump(_ai_toolkit_config(dataset, export_dir), sort_keys=False),
            encoding="utf-8",
        )
        export = DatasetExport(
            dataset_id=dataset.id,
            layout=DatasetExportLayout.AI_TOOLKIT,
            status=DatasetExportStatus.COMPLETED,
            output_dir=str(export_dir),
        )
        session.add(export)
        session.commit()
        session.refresh(dataset)
        session.refresh(export)
        return dataset, export

    def test_unknown_preset_is_422(self):
        with Session(self.engine) as session:
            dataset, export = self._dataset_export(session)
            with self.assertRaises(HTTPException) as raised:
                start_training_run(
                    dataset.id,
                    TrainingRunRequest(export_id=export.id, base_model="unknown"),
                    session,
                )
            self.assertEqual(raised.exception.status_code, 422)

    def test_token_gate_is_409_when_launcher_reports_missing_token(self):
        with Session(self.engine) as session:
            dataset, export = self._dataset_export(session)
            with patch(
                "app.api.datasets.launcher_health",
                return_value={"hf_token_configured": False},
            ):
                with self.assertRaises(HTTPException) as raised:
                    start_training_run(
                        dataset.id,
                        TrainingRunRequest(
                            export_id=export.id,
                            base_model="flux-dev",
                        ),
                        session,
                    )
            self.assertEqual(raised.exception.status_code, 409)

    def test_missing_heartbeat_allows_gated_preset(self):
        with Session(self.engine) as session:
            dataset, export = self._dataset_export(session)
            with patch(
                "app.api.datasets.launcher_health",
                return_value={"hf_token_configured": None},
            ):
                run = start_training_run(
                    dataset.id,
                    TrainingRunRequest(
                        export_id=export.id,
                        base_model="flux-dev",
                    ),
                    session,
                )
            self.assertEqual(run.base_model, "flux-dev")

    def test_migration_has_single_head(self):
        config = Config("alembic.ini")
        script = ScriptDirectory.from_config(config)
        self.assertEqual(script.get_heads(), ["a4b5c6d7e8f9"])


if __name__ == "__main__":
    unittest.main()
