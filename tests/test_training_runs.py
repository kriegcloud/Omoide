import json
import tempfile
import unittest
from pathlib import Path

import yaml
from fastapi import HTTPException
from sqlmodel import Session, SQLModel, create_engine, select

from app.api.datasets import get_training_sample_image
from app.config import settings
from app.models import (
    DatasetExport,
    DatasetExportLayout,
    DatasetExportStatus,
    TrainingDataset,
    TrainingRunStatus,
    TrainingSample,
)
from app.services.datasets import _ai_toolkit_config
from app.services.training_runs import cancel_run, create_run, reconcile_runs


class TrainingRunTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}
        )
        SQLModel.metadata.create_all(self.engine)
        self.previous_datasets_dir = settings.general.datasets_dir
        self.previous_host_root = settings.general.datasets_host_root
        settings.general.datasets_dir = self.root / "datasets"
        settings.general.datasets_host_root = None

    def tearDown(self):
        settings.general.datasets_dir = self.previous_datasets_dir
        settings.general.datasets_host_root = self.previous_host_root
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
        export_dir = self.root / "datasets" / dataset.slug / "20260904-120000"
        export_dir.mkdir(parents=True)
        config = _ai_toolkit_config(dataset, export_dir)
        (export_dir / "config.yaml").write_text(
            yaml.safe_dump(config, sort_keys=False), encoding="utf-8"
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

    def test_create_run_writes_overridden_config_and_requested_marker(self):
        with Session(self.engine) as session:
            dataset, export = self._dataset_export(session)
            run = create_run(
                session,
                dataset,
                export,
                {
                    "steps": 321,
                    "lr": 2.5e-5,
                    "rank": 8,
                    "sample_prompts": ["adax in a studio", "adax outdoors"],
                },
            )
            run_dir = Path(run.run_dir)
            config = yaml.safe_load((run_dir / "config.yaml").read_text())
            process = config["config"]["process"][0]
            self.assertEqual(process["train"]["steps"], 321)
            self.assertEqual(process["train"]["lr"], 2.5e-5)
            self.assertEqual(process["network"]["linear"], 8)
            self.assertEqual(process["network"]["linear_alpha"], 8)
            self.assertEqual(process["sample"]["prompts"], ["adax in a studio", "adax outdoors"])
            self.assertEqual(process["training_folder"], str(run_dir / "output"))
            self.assertRegex(config["config"]["name"], r"^ada-portraits-\d{8}-\d{6}$")
            requested = json.loads((run_dir / "REQUESTED").read_text())
            self.assertEqual(requested["run_id"], run.id)
            self.assertIn("created_at", requested)

    def test_create_run_maps_export_dataset_folder_to_host_root(self):
        with Session(self.engine) as session:
            dataset, export = self._dataset_export(session)
            export_dir = Path(export.output_dir)
            container_dataset = settings.general.resolved_datasets_dir() / export_dir.name / "dataset"
            config = yaml.safe_load((export_dir / "config.yaml").read_text())
            config["config"]["process"][0]["datasets"][0]["folder_path"] = str(container_dataset)
            (export_dir / "config.yaml").write_text(yaml.safe_dump(config, sort_keys=False))
            settings.general.datasets_host_root = Path("/host/datasets")
            run = create_run(session, dataset, export, {"steps": 10})
            process = yaml.safe_load((Path(run.run_dir) / "config.yaml").read_text())["config"]["process"][0]
            self.assertEqual(
                process["datasets"][0]["folder_path"],
                str(Path("/host/datasets") / export_dir.name / "dataset"),
            )
            self.assertTrue(process["training_folder"].startswith("/host/datasets/"))

    def test_reconcile_reads_status_and_registers_sample_steps(self):
        with Session(self.engine) as session:
            dataset, export = self._dataset_export(session)
            run = create_run(session, dataset, export, {})
            run_dir = Path(run.run_dir)
            samples = run_dir / "output" / "ada" / "samples"
            samples.mkdir(parents=True)
            (samples / "portrait_0000250_grid.jpg").write_bytes(b"jpeg")
            (samples / "portrait_500.jpg").write_bytes(b"jpeg")
            (samples / "preview.jpg").write_bytes(b"jpeg")
            (run_dir / "status.json").write_text(json.dumps({
                "status": "running",
                "step": 510,
                "total": 2000,
                "loss": 0.123,
                "updated_at": "2026-09-04T12:05:00",
            }))

            reconcile_runs(session)
            session.refresh(run)
            self.assertEqual(run.status, TrainingRunStatus.RUNNING)
            self.assertEqual(run.current_step, 510)
            self.assertEqual(run.total_steps, 2000)
            self.assertEqual(run.last_loss, 0.123)
            rows = session.exec(
                select(TrainingSample)
                .where(TrainingSample.run_id == run.id)
                .order_by(TrainingSample.step)
            ).all()
            self.assertEqual([sample.step for sample in rows], [3, 250, 500])

    def test_cancel_marks_cancelled_on_next_reconcile_without_new_status(self):
        with Session(self.engine) as session:
            dataset, export = self._dataset_export(session)
            run = create_run(session, dataset, export, {})
            cancel_run(session, run)
            self.assertTrue((Path(run.run_dir) / "CANCEL").is_file())
            self.assertEqual(run.status, TrainingRunStatus.REQUESTED)
            reconcile_runs(session)
            session.refresh(run)
            self.assertEqual(run.status, TrainingRunStatus.CANCELLED)

    def test_sample_image_route_refuses_path_outside_run_directory(self):
        with Session(self.engine) as session:
            dataset, export = self._dataset_export(session)
            run = create_run(session, dataset, export, {})
            outside = self.root / "outside.jpg"
            outside.write_bytes(b"not an image")
            sample = TrainingSample(run_id=run.id, step=1, path=str(outside))
            session.add(sample)
            session.commit()
            with self.assertRaises(HTTPException) as raised:
                get_training_sample_image(run.id, sample.id, session)
            self.assertEqual(raised.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
