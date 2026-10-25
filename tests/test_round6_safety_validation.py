"""HTTP boundary validation and presentation guard regression coverage."""
import asyncio
import importlib
import os
import re
import tempfile
import unittest
from unittest.mock import Mock

_CONFIG = tempfile.TemporaryDirectory()
os.environ.setdefault("XDG_CONFIG_HOME", _CONFIG.name)

import httpx
from fastapi import FastAPI
from fastapi.routing import APIRoute
try:
    from fastapi.routing import iter_route_contexts
except ImportError:
    iter_route_contexts = iter
from app.config import settings
from app.database import get_session


class RequestSafetyTests(unittest.IsolatedAsyncioTestCase):
    async def test_openapi_mutations_have_early_presentation_guard(self):
        from app.main import app as production
        # Exercise each production route's actual router/global dependencies
        # with a harmless endpoint: no library mutations occur even before the fix.
        async def unguarded():
            return {"unguarded": True}
        isolated = FastAPI()
        methods = {"POST", "PATCH", "PUT", "DELETE"}
        checked = []
        for route in iter_route_contexts(production.routes):
            if getattr(route, "path", None) not in production.openapi()["paths"]:
                continue
            if not route.path.startswith("/api/") or route.path.startswith("/api/config/") or "annotations" in route.path:
                continue
            for method in sorted(route.methods & methods):
                isolated.add_api_route(route.path, unguarded, methods=[method], dependencies=route.dependencies)
                checked.append((method, route.path))
        self.assertGreater(len(checked), 50)
        previous = settings.general.presentation_mode
        settings.general.presentation_mode = True
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=isolated), base_url="http://test") as client:
                for method, path in checked:
                    with self.subTest(method=method, path=path):
                        response = await client.request(method, re.sub(r"\{[^}]+\}", "1", path))
                        expected = 200 if (method, path) == ("POST", "/api/search/by-image") else 403
                        self.assertEqual(response.status_code, expected, response.text)
        finally:
            settings.general.presentation_mode = previous

    async def test_tag_and_search_limits_and_cursors_return_422(self):
        app = FastAPI()
        app.include_router(importlib.import_module("app.api.tags").router, prefix="/api/tags")
        app.include_router(importlib.import_module("app.api.search").router, prefix="/api/search")
        session = Mock()
        session.exec.return_value.all.return_value = []
        async def get_test_session():
            return session
        app.dependency_overrides[get_session] = get_test_session
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test") as client:
            for path in ("/api/tags/", "/api/search/person", "/api/search/tags"):
                for params in ({"limit": 0}, {"limit": -1}, {"limit": 501}, {"cursor": "broken"}, {"cursor": "-1"}, {"cursor": ""}):
                    with self.subTest(path=path, params=params):
                        response = await client.get(path, params={"query": "x", **params})
                        self.assertEqual(response.status_code, 422, response.text)

    async def test_actual_album_and_person_mutations_fail_before_database_access(self):
        app = FastAPI()
        app.include_router(importlib.import_module("app.api.person").router, prefix="/api/person")
        app.include_router(importlib.import_module("app.api.albums").router, prefix="/api/albums")
        session_calls = []
        async def get_test_session():
            session_calls.append(True)
            return Mock()
        app.dependency_overrides[get_session] = get_test_session
        previous = settings.general.presentation_mode
        settings.general.presentation_mode = True
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                for method, path in (("POST", "/api/albums"), ("PATCH", "/api/albums/1"), ("POST", "/api/albums/1/media"), ("DELETE", "/api/person/1/events/2"), ("PUT", "/api/person/1/events/2"), ("POST", "/api/person/1/events")):
                    with self.subTest(method=method, path=path):
                        response = await client.request(method, path, json={})
                        self.assertEqual(response.status_code, 403, response.text)
            self.assertEqual(session_calls, [])
        finally:
            settings.general.presentation_mode = previous


    async def test_opaque_cursor_keys_reject_invalid_shapes_and_integer_ranges(self):
        from app.pagination import encode_cursor

        app = FastAPI()
        app.include_router(importlib.import_module("app.api.tags").router, prefix="/api/tags")
        app.include_router(importlib.import_module("app.api.search").router, prefix="/api/search")
        session = Mock()
        app.dependency_overrides[get_session] = lambda: session
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            for path in ("/api/tags/", "/api/search/tags", "/api/search/person"):
                person = path.endswith("/person")
                bad_keys = ([[-1, 1], [0, 0], [0, 2**63], [0, "1"], [0, 1.5]] if person
                            else [[0], [-1], [2**63], ["1"], [1.5]])
                bad_keys += [[], [1, 2, 3]]
                for keys in bad_keys:
                    with self.subTest(path=path, keys=keys):
                        response = await client.get(path, params={"query": "x", "cursor": encode_cursor("", keys)})
                        self.assertEqual(response.status_code, 422, response.text)
            session.exec.assert_not_called()

    async def test_timeline_mutations_reject_nonpositive_ids_and_missing_parent(self):
        app = FastAPI()
        app.include_router(importlib.import_module("app.api.person").router, prefix="/api/person")
        session = Mock()
        session.get.return_value = None
        app.dependency_overrides[get_session] = lambda: session
        previous = settings.general.presentation_mode
        settings.general.presentation_mode = False
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                for method, path in (("POST", "/api/person/0/events"),
                                     ("PUT", "/api/person/-1/events/1"),
                                     ("DELETE", "/api/person/1/events/0")):
                    with self.subTest(method=method, path=path):
                        response = await client.request(method, path, json={"title": "Fixture", "event_date": "2026-01-01"})
                        self.assertEqual(response.status_code, 422, response.text)
                session.get.assert_not_called()
                response = await client.post("/api/person/123/events", json={"title": "Fixture", "event_date": "2026-01-01"})
                self.assertEqual(response.status_code, 404, response.text)
                session.add.assert_not_called()
        finally:
            settings.general.presentation_mode = previous
