"""Bounded HTTP transport to the curation API. No other effect exists here."""
from __future__ import annotations

import json
import logging

import httpx

from .config import Config, load_credential
from .errors import CurationError

logger = logging.getLogger('omoide_curation_mcp.http')

MAX_JSON_BYTES = 8 * 1024 * 1024


class CurationApi:
    """One httpx client, one lazily loaded bearer, verbatim error codes.

    The bearer is read from the configured 0600 file on first authenticated use
    and kept in memory only. It is never logged and never placed in a URL.
    """

    def __init__(self, config: Config) -> None:
        self.config = config
        self._token: str | None = None
        # Process-local replay map for the one mutating route that takes no
        # server-side idempotency key. Never a substitute for durable admission.
        self.caption_replays: dict[tuple[str, str], tuple[str, dict]] = {}
        self._client = httpx.AsyncClient(
            base_url=config.base_url,
            timeout=httpx.Timeout(config.timeout_seconds, connect=10.0),
            follow_redirects=False,
            headers={'Accept': 'application/json'},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    @property
    def credential_configured(self) -> bool:
        return bool(self.config.credential_path)

    def token(self) -> str:
        if self._token is None:
            self._token = load_credential(self.config.credential_path)
            logger.info('loaded curation credential from the configured file')
        return self._token

    def _auth_headers(self) -> dict[str, str]:
        return {'Authorization': 'Bearer ' + self.token()}

    async def request_json(self, method: str, path: str, *, body: dict | None = None,
                           authenticated: bool = True) -> dict | list:
        headers = self._auth_headers() if authenticated else {}
        payload = await self._send(method, path, headers=headers, body=body, max_bytes=MAX_JSON_BYTES)
        try:
            return json.loads(payload.decode('utf-8'))
        except (ValueError, UnicodeDecodeError):
            raise CurationError('invalid_upstream_response', origin='adapter') from None

    async def request_bytes(self, path: str, *, max_bytes: int) -> tuple[bytes, str]:
        headers = self._auth_headers()
        data, media_type = await self._send('GET', path, headers=headers, body=None,
                                            max_bytes=max_bytes, want_media_type=True)
        return data, media_type

    async def _send(self, method: str, path: str, *, headers: dict[str, str], body: dict | None,
                    max_bytes: int, want_media_type: bool = False):
        request = self._client.build_request(method, path, headers=headers,
                                             json=body if body is not None else None)
        try:
            response = await self._client.send(request, stream=True)
        except httpx.HTTPError:
            # Never include the exception text: it can carry the full URL.
            raise CurationError('omoide_unreachable', origin='adapter') from None
        try:
            declared = response.headers.get('content-length')
            if declared and declared.isdigit() and int(declared) > max_bytes:
                raise CurationError('response_too_large', origin='adapter')
            chunks, size = [], 0
            try:
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > max_bytes:
                        raise CurationError('response_too_large', origin='adapter')
                    chunks.append(chunk)
            except httpx.HTTPError:
                raise CurationError('omoide_unreachable', origin='adapter') from None
            payload = b''.join(chunks)
            media_type = response.headers.get('content-type', '').split(';')[0].strip()
            logger.info('%s %s -> %s', method, path, response.status_code)
            if response.status_code >= 400:
                raise self._failure(response.status_code, payload)
            if response.status_code >= 300:
                raise CurationError('unexpected_redirect', response.status_code, origin='adapter')
            return (payload, media_type) if want_media_type else payload
        finally:
            await response.aclose()

    @staticmethod
    def _failure(status: int, payload: bytes) -> CurationError:
        """`{"detail": {"code": ...}}` is relayed verbatim; other shapes are not
        guessed at, and no upstream text is copied into the result."""
        try:
            detail = json.loads(payload.decode('utf-8')).get('detail')
        except (ValueError, UnicodeDecodeError, AttributeError):
            detail = None
        if isinstance(detail, dict) and isinstance(detail.get('code'), str):
            return CurationError(detail['code'], status, origin='omoide')
        if status == 422:
            return CurationError('schema_validation_failed', status, origin='omoide')
        return CurationError('unexpected_upstream_status', status, origin='omoide')
