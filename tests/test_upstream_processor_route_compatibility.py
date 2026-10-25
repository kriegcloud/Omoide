"""Existing media processor discovery remains available after upstream sync."""
import importlib
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from fastapi import FastAPI


class ProcessorRouteCompatibility(unittest.IsolatedAsyncioTestCase):
    async def test_media_processor_listing_returns_registered_names_without_inference(self):
        processors_api = importlib.import_module('app.api.processors')
        app = FastAPI()
        app.include_router(processors_api.router, prefix='/api')
        registered = [SimpleNamespace(name='exif'), SimpleNamespace(name='faces')]
        with patch.object(processors_api, 'load_processors', return_value=registered) as loader:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                response = await client.get('/api/media/123/processors')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), ['exif', 'faces'])
        loader.assert_called_once_with()
