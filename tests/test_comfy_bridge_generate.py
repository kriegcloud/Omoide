import hashlib
import json
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
from uuid import uuid4

from integrations.comfyui.bridge.config import PROFILE_BINDINGS, load_config
from integrations.comfyui.bridge.errors import BridgeError
from integrations.comfyui.bridge.service import BridgeService


PROFILE_ID = "omoide-eval-zimage-v1"


class FakeComfy:
    def __init__(self, output: Path):
        self.output = output
        self.entries = {}
        self.submissions = []

    def system_stats(self):
        return {"system": {"comfyui_version": "test"}}

    def queue(self):
        return {"queue_running": [], "queue_pending": []}

    def history(self, attempt_id):
        return {attempt_id: self.entries[attempt_id]} if attempt_id in self.entries else {}

    def submit(self, *, prompt_id, prompt, extra_data):
        self.submissions.append((prompt_id, prompt, extra_data))
        row = [0, prompt_id, prompt, extra_data, ["save"]]
        self.entries[prompt_id] = {
            "prompt": row,
            "outputs": {"save": {"images": [{"filename": self.output.name, "subfolder": "", "type": "output"}]}},
            "status": {"status_str": "success", "completed": True},
        }
        return {"prompt_id": prompt_id}


class GenerateBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.staging = self.root / "input" / "omoide"
        self.staging.mkdir(parents=True)
        self.output = self.root / "output"
        self.output.mkdir()
        self.lora_root = self.root / "datasets"
        self.lora_root.mkdir()
        workflow = self.root / "eval.json"
        workflow.write_text(json.dumps({
            "params": {"class_type": "OmoideEvalParams", "inputs": {"params_json": "{}"}},
            "loader": {"class_type": "OmoideEvalLoraLoader", "inputs": {"params_json": ["params", 8]}},
            "save": {"class_type": "SaveImage", "inputs": {"images": ["loader", 0]}},
        }))
        digest = hashlib.sha256(workflow.read_bytes()).hexdigest()
        self.binding = replace(
            PROFILE_BINDINGS["omoide-caption-v1"],
            workflow_path=workflow,
            workflow_sha256=digest,
            image_node_id=None,
            image_node_class=None,
            image_input=None,
            output_node_id="save",
            output_node_class="SaveImage",
            output_key="images",
            result_kind="image",
            required_node_classes=("OmoideEvalParams", "OmoideEvalLoraLoader", "SaveImage"),
            required_workflow_nodes=(("params", "OmoideEvalParams"), ("save", "SaveImage")),
            required_combo_values=(),
            artifacts=(),
            executor_artifacts=(),
            input_json_node_id="params",
            input_json_input="params_json",
            input_kind="params",
        )
        self.config_path = self.root / "config.json"
        self.config_path.write_text(json.dumps({
            "schema": "omoide-comfy-bridge-config/v1",
            "comfy_base_url": "http://127.0.0.1:8188",
            "staging_directory": str(self.staging),
            "output_directory": str(self.output),
            "lora_root": str(self.lora_root),
            "profiles": {PROFILE_ID: {
                "workflow_path": str(workflow),
                "workflow_sha256": digest,
                "input_kind": "params",
            }},
        }))

    def tearDown(self):
        self.temporary.cleanup()

    def _config(self):
        with patch("integrations.comfyui.bridge.config.PROFILE_BINDINGS", {PROFILE_ID: self.binding}):
            return load_config(self.config_path)

    def _config_for_url(self, base_url: str):
        payload = json.loads(self.config_path.read_text())
        payload["comfy_base_url"] = base_url
        self.config_path.write_text(json.dumps(payload))
        return self._config()

    def _params(self, path: Path):
        return {"prompt": "portrait", "negative": "", "seed": 1, "steps": 9, "cfg": 1.0, "width": 1024, "height": 1024, "lora_path": str(path), "lora_strength": 1.0}

    def test_params_profile_validation_and_health_input_kind(self):
        config = self._config()
        self.assertEqual(config.profiles[PROFILE_ID].input_kind, "params")
        service = BridgeService(config, http_client=FakeComfy(self.output / "result.png"), profile_readiness=lambda _profile: (True, None))
        self.assertEqual(service.health()["profile_input_kinds"], {PROFILE_ID: "params"})

    def test_generate_forwards_canonical_params_and_returns_image(self):
        from PIL import Image

        result_path = self.output / "result.png"
        Image.new("RGB", (16, 16), "green").save(result_path)
        lora = self.lora_root / "run" / "model.safetensors"
        lora.parent.mkdir()
        lora.write_bytes(b"model")
        fake = FakeComfy(result_path)
        service = BridgeService(self._config(), http_client=fake, profile_readiness=lambda _profile: (True, None))
        params = self._params(lora)
        result = service.generate({"attempt_id": str(uuid4()), "profile_id": PROFILE_ID, "params": params})
        self.assertEqual(result["action"], "generate")
        forwarded = json.loads(fake.submissions[0][1]["params"]["inputs"]["params_json"])
        self.assertEqual(forwarded, params)
        self.assertEqual(result["raw_result"]["media_type"], "image/png")

    def test_generate_success_through_fake_comfy_http_server(self):
        from PIL import Image

        result_path = self.output / "http-result.png"
        Image.new("RGB", (16, 16), "blue").save(result_path)
        history = {}
        submissions = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                return

            def send_json(self, payload):
                encoded = json.dumps(payload, separators=(",", ":")).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def do_GET(self):
                if self.path == "/queue":
                    self.send_json({"queue_running": [], "queue_pending": []})
                elif self.path.startswith("/history/"):
                    attempt_id = self.path.removeprefix("/history/")
                    self.send_json({attempt_id: history[attempt_id]} if attempt_id in history else {})
                else:
                    self.send_json({"system": {"comfyui_version": "test"}})

            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
                submissions.append(payload)
                attempt_id = payload["prompt_id"]
                row = [0, attempt_id, payload["prompt"], payload["extra_data"], ["save"]]
                history[attempt_id] = {
                    "prompt": row,
                    "outputs": {"save": {"images": [{"filename": result_path.name, "subfolder": "", "type": "output"}]}},
                    "status": {"status_str": "success", "completed": True},
                }
                self.send_json({"prompt_id": attempt_id})

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            service = BridgeService(self._config_for_url(f"http://127.0.0.1:{port}"), profile_readiness=lambda _profile: (True, None))
            lora = self.lora_root / "run" / "model.safetensors"
            lora.parent.mkdir()
            lora.write_bytes(b"model")
            result = service.handle({"protocol": "omoide-comfy/v1", "action": "generate", "attempt_id": str(uuid4()), "profile_id": PROFILE_ID, "params": self._params(lora)})
            self.assertEqual(result["raw_result"]["media_type"], "image/png")
            self.assertEqual(len(submissions), 1)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_lora_path_must_be_absolute_contained_and_without_parent_segments(self):
        service = BridgeService(self._config(), http_client=FakeComfy(self.output / "result.png"), profile_readiness=lambda _profile: (True, None))
        for value in ("relative.safetensors", str(self.root / "outside.safetensors"), str(self.lora_root / "run" / ".." / "model.safetensors")):
            with self.subTest(value=value), self.assertRaises(BridgeError):
                service._validate_generate_params(self._params(Path(value)))


if __name__ == "__main__":
    unittest.main()
