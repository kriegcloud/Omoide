"""Offline SeedVR2 profile contract; never submits to a live ComfyUI server."""
import base64
import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from PIL import Image

from integrations.comfyui.bridge.config import PROFILE_BINDINGS, load_config
from integrations.comfyui.bridge.errors import BridgeError
from integrations.comfyui.bridge.service import BridgeService

PROFILE_ID = "omoide-upscale-seedvr2-v1"
PROFILE_DIR = Path(__file__).resolve().parents[1] / "integrations/comfyui/profiles" / PROFILE_ID


class FakeComfy:
    def __init__(self, output):
        self.output = output
        self.entries = {}
        self.submissions = []
        self.uploads = []

    def queue(self):
        return {"queue_running": [], "queue_pending": []}

    def history(self, attempt_id):
        return {attempt_id: self.entries[attempt_id]} if attempt_id in self.entries else {}

    def upload_image(self, **kwargs):
        self.uploads.append(kwargs)
        return {"name": kwargs["filename"], "subfolder": kwargs["subfolder"], "type": "input"}

    def submit(self, *, prompt_id, prompt, extra_data):
        self.submissions.append(prompt)
        self.entries[prompt_id] = {
            "prompt": [0, prompt_id, prompt, extra_data, ["12"]],
            "outputs": {"12": {"images": [{"filename": self.output.name, "subfolder": "", "type": "output"}]}},
            "status": {"status_str": "success", "completed": True},
        }
        return {"prompt_id": prompt_id}


class SeedVR2BridgeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.output = self.root / "output"
        self.output.mkdir()
        self.result_path = self.output / "result.png"
        Image.new("RGB", (16, 16), "green").save(self.result_path)
        self.data = self.result_path.read_bytes()
        self.workflow = PROFILE_DIR / "workflow.api.json"
        binding = replace(PROFILE_BINDINGS[PROFILE_ID], workflow_path=self.workflow)
        self.config_path = self.root / "config.json"
        self.config_path.write_text(json.dumps({
            "schema": "omoide-comfy-bridge-config/v1",
            "comfy_base_url": "http://127.0.0.1:8188",
            "staging_directory": str(self.root / "omoide"),
            "output_directory": str(self.output),
            "profiles": {PROFILE_ID: {"workflow_path": str(self.workflow), "workflow_sha256": binding.workflow_sha256}},
        }))
        with patch("integrations.comfyui.bridge.config.PROFILE_BINDINGS", {PROFILE_ID: binding}):
            self.config = load_config(self.config_path)
        self.fake = FakeComfy(self.result_path)
        self.service = BridgeService(self.config, http_client=self.fake, profile_readiness=lambda _profile: (True, None))

    def request(self, params=None):
        request = {"attempt_id": str(uuid4()), "profile_id": PROFILE_ID,
                   "image": base64.b64encode(self.data).decode(), "media_type": "image/png",
                   "image_sha256": hashlib.sha256(self.data).hexdigest()}
        if params is not None:
            request["params"] = params
        return request

    def test_fake_bridge_maps_scale_to_native_node_and_returns_image(self):
        result = self.service.repair(self.request({"scale": 4}))
        prompt = self.fake.submissions[0]
        self.assertEqual(prompt["3"]["inputs"]["scale_by"], 4)
        self.assertEqual(prompt["1"]["inputs"]["image"], "omoide/" + self.fake.uploads[0]["filename"])
        self.assertEqual(prompt["11"]["inputs"]["color_correction_method"], "lab")
        self.assertEqual(result["raw_result"]["media_type"], "image/png")
        self.assertEqual(result["profile_id"], PROFILE_ID)
        self.assertEqual(json.loads(self.workflow.read_text())["3"]["inputs"]["scale_by"], 2.0)

    def test_omitted_or_empty_params_preserve_conservative_default(self):
        for params in (None, {}):
            self.service.repair(self.request(params))
            self.assertEqual(self.fake.submissions[-1]["3"]["inputs"]["scale_by"], 2)

    def test_invalid_scale_or_extra_fields_rejected_before_upload(self):
        for params in ({"scale": 3}, {"scale": True}, {"scale": "4"}, {"scale": 2, "seed": 1}):
            with self.subTest(params=params), self.assertRaises(BridgeError) as error:
                self.service.repair(self.request(params))
            self.assertEqual(error.exception.code, "invalid-params")
        self.assertEqual(self.fake.uploads, [])
        self.assertEqual(self.fake.submissions, [])

    def test_workflow_and_example_are_pinned_to_validated_source(self):
        binding = PROFILE_BINDINGS[PROFILE_ID]
        self.assertEqual(hashlib.sha256(self.workflow.read_bytes()).hexdigest(), binding.workflow_sha256)
        example = json.loads((PROFILE_DIR.parent.parent / "bridge/config.example.json").read_text())
        self.assertEqual(example["profiles"][PROFILE_ID]["workflow_sha256"], binding.workflow_sha256)
        self.assertEqual((binding.image_node_id, binding.output_node_id), ("1", "12"))
        self.assertIn(("3", "ImageScaleBy"), binding.required_workflow_nodes)


if __name__ == "__main__":
    unittest.main()
