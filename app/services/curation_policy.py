"""Shared fixture/production authority, including legacy HTTP stamp protection."""
import hashlib
import json
import os
import re
from datetime import UTC, datetime

from fastapi import HTTPException
from starlette.responses import JSONResponse
from sqlmodel import Session, select

from app.config import settings
from app.curation_models import CurationDataset, CurationGrant

POLICY_VERSION = 'fixture-stills-v1'
PRODUCTION_POLICY_VERSION = 'production-stills-v1'


def mode() -> str:
    configured = os.environ.get('OMOIDE_CURATION_MODE')
    if configured is not None:
        return configured if configured in {'fixture', 'production'} else 'disabled'
    return 'fixture' if os.environ.get('OMOIDE_CURATION_FIXTURES') == '1' else 'disabled'


def enabled() -> bool:
    return mode() != 'disabled'


def current_policy_version() -> str:
    return PRODUCTION_POLICY_VERSION if mode() == 'production' else POLICY_VERSION


def fail(code: str, status: int = 409):
    raise HTTPException(status, detail={'code': code})


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def authorize(session: Session, token: str, dataset_id: str | None = None,
              operation: str = 'read') -> CurationGrant:
    active_mode = mode()
    if active_mode == 'disabled':
        fail('not_found', 404)
    if not isinstance(token, str) or not 16 <= len(token) <= 256:
        fail('unauthorized', 401)
    grant = session.exec(select(CurationGrant).where(
        CurationGrant.token_sha256 == digest(token.encode())
    ).execution_options(populate_existing=True)).first()
    if grant is None or grant.revoked or grant.expires_at <= datetime.now(UTC).replace(tzinfo=None):
        fail('unauthorized', 401)
    allowed_kinds = {'human', 'agent'} if active_mode == 'production' else {'fixture_human', 'agent'}
    dataset = session.get(CurationDataset, grant.dataset_id, populate_existing=True)
    if grant.actor_kind not in allowed_kinds or dataset is None or dataset.policy_version != current_policy_version():
        fail('authority_mode_mismatch', 403)
    if dataset.policy.get('fixture_only') is not (active_mode == 'fixture'):
        fail('authority_mode_mismatch', 403)
    if dataset_id is not None and grant.dataset_id != dataset_id:
        fail('not_found', 404)
    if operation not in grant.operations:
        fail('operation_forbidden', 403)
    if operation not in {'read', 'preview'} and settings.general.presentation_mode:
        fail('presentation_mode', 403)
    if operation in {'review', 'enroll'} and grant.actor_kind != ('human' if active_mode == 'production' else 'fixture_human'):
        fail('review_authority_required', 403)
    if operation == 'preview' and not grant.disclosure:
        fail('disclosure_forbidden', 403)
    return grant


def dataset_for(session: Session, token: str, dataset_id: str,
                operation: str = 'read') -> tuple[CurationDataset, CurationGrant]:
    grant = authorize(session, token, dataset_id, operation)
    dataset = session.get(CurationDataset, dataset_id, populate_existing=True)
    if dataset is None:
        fail('not_found', 404)
    if dataset.policy_version != current_policy_version():
        fail('policy_stale')
    return dataset, grant


def require_revision(dataset: CurationDataset, expected: int):
    if dataset.revision != expected:
        fail('revision_conflict')


_ITEM_PATCH = re.compile(r'^/api/datasets/[^/]+/items/[^/]+$')
_LEGACY_STAMPS = (
    ('PATCH', re.compile(r'^/api/datasets/[^/]+/items/[^/]+/caption$')),
    ('POST', re.compile(r'^/api/datasets/[^/]+/items/[^/]+/(?:caption/reviewed|review)$')),
    ('POST', re.compile(r'^/api/annotations/annotations/[^/]+/(?:revisions|approve)$')),
)


async def _bounded_body(request):
    length = request.headers.get('content-length')
    if length and (not length.isdigit() or int(length) > 262144):
        return None
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > 262144:
            return None
        chunks.append(chunk)
    request._body = b''.join(chunks)
    return request._body


def install_curation_guard(app):
    @app.middleware('http')
    async def curation_authority_guard(request, call_next):
        path = request.url.path.rstrip('/')
        active_mode = mode()
        method = request.method.upper()
        def denial(code, status=403):
            return JSONResponse({'detail': {'code': code}}, status)
        if active_mode != 'disabled':
            if active_mode == 'fixture' and method in {'POST', 'PUT', 'PATCH', 'DELETE'}:
                if path.startswith('/api/') and not path.startswith('/api/curation/'):
                    return denial('legacy_human_authority_unavailable')
            if active_mode == 'production':
                # Independent of credentials: dropping an agent header must not
                # turn an anonymous legacy API call into purported human review.
                if any(method == verb and pattern.fullmatch(path) for verb, pattern in _LEGACY_STAMPS):
                    return denial('legacy_human_authority_unavailable')
                if method == 'PATCH' and _ITEM_PATCH.fullmatch(path):
                    body = await _bounded_body(request)
                    if body is None:
                        return denial('request_too_large', 413)
                    try:
                        payload = json.loads(body)
                    except (ValueError, UnicodeDecodeError):
                        return denial('invalid_request', 422)
                    if isinstance(payload, dict) and 'reviewed_at' in payload:
                        return denial('legacy_human_authority_unavailable')
            if path.startswith('/api/curation/') and method in {'POST', 'PUT', 'PATCH'}:
                if await _bounded_body(request) is None:
                    return denial('request_too_large', 413)
        response = await call_next(request)
        if path.startswith('/api/curation/'):
            response.headers['Cache-Control'] = 'no-store'
        return response
