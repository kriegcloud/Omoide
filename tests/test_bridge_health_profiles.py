import unittest
from unittest.mock import patch
from uuid import uuid4

from app.services.comfy_annotation import ComfyAnnotationClient
from app.services.comfy_repair import ComfyRepairClient, parse_repair_health
from integrations.comfyui.bridge.service import LEGACY_HEALTH_PROFILES, filter_health

SEEDVR2 = "omoide-upscale-seedvr2-v1"


def bridge_health():
    configured = sorted([*LEGACY_HEALTH_PROFILES, SEEDVR2])
    return {
        "ok": True,
        "protocol": "omoide-comfy/v1",
        "action": "health",
        "ready": True,
        "profiles": configured,
        "configured_profiles": configured,
        "profile_result_kinds": {p: ("image" if "upscale" in p else "text") for p in configured},
        "profile_input_kinds": {p: "image" for p in configured},
        "unavailable_profiles": {},
        "active_attempt_id": None,
        "comfy_url": "http://127.0.0.1:8188",
    }


class BridgeHealthProfileTests(unittest.TestCase):
    def test_plain_health_hides_profiles_old_clients_cannot_parse(self):
        reply = filter_health(bridge_health(), LEGACY_HEALTH_PROFILES)
        self.assertNotIn(SEEDVR2, reply["profiles"])
        self.assertNotIn(SEEDVR2, reply["configured_profiles"])
        self.assertNotIn(SEEDVR2, reply["profile_result_kinds"])
        client = ComfyAnnotationClient.__new__(ComfyAnnotationClient)
        with patch.object(ComfyAnnotationClient, "_request", return_value=reply):
            health = client.health()
        self.assertTrue(health.ready)

    def test_repair_client_names_its_profiles_and_sees_seedvr2(self):
        sent = {}

        def fake_request(_self, payload):
            sent.update(payload)
            return filter_health(bridge_health(), frozenset(payload["profiles"]))

        client = ComfyRepairClient.__new__(ComfyRepairClient)
        with patch.object(ComfyRepairClient, "_request", fake_request):
            health = client.health()
        self.assertIn(SEEDVR2, sent["profiles"])
        self.assertIn(SEEDVR2, health.profiles)
        self.assertNotIn("omoide-caption-v1", health.profiles)
        self.assertEqual(health.profile_result_kinds[SEEDVR2], "image")

    def test_repair_health_rejects_malformed_reply(self):
        bad = bridge_health()
        bad["profiles"] = "omoide-upscale-v1"
        from app.services.comfy_annotation import ComfyAnnotationError

        with self.assertRaises(ComfyAnnotationError):
            parse_repair_health(bad, frozenset({"omoide-upscale-v1"}))
        ok = bridge_health()
        ok["active_attempt_id"] = str(uuid4())
        self.assertIsNotNone(parse_repair_health(ok, frozenset({SEEDVR2})).active_attempt_id)


if __name__ == "__main__":
    unittest.main()
