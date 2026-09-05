import hashlib
import json
import socket
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from email.parser import BytesParser
from email.policy import default
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from PIL import Image

from app.config import AnnotationSettings
from app.services.comfy_annotation import (
    CAPTION_PROFILE_ID,
    TAGS_PROFILE_ID,
    ComfyAnnotationClient,
    ComfyAnnotationError,
    ComfyAnnotationResult,
    ComfyAttemptState,
)
from integrations.comfyui.bridge.config import (
    PROFILE_BINDINGS,
    ArtifactBinding,
    load_config,
)
from integrations.comfyui.bridge.comfy_http import (
    MAX_EXACT_HISTORY_RESPONSE_BYTES,
    MAX_HTTP_RESPONSE_BYTES,
)
from integrations.comfyui.bridge.errors import BridgeError
from integrations.comfyui.bridge.service import BridgeService, BridgeUnixServer

CAPTION_IMAGE_NODE = "1111775332128324"
CAPTION_OUTPUT_NODE = "3287111284508309"
TAGS_IMAGE_NODE = "3128925228060849"
TAGS_OUTPUT_NODE = "3436206424550325"
TAG_GENERAL_THRESHOLD = 0.5296000242233276
TAG_CHARACTER_THRESHOLD = 0.8500000238418579


class FakeComfyState:
    def __init__(self, staging_directory: Path) -> None:
        self.staging_directory = staging_directory
        self.lock = threading.Lock()
        self.history: dict[str, dict] = {}
        self.running: list[list] = []
        self.pending: list[list] = []
        self.prompt_requests: list[dict] = []
        self.cancelled_ids: list[str] = []
        self.history_delete_requests: list[dict] = []
        self.mode = "success"
        self.prompt_seen = threading.Event()
        self.upload_seen = threading.Event()
        self.release_upload = threading.Event()
        self.history_failures_remaining = 0

    def queue_payload(self) -> dict[str, list[list]]:
        with self.lock:
            return {
                "queue_running": list(self.running),
                "queue_pending": list(self.pending),
            }


def fake_handler(state: FakeComfyState):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args) -> None:
            return

        def _read_body(self) -> bytes:
            return self.rfile.read(int(self.headers.get("Content-Length", "0")))

        def _send_json(self, payload: object, status: int = 200) -> None:
            encoded = json.dumps(payload, separators=(",", ":")).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def _send_raw(self, payload: bytes, status: int = 200) -> None:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self) -> None:
            if self.path == "/system_stats":
                self._send_json({"system": {"comfyui_version": "test"}})
                return
            if self.path == "/queue":
                self._send_json(state.queue_payload())
                return
            if self.path.startswith("/history/"):
                prompt_id = self.path.removeprefix("/history/")
                with state.lock:
                    if state.history_failures_remaining > 0:
                        state.history_failures_remaining -= 1
                        self._send_json({"error": "transient"}, 500)
                        return
                    entry = state.history.get(prompt_id)
                    response = {prompt_id: entry} if entry is not None else {}
                self._send_json(response)
                return
            self._send_json({"error": "not found"}, 404)

        def do_POST(self) -> None:
            if self.path == "/history":
                payload = json.loads(self._read_body())
                with state.lock:
                    state.history_delete_requests.append(payload)
                    for prompt_id in payload.get("delete", []):
                        state.history.pop(prompt_id, None)
                # Native ComfyUI returns HTTP 200 with an empty body here.
                self._send_raw(b"")
                return
            if self.path == "/upload/image":
                message = BytesParser(policy=default).parsebytes(
                    (
                        "Content-Type: "
                        + self.headers["Content-Type"]
                        + "\r\nMIME-Version: 1.0\r\n\r\n"
                    ).encode("ascii")
                    + self._read_body()
                )
                image_part = next(
                    part
                    for part in message.iter_parts()
                    if part.get_param("name", header="content-disposition") == "image"
                )
                filename = image_part.get_filename()
                assert filename is not None
                state.staging_directory.mkdir(parents=True, exist_ok=True)
                (state.staging_directory / filename).write_bytes(
                    image_part.get_payload(decode=True)
                )
                if state.mode == "hold-upload":
                    state.upload_seen.set()
                    state.release_upload.wait(timeout=3)
                self._send_json(
                    {"name": filename, "subfolder": "omoide", "type": "input"}
                )
                return
            if self.path == "/prompt":
                payload = json.loads(self._read_body())
                prompt_id = payload["prompt_id"]
                metadata = payload["extra_data"]["omoide_annotation"]
                profile_id = metadata["profile_id"]
                output_node = (
                    CAPTION_OUTPUT_NODE
                    if profile_id == CAPTION_PROFILE_ID
                    else TAGS_OUTPUT_NODE
                )
                output = (
                    "a careful caption"
                    if profile_id == CAPTION_PROFILE_ID
                    else json.dumps(
                        {
                            "schema": "omoide.annotation/v1",
                            "general": [{"tag": "portrait", "score": 0.9}],
                        },
                        separators=(",", ":"),
                    )
                )
                row = [
                    0,
                    prompt_id,
                    payload["prompt"],
                    payload["extra_data"],
                    [output_node],
                ]
                with state.lock:
                    state.prompt_requests.append(payload)
                    if state.mode == "hold":
                        state.running.append(row)
                    elif state.mode != "ambiguous-lost":
                        state.history[prompt_id] = {
                            "prompt": row,
                            "outputs": {output_node: {"text": [output]}},
                            "status": {"status_str": "success", "completed": True},
                        }
                    if state.mode == "accepted-history-500":
                        state.history_failures_remaining = 1
                state.prompt_seen.set()
                if state.mode in {"ambiguous-success", "ambiguous-lost"}:
                    self.close_connection = True
                    try:
                        self.connection.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                    self.connection.close()
                    return
                if state.mode == "accepted-500":
                    self._send_json({"error": "late failure"}, 500)
                    return
                if state.mode == "accepted-invalid-json":
                    self._send_raw(b"{not-json")
                    return
                self._send_json({"prompt_id": prompt_id, "number": 0})
                return
            if self.path.startswith("/api/jobs/") and self.path.endswith("/cancel"):
                prompt_id = self.path.removeprefix("/api/jobs/").removesuffix(
                    "/cancel"
                )
                with state.lock:
                    before = len(state.running) + len(state.pending)
                    state.running = [item for item in state.running if item[1] != prompt_id]
                    state.pending = [item for item in state.pending if item[1] != prompt_id]
                    cancelled = before != len(state.running) + len(state.pending)
                    if cancelled:
                        state.cancelled_ids.append(prompt_id)
                self._send_json({"cancelled": cancelled})
                return
            self._send_json({"error": "not found"}, 404)

    return Handler


def workflow(image_node: str, output_node: str) -> dict:
    return {
        image_node: {
            "class_type": "LoadImage",
            "inputs": {"image": "placeholder.png"},
        },
        output_node: {
            "class_type": "PreviewAny",
            "inputs": {"source": [image_node, 0]},
        },
    }


def write_profile_config(
    root: Path,
    base_url: str,
    staging_directory: Path,
) -> Path:
    example = json.loads(
        Path("integrations/comfyui/bridge/config.example.json").read_text(
            encoding="utf-8"
        )
    )
    profiles = example["profiles"]
    for profile in profiles.values():
        profile["timeout_seconds"] = 5
        profile["reconcile_seconds"] = 1
        profile["poll_interval_seconds"] = 0.05
    config_path = root / "bridge.json"
    config_path.write_text(
        json.dumps(
            {
                "schema": "omoide-comfy-bridge-config/v1",
                "comfy_base_url": base_url,
                "staging_directory": str(staging_directory),
                "profiles": profiles,
            }
        ),
        encoding="utf-8",
    )
    return config_path


class BridgeContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.staging_directory = self.root / "input" / "omoide"
        self.staging_directory.mkdir(parents=True)
        self.fake_state = FakeComfyState(self.staging_directory)
        self.http_server = ThreadingHTTPServer(
            ("127.0.0.1", 0),
            fake_handler(self.fake_state),
        )
        self.http_thread = threading.Thread(
            target=self.http_server.serve_forever,
            daemon=True,
        )
        self.http_thread.start()
        host, port = self.http_server.server_address
        self.config_path = write_profile_config(
            self.root,
            f"http://{host}:{port}",
            self.staging_directory,
        )
        self.socket_path = self.root / "bridge.sock"
        self.bridge_config = load_config(self.config_path)
        self.bridge_service = BridgeService(
            self.bridge_config,
            profile_readiness=lambda _profile: (True, None),
        )
        self.bridge_server = BridgeUnixServer(self.bridge_service, self.socket_path)
        self.bridge_thread = threading.Thread(
            target=self.bridge_server.serve_forever,
            daemon=True,
        )
        self.bridge_thread.start()
        deadline = time.monotonic() + 2
        while not self.socket_path.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(self.socket_path.is_socket())
        self.client = ComfyAnnotationClient(self.socket_path, timeout_seconds=8)
        # The socket inode can exist just before the server starts accepting.
        # Probe the protocol boundary so each test begins after listen(), not
        # merely after bind().
        while time.monotonic() < deadline:
            try:
                self.client.health()
                break
            except ComfyAnnotationError:
                time.sleep(0.01)
        else:
            self.fail("fake Comfy bridge did not become ready")

    def tearDown(self) -> None:
        self.bridge_server.shutdown()
        self.bridge_thread.join(timeout=2)
        self.http_server.shutdown()
        self.http_server.server_close()
        self.http_thread.join(timeout=2)
        self.temporary.cleanup()

    def test_health_reports_only_locked_profiles(self) -> None:
        health = self.client.health()

        self.assertTrue(health.ready)
        self.assertEqual(
            set(health.profiles),
            {CAPTION_PROFILE_ID, TAGS_PROFILE_ID},
        )
        self.assertEqual(set(health.configured_profiles), set(health.profiles))
        self.assertEqual(health.unavailable_profiles, {})
        self.assertIsNone(health.active_attempt_id)
        self.assertTrue(urlsplit(health.comfy_url).hostname == "127.0.0.1")

    def test_caption_submission_preserves_identity_and_cleans_staging(self) -> None:
        attempt_id = uuid4()
        result = self.client.annotate(
            attempt_id=attempt_id,
            profile_id=CAPTION_PROFILE_ID,
            image=Image.new("RGB", (8, 6), "green"),
        )

        self.assertEqual(result.attempt_id, attempt_id)
        self.assertEqual(result.prompt_id, attempt_id)
        self.assertEqual(result.raw_result, "a careful caption")
        self.assertEqual(
            result.profile_provenance["model"]["revision"],
            "5d50a2252bf1bcd49e5fee9b5f296986d442682b",
        )
        self.assertFalse((self.staging_directory / f"{attempt_id}.jpg").exists())
        payload = self.fake_state.prompt_requests[0]
        self.assertEqual(payload["prompt_id"], str(attempt_id))
        self.assertEqual(
            payload["prompt"][CAPTION_IMAGE_NODE]["inputs"]["image"],
            f"omoide/{attempt_id}.jpg",
        )
        serialized = json.dumps(payload)
        self.assertNotIn(str(self.staging_directory), serialized)
        self.assertNotIn("/run/media/", serialized)

    def test_tag_json_is_returned_as_structured_data(self) -> None:
        result = self.client.annotate(
            attempt_id=uuid4(),
            profile_id=TAGS_PROFILE_ID,
            image=Image.new("RGBA", (4, 4), (1, 2, 3, 120)),
        )

        self.assertEqual(result.raw_result["schema"], "omoide.annotation/v1")
        self.assertEqual(result.raw_result["general"][0]["tag"], "portrait")

    def test_get_attempt_recovers_completed_result_from_history(self) -> None:
        attempt_id = uuid4()
        completed = self.client.annotate(
            attempt_id=attempt_id,
            profile_id=CAPTION_PROFILE_ID,
            image=Image.new("RGB", (3, 3)),
        )

        recovered = self.client.get_attempt(attempt_id)

        self.assertIsInstance(recovered, ComfyAnnotationResult)
        self.assertEqual(recovered, completed)

    def test_ack_deletes_only_the_exact_verified_history_uuid(self) -> None:
        attempt_id = uuid4()
        foreign_id = str(uuid4())
        self.client.annotate(
            attempt_id=attempt_id,
            profile_id=CAPTION_PROFILE_ID,
            image=Image.new("RGB", (3, 3)),
        )
        with self.fake_state.lock:
            self.fake_state.history[foreign_id] = {"foreign": True}

        acknowledged = self.client.ack_attempt(attempt_id=attempt_id)

        self.assertEqual(acknowledged.status, "deleted")
        self.assertEqual(
            self.fake_state.history_delete_requests,
            [{"delete": [str(attempt_id)]}],
        )
        self.assertNotIn(str(attempt_id), self.fake_state.history)
        self.assertIn(foreign_id, self.fake_state.history)

    def test_ack_is_idempotent_when_history_is_already_absent(self) -> None:
        attempt_id = uuid4()

        acknowledged = self.client.ack_attempt(attempt_id=attempt_id)

        self.assertEqual(acknowledged.status, "already-absent")
        self.assertEqual(self.fake_state.history_delete_requests, [])

    def test_ack_rechecks_history_after_an_initial_absence(self) -> None:
        attempt_id = uuid4()
        self.client.annotate(
            attempt_id=attempt_id,
            profile_id=CAPTION_PROFILE_ID,
            image=Image.new("RGB", (3, 3)),
        )
        with self.fake_state.lock:
            entry = self.fake_state.history.pop(str(attempt_id))

        with patch.object(
            self.bridge_service.http,
            "history",
            side_effect=[{}, {str(attempt_id): entry}, {}],
        ):
            acknowledged = self.client.ack_attempt(attempt_id=attempt_id)

        self.assertEqual(acknowledged.status, "deleted")
        self.assertEqual(
            self.fake_state.history_delete_requests,
            [{"delete": [str(attempt_id)]}],
        )

    def test_ack_refuses_an_exact_attempt_that_is_still_active_or_queued(self) -> None:
        active_id = uuid4()
        self.bridge_service._set_active(str(active_id))
        try:
            with self.assertRaises(ComfyAnnotationError) as active_error:
                self.client.ack_attempt(attempt_id=active_id)
        finally:
            self.bridge_service._clear_active(str(active_id))

        self.assertEqual(active_error.exception.code, "ack-pending")
        self.assertTrue(active_error.exception.retryable)

        queued_id = uuid4()
        with self.fake_state.lock:
            self.fake_state.pending.append([0, str(queued_id), {}, {}])
        with self.assertRaises(ComfyAnnotationError) as queued_error:
            self.client.ack_attempt(attempt_id=queued_id)

        self.assertEqual(queued_error.exception.code, "ack-pending")
        self.assertTrue(queued_error.exception.retryable)
        self.assertEqual(self.fake_state.history_delete_requests, [])

    def test_ack_requires_a_canonical_lowercase_uuid(self) -> None:
        with self.assertRaises(BridgeError) as raised:
            self.bridge_service.handle(
                {
                    "protocol": "omoide-comfy/v1",
                    "action": "ack_attempt",
                    "attempt_id": "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA",
                }
            )

        self.assertEqual(raised.exception.code, "protocol-error")
        self.assertEqual(self.fake_state.history_delete_requests, [])

    def test_ack_refuses_foreign_or_unverified_history(self) -> None:
        attempt_id = uuid4()
        self.client.annotate(
            attempt_id=attempt_id,
            profile_id=CAPTION_PROFILE_ID,
            image=Image.new("RGB", (3, 3)),
        )
        with self.fake_state.lock:
            entry = self.fake_state.history[str(attempt_id)]
            entry["prompt"][3]["omoide_annotation"][
                "profile_provenance_sha256"
            ] = "0" * 64

        with self.assertRaises(ComfyAnnotationError) as raised:
            self.client.ack_attempt(attempt_id=attempt_id)

        self.assertEqual(raised.exception.code, "provenance-mismatch")
        self.assertEqual(self.fake_state.history_delete_requests, [])
        self.assertIn(str(attempt_id), self.fake_state.history)

    def test_ack_deletes_a_verified_terminal_result_that_cannot_be_decoded(
        self,
    ) -> None:
        attempt_id = uuid4()
        self.client.annotate(
            attempt_id=attempt_id,
            profile_id=TAGS_PROFILE_ID,
            image=Image.new("RGB", (3, 3)),
        )
        with self.fake_state.lock:
            entry = self.fake_state.history[str(attempt_id)]
            entry["outputs"][TAGS_OUTPUT_NODE]["text"] = ["not-json"]

        acknowledged = self.client.ack_attempt(attempt_id=attempt_id)

        self.assertEqual(acknowledged.status, "deleted")
        self.assertEqual(
            self.fake_state.history_delete_requests,
            [{"delete": [str(attempt_id)]}],
        )
        self.assertNotIn(str(attempt_id), self.fake_state.history)

    def test_ack_refuses_verified_history_without_a_terminal_status(self) -> None:
        attempt_id = uuid4()
        self.client.annotate(
            attempt_id=attempt_id,
            profile_id=CAPTION_PROFILE_ID,
            image=Image.new("RGB", (3, 3)),
        )
        with self.fake_state.lock:
            entry = self.fake_state.history[str(attempt_id)]
            entry["status"] = {"status_str": "running", "completed": False}

        with self.assertRaises(ComfyAnnotationError) as raised:
            self.client.ack_attempt(attempt_id=attempt_id)

        self.assertEqual(raised.exception.code, "ack-refused")
        self.assertEqual(self.fake_state.history_delete_requests, [])
        self.assertIn(str(attempt_id), self.fake_state.history)

    def test_ack_deletes_only_the_exact_verified_failed_history_uuid(self) -> None:
        attempt_id = uuid4()
        foreign_id = str(uuid4())
        self.client.annotate(
            attempt_id=attempt_id,
            profile_id=TAGS_PROFILE_ID,
            image=Image.new("RGB", (3, 3)),
        )
        with self.fake_state.lock:
            entry = self.fake_state.history[str(attempt_id)]
            entry["status"] = {"status_str": "error", "completed": False}
            entry["outputs"] = {"verbose_tensor_diagnostics": {"value": [1, 2, 3]}}
            self.fake_state.history[foreign_id] = {"foreign": True}

        acknowledged = self.client.ack_attempt(attempt_id=attempt_id)

        self.assertEqual(acknowledged.status, "deleted")
        self.assertEqual(
            self.fake_state.history_delete_requests,
            [{"delete": [str(attempt_id)]}],
        )
        self.assertNotIn(str(attempt_id), self.fake_state.history)
        self.assertIn(foreign_id, self.fake_state.history)

    def test_ack_accepts_verbose_history_above_the_general_response_cap(
        self,
    ) -> None:
        attempt_id = uuid4()
        self.client.annotate(
            attempt_id=attempt_id,
            profile_id=TAGS_PROFILE_ID,
            image=Image.new("RGB", (3, 3)),
        )
        verbose_bytes = MAX_HTTP_RESPONSE_BYTES + 1024
        self.assertLess(verbose_bytes, MAX_EXACT_HISTORY_RESPONSE_BYTES)
        with self.fake_state.lock:
            entry = self.fake_state.history[str(attempt_id)]
            entry["status"] = {"status_str": "error", "completed": False}
            entry["verbose_terminal_diagnostics"] = "x" * verbose_bytes

        acknowledged = self.client.ack_attempt(attempt_id=attempt_id)

        self.assertEqual(acknowledged.status, "deleted")
        self.assertEqual(
            self.fake_state.history_delete_requests,
            [{"delete": [str(attempt_id)]}],
        )

    def test_non_history_response_keeps_the_general_response_cap(self) -> None:
        response = MagicMock()
        response.headers = {
            "Content-Length": str(MAX_HTTP_RESPONSE_BYTES + 1),
        }
        response.__enter__.return_value = response

        with (
            patch.object(
                self.bridge_service.http._opener,
                "open",
                return_value=response,
            ),
            self.assertRaises(BridgeError) as raised,
        ):
            self.bridge_service.http.queue()

        self.assertEqual(raised.exception.code, "comfy-protocol-error")
        response.read.assert_not_called()

    def test_duplicate_attempt_is_never_resubmitted(self) -> None:
        attempt_id = uuid4()
        image = Image.new("RGB", (3, 3))
        self.client.annotate(
            attempt_id=attempt_id,
            profile_id=CAPTION_PROFILE_ID,
            image=image,
        )

        with self.assertRaises(ComfyAnnotationError) as raised:
            self.client.annotate(
                attempt_id=attempt_id,
                profile_id=CAPTION_PROFILE_ID,
                image=image,
            )

        self.assertEqual(raised.exception.code, "attempt-exists")
        self.assertEqual(len(self.fake_state.prompt_requests), 1)

    def test_get_attempt_returns_unknown_without_resubmitting(self) -> None:
        attempt_id = uuid4()

        state = self.client.get_attempt(attempt_id)

        self.assertIsInstance(state, ComfyAttemptState)
        self.assertEqual(state.status, "unknown")
        self.assertEqual(self.fake_state.prompt_requests, [])

    def test_get_attempt_recovers_queued_metadata_without_resubmitting(self) -> None:
        attempt_id = uuid4()
        profile = self.bridge_config.profiles[CAPTION_PROFILE_ID]
        metadata = {
            "omoide_annotation": {
                "protocol": "omoide-comfy/v1",
                "attempt_id": str(attempt_id),
                "profile_id": CAPTION_PROFILE_ID,
                "image_sha256": "a" * 64,
                "workflow_sha256": profile.workflow_sha256,
                "profile_provenance_sha256": profile.provenance_sha256(),
            }
        }
        self.fake_state.pending.append(
            [0, str(attempt_id), {}, metadata, [CAPTION_OUTPUT_NODE]]
        )

        state = self.client.get_attempt(attempt_id)

        self.assertIsInstance(state, ComfyAttemptState)
        self.assertEqual(state.status, "queued")
        self.assertEqual(state.profile_id, CAPTION_PROFILE_ID)
        self.assertEqual(self.fake_state.prompt_requests, [])

    def test_health_advertises_only_runtime_ready_profiles(self) -> None:
        self.bridge_service._profile_readiness = lambda profile: (
            profile.profile_id == TAGS_PROFILE_ID,
            None if profile.profile_id == TAGS_PROFILE_ID else "artifact-missing:test",
        )

        health = self.client.health()

        self.assertTrue(health.ready)
        self.assertEqual(health.profiles, (TAGS_PROFILE_ID,))
        self.assertEqual(
            health.unavailable_profiles,
            {CAPTION_PROFILE_ID: "artifact-missing:test"},
        )

    def test_ambiguous_submit_is_reconciled_without_duplicate(self) -> None:
        self.fake_state.mode = "ambiguous-success"
        attempt_id = uuid4()

        result = self.client.annotate(
            attempt_id=attempt_id,
            profile_id=CAPTION_PROFILE_ID,
            image=Image.new("RGB", (5, 5)),
        )

        self.assertEqual(result.attempt_id, attempt_id)
        self.assertEqual(len(self.fake_state.prompt_requests), 1)

    def test_ambiguous_absent_submit_is_not_retried_and_cleans_staging(self) -> None:
        self.fake_state.mode = "ambiguous-lost"
        attempt_id = uuid4()

        with self.assertRaises(ComfyAnnotationError) as raised:
            self.client.annotate(
                attempt_id=attempt_id,
                profile_id=CAPTION_PROFILE_ID,
                image=Image.new("RGB", (5, 5)),
            )

        self.assertEqual(raised.exception.code, "submit-unknown")
        self.assertFalse(raised.exception.retryable)
        self.assertEqual(len(self.fake_state.prompt_requests), 1)
        self.assertFalse((self.staging_directory / f"{attempt_id}.jpg").exists())

    def test_accepted_then_http_500_is_reconciled_without_duplicate(self) -> None:
        self.fake_state.mode = "accepted-500"
        attempt_id = uuid4()

        result = self.client.annotate(
            attempt_id=attempt_id,
            profile_id=CAPTION_PROFILE_ID,
            image=Image.new("RGB", (5, 5)),
        )

        self.assertEqual(result.attempt_id, attempt_id)
        self.assertEqual(len(self.fake_state.prompt_requests), 1)

    def test_accepted_then_invalid_json_is_reconciled_without_duplicate(self) -> None:
        self.fake_state.mode = "accepted-invalid-json"
        attempt_id = uuid4()

        result = self.client.annotate(
            attempt_id=attempt_id,
            profile_id=CAPTION_PROFILE_ID,
            image=Image.new("RGB", (5, 5)),
        )

        self.assertEqual(result.attempt_id, attempt_id)
        self.assertEqual(len(self.fake_state.prompt_requests), 1)

    def test_accepted_submit_retries_transient_history_failure(self) -> None:
        self.fake_state.mode = "accepted-history-500"
        attempt_id = uuid4()

        result = self.client.annotate(
            attempt_id=attempt_id,
            profile_id=CAPTION_PROFILE_ID,
            image=Image.new("RGB", (5, 5)),
        )

        self.assertEqual(result.attempt_id, attempt_id)
        self.assertEqual(len(self.fake_state.prompt_requests), 1)
        self.assertEqual(self.fake_state.history_failures_remaining, 0)

    def test_foreign_queue_work_refuses_admission(self) -> None:
        foreign_id = str(uuid4())
        self.fake_state.pending.append([0, foreign_id, {}, {}, []])

        with self.assertRaises(ComfyAnnotationError) as raised:
            self.client.annotate(
                attempt_id=uuid4(),
                profile_id=CAPTION_PROFILE_ID,
                image=Image.new("RGB", (2, 2)),
            )

        self.assertEqual(raised.exception.code, "busy")
        self.assertTrue(raised.exception.retryable)
        self.assertEqual(self.fake_state.prompt_requests, [])

    def test_cancel_targets_only_the_requested_running_prompt(self) -> None:
        self.fake_state.mode = "hold"
        attempt_id = uuid4()
        failure: list[ComfyAnnotationError] = []

        def annotate() -> None:
            try:
                self.client.annotate(
                    attempt_id=attempt_id,
                    profile_id=CAPTION_PROFILE_ID,
                    image=Image.new("RGB", (2, 2)),
                )
            except ComfyAnnotationError as error:
                failure.append(error)

        thread = threading.Thread(target=annotate)
        thread.start()
        self.assertTrue(self.fake_state.prompt_seen.wait(timeout=2))

        cancelled = self.client.cancel(attempt_id=attempt_id)
        thread.join(timeout=3)

        self.assertEqual(cancelled.status, "cancel-requested")
        self.assertEqual(self.fake_state.cancelled_ids, [str(attempt_id)])
        self.assertEqual([error.code for error in failure], ["cancelled"])

    def test_cancel_during_upload_prevents_prompt_submission(self) -> None:
        self.fake_state.mode = "hold-upload"
        attempt_id = uuid4()
        failure: list[ComfyAnnotationError] = []

        def annotate() -> None:
            try:
                self.client.annotate(
                    attempt_id=attempt_id,
                    profile_id=CAPTION_PROFILE_ID,
                    image=Image.new("RGB", (2, 2)),
                )
            except ComfyAnnotationError as error:
                failure.append(error)

        thread = threading.Thread(target=annotate)
        thread.start()
        self.assertTrue(self.fake_state.upload_seen.wait(timeout=2))
        cancelled = self.client.cancel(attempt_id=attempt_id)
        self.fake_state.release_upload.set()
        thread.join(timeout=3)

        self.assertEqual(cancelled.status, "cancel-requested")
        self.assertEqual([error.code for error in failure], ["cancelled"])
        self.assertEqual(self.fake_state.prompt_requests, [])
        self.assertFalse((self.staging_directory / f"{attempt_id}.jpg").exists())

    def test_pre_submission_cancel_tombstone_blocks_late_worker(self) -> None:
        attempt_id = uuid4()

        cancelled = self.client.cancel(attempt_id=attempt_id)
        with self.assertRaises(ComfyAnnotationError) as raised:
            self.client.annotate(
                attempt_id=attempt_id,
                profile_id=CAPTION_PROFILE_ID,
                image=Image.new("RGB", (2, 2)),
            )

        self.assertEqual(cancelled.status, "cancel-requested")
        self.assertEqual(raised.exception.code, "attempt-exists")
        self.assertEqual(self.fake_state.prompt_requests, [])


class BridgeConfigurationTests(unittest.TestCase):
    def test_app_client_deadline_exceeds_bridge_deadline_and_margin(self) -> None:
        example = json.loads(
            Path("integrations/comfyui/bridge/config.example.json").read_text(
                encoding="utf-8"
            )
        )
        bridge_deadline = max(
            profile["timeout_seconds"] + profile["reconcile_seconds"]
            for profile in example["profiles"].values()
        )

        self.assertGreaterEqual(
            AnnotationSettings().inference_timeout_seconds,
            bridge_deadline + 120,
        )

    def test_example_pins_final_workflow_digests_and_bindings(self) -> None:
        example = json.loads(
            Path(
                "integrations/comfyui/bridge/config.example.json"
            ).read_text(encoding="utf-8")
        )

        caption = example["profiles"][CAPTION_PROFILE_ID]
        tags = example["profiles"][TAGS_PROFILE_ID]
        self.assertEqual(
            caption["workflow_sha256"],
            "a120a3a58837248eded8429632d45bbd06d547711d44d93892fdc69e39929a03",
        )
        self.assertEqual(caption["image_node_id"], CAPTION_IMAGE_NODE)
        self.assertEqual(caption["output_node_id"], CAPTION_OUTPUT_NODE)
        self.assertEqual(
            tags["workflow_sha256"],
            "76fdd0daca41ad3bf6e8409a1e98e72bc08a1fdf5f32475e26e7e2296c00a1fc",
        )
        self.assertEqual(tags["image_node_id"], TAGS_IMAGE_NODE)
        self.assertEqual(tags["output_node_id"], TAGS_OUTPUT_NODE)

    def test_profile_provenance_pins_executor_and_selection_policy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            staging = root / "input" / "omoide"
            staging.mkdir(parents=True)
            config = load_config(
                write_profile_config(
                    root,
                    "http://127.0.0.1:8188",
                    staging,
                )
            )

        provenance = config.profiles[TAGS_PROFILE_ID].provenance()
        executor_names = {
            item["name"] for item in provenance["executor"]["artifacts"]
        }
        self.assertIn("omoide_annotation/core.py", executor_names)
        self.assertEqual(
            provenance["selection"]["general_threshold"], TAG_GENERAL_THRESHOLD
        )
        self.assertEqual(
            provenance["selection"]["character_threshold"], TAG_CHARACTER_THRESHOLD
        )
        self.assertEqual(provenance["runtime"]["comfy_version"], "0.34.0")

    def test_readiness_fails_closed_when_executor_source_changes(self) -> None:
        class ReadyCatalog:
            @staticmethod
            def system_stats() -> dict:
                return {"system": {"comfyui_version": "0.34.0"}}

            @staticmethod
            def object_info(node_class: str) -> dict:
                return {node_class: {"input": {"required": {}}}}

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            staging = root / "input" / "omoide"
            staging.mkdir(parents=True)
            config = load_config(
                write_profile_config(
                    root,
                    "http://127.0.0.1:8188",
                    staging,
                )
            )
            source = root / "executor.py"
            source.write_bytes(b"reviewed")
            executor = ArtifactBinding(
                path=source,
                bytes_expected=8,
                sha256=hashlib.sha256(b"reviewed").hexdigest(),
                name="executor.py",
            )
            profile = replace(
                config.profiles[TAGS_PROFILE_ID],
                artifacts=(),
                executor_artifacts=(executor,),
            )
            service = BridgeService(config, http_client=ReadyCatalog())

            self.assertEqual(service._default_profile_readiness(profile), (True, None))
            source.write_bytes(b"modified")
            self.assertEqual(
                service._default_profile_readiness(profile),
                (False, "executor-source-mismatch:executor.py"),
            )

    def test_workflow_digest_mismatch_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            staging = root / "input" / "omoide"
            staging.mkdir(parents=True)
            config_path = write_profile_config(
                root,
                "http://127.0.0.1:8188",
                staging,
            )
            config = json.loads(config_path.read_text())
            config["profiles"][CAPTION_PROFILE_ID]["workflow_sha256"] = "0" * 64
            config_path.write_text(json.dumps(config))

            with self.assertRaises(BridgeError) as raised:
                load_config(config_path)

        self.assertEqual(raised.exception.code, "configuration-error")

    def test_profile_cannot_repin_an_alternate_workflow_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            staging = root / "input" / "omoide"
            staging.mkdir(parents=True)
            config_path = write_profile_config(
                root,
                "http://127.0.0.1:8188",
                staging,
            )
            config = json.loads(config_path.read_text())
            canonical = PROFILE_BINDINGS[CAPTION_PROFILE_ID].workflow_path
            alternate = root / "alternate-caption.json"
            alternate.write_bytes(canonical.read_bytes())
            config["profiles"][CAPTION_PROFILE_ID]["workflow_path"] = str(alternate)
            config_path.write_text(json.dumps(config))

            with self.assertRaises(BridgeError) as raised:
                load_config(config_path)

        self.assertEqual(raised.exception.code, "configuration-error")

    def test_trusted_workflow_contains_every_required_execution_node(self) -> None:
        for profile_id, binding in PROFILE_BINDINGS.items():
            workflow_data = json.loads(binding.workflow_path.read_text())
            for node_id, node_class in binding.required_workflow_nodes:
                self.assertIn(node_id, workflow_data, profile_id)
                self.assertEqual(
                    workflow_data[node_id].get("class_type"),
                    node_class,
                    profile_id,
                )

    def test_non_loopback_comfy_url_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            staging = root / "input" / "omoide"
            staging.mkdir(parents=True)
            config_path = write_profile_config(
                root,
                "http://127.0.0.1:8188",
                staging,
            )
            config = json.loads(config_path.read_text())
            config["comfy_base_url"] = "http://example.com:8188"
            config_path.write_text(json.dumps(config))

            with self.assertRaises(BridgeError) as raised:
                load_config(config_path)

        self.assertEqual(raised.exception.code, "configuration-error")

    def test_locked_caption_binding_cannot_be_overridden(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            staging = root / "input" / "omoide"
            staging.mkdir(parents=True)
            config_path = write_profile_config(
                root,
                "http://127.0.0.1:8188",
                staging,
            )
            config = json.loads(config_path.read_text())
            config["profiles"][CAPTION_PROFILE_ID]["output_node_id"] = "1"
            config_path.write_text(json.dumps(config))

            with self.assertRaises(BridgeError) as raised:
                load_config(config_path)

        self.assertEqual(raised.exception.code, "configuration-error")


if __name__ == "__main__":
    unittest.main()
