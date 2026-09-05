import io
import hashlib
import json
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from dataclasses import replace
from unittest.mock import patch

from fastapi import BackgroundTasks, HTTPException
from PIL import Image
from sqlmodel import Session, SQLModel, create_engine

from app.api.repairs import _params_for_media, start_repair
from app.config import settings
from app.models import Face, ImageRepairJob, ImageRepairStatus, Media, Person
from app.schemas.repair import RepairRequest
from app.services.comfy_annotation import ComfyAnnotationError
from app.services.image_edits import write_repaired
from app.tasks.image_repair import run_repair_job
from integrations.comfyui.bridge.config import PROFILE_BINDINGS, load_config
from integrations.comfyui.bridge.errors import BridgeError


class FakeRepairClient:
    def __init__(self, *, failure: ComfyAnnotationError | None = None):
        self.failure = failure

    def repair(self, **_kwargs):
        if self.failure:
            raise self.failure
        buffer = io.BytesIO()
        Image.new("RGB", (40, 30), "green").save(buffer, "JPEG")
        return SimpleNamespace(image=buffer.getvalue())

    def ack_attempt(self, **_kwargs):
        return None


class ImageRepairTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "portrait.jpg"
        Image.new("RGB", (100, 80), "blue").save(self.source)
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
        SQLModel.metadata.create_all(self.engine)

    def tearDown(self):
        self.engine.dispose()
        self.temp.cleanup()

    def _job(self) -> str:
        with Session(self.engine) as session:
            media = Media(
                path=str(self.source), filename=self.source.name, size=self.source.stat().st_size,
                width=100, height=80,
            )
            session.add(media)
            session.commit()
            session.refresh(media)
            job = ImageRepairJob(
                id=str(uuid.uuid4()), media_id=media.id,
                profile="omoide-remove-text-v1", status=ImageRepairStatus.QUEUED,
            )
            session.add(job)
            session.commit()
            return job.id

    def test_repair_copy_naming_is_collision_safe(self):
        image = Image.new("RGB", (10, 10))
        first = write_repaired(
            self.source, image, "omoide-remove-text-v1", media_roots=[(self.root, False)]
        )
        second = write_repaired(
            self.source, image, "omoide-remove-text-v1", media_roots=[(self.root, False)]
        )
        self.assertEqual(first.name, "portrait_repaired-remove-text.jpg")
        self.assertEqual(second.name, "portrait_repaired-remove-text-2.jpg")

    def test_job_success_registers_media(self):
        job_id = self._job()
        with (
            patch("app.database.engine", self.engine),
            patch("app.tasks.image_repair.repair_client", return_value=FakeRepairClient()),
            patch.object(type(settings.general), "resolved_media_dirs", return_value=[(self.root, False)]),
            patch("app.tasks.image_repair.generate_perceptual_hash", return_value="hash"),
            patch("app.tasks.image_repair.generate_thumbnail", return_value=(None, None)),
            patch("app.tasks.media_processing.run_processors_for_media"),
        ):
            run_repair_job(job_id)
        with Session(self.engine) as session:
            job = session.get(ImageRepairJob, job_id)
            self.assertEqual(job.status, ImageRepairStatus.SUCCEEDED)
            result = session.get(Media, job.result_media_id)
            self.assertEqual(result.filename, "portrait_repaired-remove-text.jpg")
            self.assertEqual(result.created_at, session.get(Media, job.media_id).created_at)

    def test_job_failure_keeps_bridge_classification(self):
        job_id = self._job()
        failure = ComfyAnnotationError("busy", "Bridge is busy", retryable=True)
        with (
            patch("app.database.engine", self.engine),
            patch("app.tasks.image_repair.repair_client", return_value=FakeRepairClient(failure=failure)),
        ):
            run_repair_job(job_id)
        with Session(self.engine) as session:
            job = session.get(ImageRepairJob, job_id)
            self.assertEqual(job.status, ImageRepairStatus.FAILED)
            self.assertEqual(job.error_code, "busy")
            self.assertTrue(job.retryable)

    def test_remove_people_defaults_to_source_pixel_face_box(self):
        with Session(self.engine) as session:
            person = Person(name="Subject", appearance_count=1)
            media = Media(path=str(self.source), filename=self.source.name, size=1, width=2560, height=1280)
            session.add(person)
            session.add(media)
            session.commit()
            session.add(Face(media_id=media.id, person_id=person.id, bbox=[100, 50, 200, 300]))
            session.commit()
            params = _params_for_media(session, media, RepairRequest(
                profile="omoide-remove-people-v1", person_id=person.id,
            ))
        self.assertEqual(params["subject_box"], {"x": 200, "y": 100, "width": 400, "height": 600})

    def test_presentation_mode_precedes_disabled_response(self):
        old_presentation = settings.general.presentation_mode
        old_enabled = settings.repairs.enabled
        try:
            settings.general.presentation_mode = True
            settings.repairs.enabled = False
            with self.assertRaises(HTTPException) as raised:
                start_repair(1, RepairRequest(profile="omoide-remove-text-v1"), BackgroundTasks(), None)
            self.assertEqual(raised.exception.status_code, 403)
            settings.general.presentation_mode = False
            with self.assertRaises(HTTPException) as raised:
                start_repair(1, RepairRequest(profile="omoide-remove-text-v1"), BackgroundTasks(), None)
            self.assertEqual(raised.exception.status_code, 503)
        finally:
            settings.general.presentation_mode = old_presentation
            settings.repairs.enabled = old_enabled

    def test_image_profile_requires_output_directory_and_save_image_contract(self):
        workflow = self.root / "repair.json"
        workflow.write_text(json.dumps({
            "input": {"class_type": "LoadImage", "inputs": {"image": "old.png"}},
            "output": {"class_type": "SaveImage", "inputs": {"images": ["input", 0]}},
        }))
        profile_id = "omoide-remove-text-v1"
        binding = replace(
            PROFILE_BINDINGS["omoide-caption-v1"],
            workflow_path=workflow,
            workflow_sha256=hashlib.sha256(workflow.read_bytes()).hexdigest(),
            image_node_id="input",
            output_node_id="output",
            output_node_class="SaveImage",
            output_key="images",
            result_kind="image",
            required_node_classes=("LoadImage", "SaveImage"),
            required_workflow_nodes=(("input", "LoadImage"), ("output", "SaveImage")),
            required_combo_values=(),
            artifacts=(),
            executor_artifacts=(),
        )
        payload = {
            "schema": "omoide-comfy-bridge-config/v1",
            "comfy_base_url": "http://127.0.0.1:8188",
            "staging_directory": str(self.root / "omoide"),
            "profiles": {profile_id: {
                "workflow_path": str(workflow),
                "workflow_sha256": binding.workflow_sha256,
            }},
        }
        config_path = self.root / "config.json"
        config_path.write_text(json.dumps(payload))
        with patch("integrations.comfyui.bridge.config.PROFILE_BINDINGS", {profile_id: binding}):
            with self.assertRaises(BridgeError):
                load_config(config_path)
            payload["output_directory"] = str(self.root / "output")
            config_path.write_text(json.dumps(payload))
            loaded = load_config(config_path)
        self.assertEqual(loaded.profiles[profile_id].result_kind, "image")


if __name__ == "__main__":
    unittest.main()
