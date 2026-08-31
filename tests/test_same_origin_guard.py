import os
import tempfile
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

_CONFIG_HOME = tempfile.TemporaryDirectory()
os.environ["XDG_CONFIG_HOME"] = _CONFIG_HOME.name
os.environ.pop("OMOIDE_WORKSTATION_BROWSER_HARDENING", None)

from app.main import (  # noqa: E402
    _workstation_browser_hardening_enabled,
    enforce_same_origin_browser_requests,
)


def make_test_client(
    *,
    base_url: str = "http://127.0.0.1:8123",
    hardened: bool = True,
) -> TestClient:
    app = FastAPI()
    if hardened:
        app.middleware("http")(enforce_same_origin_browser_requests)

    @app.api_route(
        "/probe",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    )
    async def probe() -> dict[str, bool]:
        return {"accepted": True}

    return TestClient(app, base_url=base_url)


class SameOriginGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = make_test_client()

    def assert_forbidden(self, response) -> None:
        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            response.json(),
            {
                "detail": {
                    "code": "same_origin_required",
                    "message": (
                        "Unsafe browser requests must use the application's "
                        "origin."
                    ),
                }
            },
        )
        self.assertEqual(
            response.headers["content-type"],
            "application/json",
        )

    def test_same_origin_post_is_allowed(self) -> None:
        response = self.client.post(
            "/probe",
            headers={
                "Origin": "http://127.0.0.1:8123",
                "Sec-Fetch-Site": "same-origin",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"accepted": True})

    def test_default_and_explicit_ports_are_normalized(self) -> None:
        default_port_client = make_test_client(
            base_url="http://127.0.0.1",
        )
        response = default_port_client.put(
            "/probe",
            headers={"Origin": "http://127.0.0.1:80"},
        )

        self.assertEqual(response.status_code, 200)

        self.assert_forbidden(
            default_port_client.put(
                "/probe",
                headers={"Origin": "http://127.0.0.1:81"},
            )
        )

    def test_cross_origin_form_post_is_forbidden(self) -> None:
        response = self.client.post(
            "/probe",
            headers={"Origin": "https://attacker.example"},
            data={"action": "scan"},
        )

        self.assert_forbidden(response)

    def test_null_origin_is_forbidden(self) -> None:
        self.assert_forbidden(
            self.client.post("/probe", headers={"Origin": "null"})
        )

    def test_malformed_origin_is_forbidden(self) -> None:
        self.assert_forbidden(
            self.client.post("/probe", headers={"Origin": "not-an-origin"})
        )

    def test_referer_only_requests_are_compared_by_origin(self) -> None:
        with self.subTest("same origin"):
            response = self.client.post(
                "/probe",
                headers={
                    "Referer": "http://127.0.0.1:8123/gallery?page=2"
                },
            )
            self.assertEqual(response.status_code, 200)

        with self.subTest("cross origin"):
            self.assert_forbidden(
                self.client.post(
                    "/probe",
                    headers={"Referer": "https://attacker.example/form"},
                )
            )

    def test_cross_site_fetch_metadata_is_forbidden(self) -> None:
        self.assert_forbidden(
            self.client.delete(
                "/probe",
                headers={"Sec-Fetch-Site": "cross-site"},
            )
        )

    def test_forwarded_headers_do_not_override_the_request_origin(self) -> None:
        self.assert_forbidden(
            self.client.post(
                "/probe",
                headers={
                    "Origin": "https://attacker.example",
                    "X-Forwarded-Host": "attacker.example",
                    "X-Forwarded-Proto": "https",
                },
            )
        )

    def test_each_browser_origin_header_must_be_same_origin(self) -> None:
        self.assert_forbidden(
            self.client.post(
                "/probe",
                headers={
                    "Origin": "http://127.0.0.1:8123",
                    "Referer": "https://attacker.example/form",
                },
            )
        )

    def test_non_browser_client_without_browser_headers_is_allowed(self) -> None:
        response = self.client.patch("/probe")

        self.assertEqual(response.status_code, 200)

    def test_hostile_host_is_forbidden_even_when_origin_matches(self) -> None:
        response = self.client.post(
            "/probe",
            headers={
                "Host": "attacker.example",
                "Origin": "http://attacker.example",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json(),
            {
                "detail": {
                    "code": "invalid_host",
                    "message": "The request Host must be a loopback address.",
                }
            },
        )

    def test_loopback_hosts_with_optional_ports_are_allowed(self) -> None:
        for host in (
            "127.0.0.1",
            "127.0.0.1:8123",
            "localhost",
            "localhost:9123",
            "[::1]",
            "[::1]:8123",
        ):
            with self.subTest(host=host):
                response = self.client.get(
                    "/probe",
                    headers={"Host": host},
                )
                self.assertEqual(response.status_code, 200)

        response = self.client.post(
            "/probe",
            headers={
                "Host": "[::1]:8123",
                "Origin": "http://[::1]:8123",
            },
        )
        self.assertEqual(response.status_code, 200)

    def test_browser_hardening_is_opt_in_for_upstream_compatibility(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(_workstation_browser_hardening_enabled())

        with patch.dict(
            os.environ,
            {"OMOIDE_WORKSTATION_BROWSER_HARDENING": "true"},
            clear=True,
        ):
            self.assertTrue(_workstation_browser_hardening_enabled())

        unhardened_client = make_test_client(hardened=False)
        response = unhardened_client.post(
            "/probe",
            headers={
                "Host": "attacker.example",
                "Origin": "http://localhost:5173",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("content-security-policy", response.headers)
        self.assertNotIn("x-frame-options", response.headers)

    def test_hardened_responses_cannot_be_framed(self) -> None:
        response = self.client.get("/probe")

        self.assertEqual(
            response.headers["content-security-policy"],
            "frame-ancestors 'none'",
        )
        self.assertEqual(
            response.headers["cross-origin-resource-policy"],
            "same-origin",
        )
        self.assertEqual(response.headers["x-frame-options"], "DENY")

    def test_safe_get_is_allowed_regardless_of_browser_origin(self) -> None:
        response = self.client.get(
            "/probe",
            headers={
                "Origin": "https://attacker.example",
                "Sec-Fetch-Site": "cross-site",
            },
        )

        self.assertEqual(response.status_code, 200)

    def test_options_preflight_is_allowed(self) -> None:
        response = self.client.options(
            "/probe",
            headers={
                "Origin": "https://attacker.example",
                "Sec-Fetch-Site": "cross-site",
            },
        )

        self.assertEqual(response.status_code, 200)


if __name__ == "__main__":
    unittest.main()
