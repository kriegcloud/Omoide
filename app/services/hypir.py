"""Synchronous hosted restoration. Never include remote bodies or credentials in errors."""
from __future__ import annotations
import base64
import binascii
import io
import json
from types import SimpleNamespace
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, HTTPRedirectHandler
from PIL import Image
from app.services.comfy_annotation import ComfyAnnotationError


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


# Do not forward the key across a hosted-service redirect.
urlopen = build_opener(_NoRedirect).open


class HypirClient:
    def __init__(self, api_key: str):
        self._api_key = api_key

    def repair(self, provider: str, image: Image.Image, params: dict):
        if provider not in {'hypir:ultra', 'hypir:balance'} or not self._api_key:
            raise ComfyAnnotationError('hypir-unavailable', 'Hosted upscaler is unavailable')
        encoded = io.BytesIO()
        image.save(encoded, 'PNG')
        body = {key: value for key, value in params.items() if key not in {'provider', 'hosted_consent', 'scale'}}
        body['upscale'] = params.get('scale', 2)
        body['input'] = 'data:application/octet-stream;base64,' + base64.b64encode(encoded.getvalue()).decode('ascii')
        # Level fields are the public HYPIR API contract; the service maps them
        # to internal sharpness/noise/blur controls.
        request = Request('https://hypir.api.suppixel.ai/' + provider.split(':')[1], data=json.dumps(body).encode(), headers={'X-API-KEY':self._api_key, 'Content-Type':'application/json'}, method='POST')
        try:
            with urlopen(request, timeout=600) as response:
                result = json.load(response)
        except HTTPError as exc:
            code = exc.code
            exc.close()
            raise ComfyAnnotationError(f'hypir-http-{code}', f'Hosted upscaler returned HTTP {code}', retryable=code == 429 or code >= 500) from None
        except (URLError, TimeoutError, OSError):
            raise ComfyAnnotationError('hypir-transport', 'Hosted upscaler request failed', retryable=True) from None
        except (ValueError, TypeError):
            raise ComfyAnnotationError('hypir-invalid-response', 'Hosted upscaler returned invalid JSON') from None
        try:
            uri = result['output']
            header, encoded_image = uri.split(',', 1)
            if not header.startswith('data:') or not header.endswith(';base64'):
                raise ValueError('Invalid data URI')
            data = base64.b64decode(encoded_image, validate=True)
            with Image.open(io.BytesIO(data)) as opened:
                opened.verify()
            with Image.open(io.BytesIO(data)) as opened:
                opened.load()
        except (KeyError, TypeError, AttributeError, ValueError, binascii.Error, OSError, Image.DecompressionBombError):
            raise ComfyAnnotationError('hypir-invalid-image', 'Hosted upscaler returned an invalid image') from None
        return SimpleNamespace(image=data)
