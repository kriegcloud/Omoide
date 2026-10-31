"""Single service authority for the isolated fixture still slice.

A fixture_human credential tests an authority path, NOT human presence. There is
no generative acceptance route. Credentials are issued only by the local fixture
bootstrap helper, never from client supplied identity or an HTTP endpoint.
"""
import hashlib
import os
from datetime import UTC, datetime

from fastapi import HTTPException
from starlette.responses import JSONResponse
from sqlmodel import Session, select

from app.config import settings
from app.curation_models import CurationDataset, CurationGrant

POLICY_VERSION = 'fixture-stills-v1'


def enabled() -> bool:
    return os.environ.get('OMOIDE_CURATION_FIXTURES') == '1'


def fail(code: str, status: int = 409):
    raise HTTPException(status, detail={'code': code})


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def authorize(session: Session, token: str, dataset_id: str | None = None,
              operation: str = 'read') -> CurationGrant:
    if not enabled():
        fail('not_found', 404)
    grant = session.exec(select(CurationGrant).where(
        CurationGrant.token_sha256 == digest(token.encode())
    ).execution_options(populate_existing=True)).first()
    if grant is None or grant.revoked or grant.expires_at <= datetime.now(UTC).replace(tzinfo=None):
        fail('unauthorized', 401)
    if dataset_id is not None and grant.dataset_id != dataset_id:
        fail('not_found', 404)
    if operation not in grant.operations:
        fail('operation_forbidden', 403)
    if operation not in {'read', 'preview'} and settings.general.presentation_mode:
        fail('presentation_mode', 403)
    if operation == 'review' and grant.actor_kind != 'fixture_human':
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
    if dataset.policy_version != POLICY_VERSION:
        fail('policy_stale')
    return dataset, grant


def require_revision(dataset: CurationDataset, expected: int):
    if dataset.revision != expected:
        fail('revision_conflict')


def install_curation_guard(app):
    @app.middleware('http')
    async def curation_authority_guard(request, call_next):
        path = request.url.path.rstrip('/')
        if enabled():
            if request.method in {'POST', 'PUT', 'PATCH', 'DELETE'}:
                # No human-presence integration exists. During fixture mode ALL
                # legacy human stamping is disabled, including anonymous calls:
                # an agent cannot simply remove its Authorization header.
                if path.startswith('/api/') and not path.startswith('/api/curation/'):
                    return JSONResponse({'detail': {'code': 'legacy_human_authority_unavailable'}}, 403)
            if path.startswith('/api/curation/') and request.method in {'POST', 'PUT', 'PATCH'}:
                length = request.headers.get('content-length')
                if length and (not length.isdigit() or int(length) > 262144):
                    return JSONResponse({'detail': {'code': 'request_too_large'}}, 413)
                # Enforce chunked bodies too, before Pydantic parses them.
                size = 0
                chunks = []
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > 262144:
                        return JSONResponse({'detail': {'code': 'request_too_large'}}, 413)
                    chunks.append(chunk)
                request._body = b''.join(chunks)
        return await call_next(request)
