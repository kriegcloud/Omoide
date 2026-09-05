import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import yaml
from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import BackgroundTasks, HTTPException
from PIL import Image
from sqlmodel import Session, SQLModel, create_engine, select

from app.api.datasets import get_eval_sample_image, start_eval_batch
from app.config import settings
from app.models import DatasetExport, DatasetExportLayout, DatasetExportStatus, EvalBatch, EvalSample, Status, TrainingDataset, TrainingRun, TrainingRunStatus
from app.schemas.dataset import EvalBatchRequest
from app.tasks.eval_batch import run_eval_batch


class FakeEvalClient:
    def generate(self, _attempt_id, _profile_id, _params):
        data = io.BytesIO()
        Image.new("RGB", (32, 32), "green").save(data, "PNG")
        return SimpleNamespace(image=data.getvalue())


class EvalKitTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
        SQLModel.metadata.create_all(self.engine)
        self.old_enabled = settings.repairs.enabled
        self.old_presentation = settings.general.presentation_mode
        self.old_datasets = settings.general.datasets_dir
        self.old_host_root = settings.general.datasets_host_root
        settings.repairs.enabled = True
        settings.general.presentation_mode = False
        settings.general.datasets_dir = self.root / "datasets"
        settings.general.datasets_host_root = None

    def tearDown(self):
        settings.repairs.enabled = self.old_enabled
        settings.general.presentation_mode = self.old_presentation
        settings.general.datasets_dir = self.old_datasets
        settings.general.datasets_host_root = self.old_host_root
        self.engine.dispose()
        self.temporary.cleanup()

    def _run(self, session: Session):
        dataset = TrainingDataset(name="Portraits", slug="portraits", person_id=7, trigger_word="subject", class_token="person")
        session.add(dataset)
        session.commit()
        export = DatasetExport(dataset_id=dataset.id, layout=DatasetExportLayout.AI_TOOLKIT, status=DatasetExportStatus.COMPLETED, output_dir=str(self.root))
        session.add(export)
        session.commit()
        run_dir = self.root / "datasets" / "portraits" / "run"
        output = run_dir / "output"
        output.mkdir(parents=True)
        checkpoint = output / "portraits.safetensors"
        checkpoint.write_bytes(b"checkpoint")
        config = {"config": {"process": [{"sample": {"prompts": ["subject indoors", "subject outdoors"], "sample_steps": 9, "guidance_scale": 1}, "network": {"linear": 16}, "train": {"lr": 0.0001}}]}}
        run = TrainingRun(dataset_id=dataset.id, export_id=export.id, status=TrainingRunStatus.COMPLETED, run_dir=str(run_dir), config_yaml=yaml.safe_dump(config), steps=100)
        session.add(run)
        session.commit()
        session.refresh(run)
        return dataset, run, checkpoint

    def test_batch_creation_uses_run_prompts_seed_and_checkpoint_defaults(self):
        with Session(self.engine) as session, patch("app.api.datasets.eval_client", return_value=SimpleNamespace(health=lambda: SimpleNamespace(profiles=(settings.repairs.eval_profile_id,)))):
            _, run, checkpoint = self._run(session)
            response = start_eval_batch(run.id, EvalBatchRequest(), BackgroundTasks(), session)
            self.assertEqual(response.prompts, ["subject indoors", "subject outdoors"])
            self.assertEqual(response.seeds, [1, 2, 3, 4])
            self.assertEqual(response.lora_strength, 1.0)
            self.assertEqual(response.checkpoint_path, str(checkpoint.resolve()))

    def test_task_saves_pngs_and_scores_every_cell(self):
        with Session(self.engine) as session:
            dataset, run, checkpoint = self._run(session)
            batch = EvalBatch(run_id=run.id, checkpoint_path=str(checkpoint), lora_path=str(checkpoint), prompts=["one", "two"], seeds=[1, 2], lora_strength=0.8)
            session.add(batch)
            session.commit()
            batch_id = batch.id
        with patch("app.tasks.eval_batch.db.engine", self.engine), patch("app.tasks.eval_batch.eval_client", return_value=FakeEvalClient()), patch("app.tasks.eval_batch.score_path", return_value=(0.75, 1, [0, 0, 10, 10])) as scorer:
            run_eval_batch(batch_id)
        with Session(self.engine) as session:
            batch = session.get(EvalBatch, batch_id)
            samples = session.exec(select(EvalSample).where(EvalSample.batch_id == batch_id)).all()
            self.assertEqual(batch.status, Status.COMPLETED)
            self.assertEqual(len(samples), 4)
            self.assertEqual(scorer.call_count, 4)
            self.assertTrue(all(Path(sample.path).is_file() and Path(sample.path).suffix == ".png" for sample in samples))
            self.assertTrue(all(sample.likeness == 0.75 and sample.attempt_id for sample in samples))

    def test_image_route_refuses_path_outside_run_directory(self):
        with Session(self.engine) as session:
            _, run, checkpoint = self._run(session)
            batch = EvalBatch(run_id=run.id, checkpoint_path=str(checkpoint), lora_path=str(checkpoint), prompts=["one"], seeds=[1], lora_strength=1)
            session.add(batch)
            session.commit()
            outside = self.root / "outside.png"
            outside.write_bytes(b"outside")
            sample = EvalSample(batch_id=batch.id, prompt_index=0, seed=1, path=str(outside), attempt_id="00000000-0000-4000-8000-000000000001")
            session.add(sample)
            session.commit()
            with self.assertRaises(HTTPException) as raised:
                get_eval_sample_image(batch.id, sample.id, session)
            self.assertEqual(raised.exception.status_code, 404)

    def test_creation_is_503_when_repairs_are_disabled(self):
        settings.repairs.enabled = False
        with self.assertRaises(HTTPException) as raised:
            start_eval_batch(1, EvalBatchRequest(), BackgroundTasks(), None)
        self.assertEqual(raised.exception.status_code, 503)

    def test_creation_is_503_when_eval_profile_is_not_healthy(self):
        with patch("app.api.datasets.eval_client", return_value=SimpleNamespace(health=lambda: SimpleNamespace(profiles=()))):
            with self.assertRaises(HTTPException) as raised:
                start_eval_batch(1, EvalBatchRequest(), BackgroundTasks(), None)
        self.assertEqual(raised.exception.status_code, 503)

    def test_migration_head_is_single(self):
        self.assertEqual(ScriptDirectory.from_config(Config("alembic.ini")).get_heads(), ["f9a0b1c2d3e5"])


if __name__ == "__main__":
    unittest.main()
