"""Regression: enabled curation must never let legacy routes mint human stamps."""
import os
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient


class LegacyAuthorityGapTests(unittest.TestCase):
    def test_legacy_human_stamp_requires_central_authority(self):
        # Exercise the actual production middleware installer if present. The old
        # stack had no such guard: a route ran even for a caller claiming agent.
        app = FastAPI()
        try:
            from app.services.curation_policy import install_curation_guard
        except ImportError:
            pass
        else:
            install_curation_guard(app)

        @app.post('/api/annotations/annotations/example/revisions')
        def legacy_stamp():
            return {'author': 'human'}

        with patch.dict(os.environ, {'OMOIDE_CURATION_FIXTURES': '1'}):
            result = TestClient(app).post(
                '/api/annotations/annotations/example/revisions',
                headers={'Authorization': 'Bearer forged-agent', 'X-Actor-Type': 'human'},
                json={'actor_type': 'human'},
            )
        self.assertEqual(result.status_code, 403, result.text)
