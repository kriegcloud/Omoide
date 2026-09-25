"""Curation API. All writes also authorize inside the service."""
from fastapi import APIRouter, Depends, Request, Response
from sqlmodel import Session, select

from app.curation_models import CurationArtifact, CurationDataset, CurationSource
from app.database import get_session
from app.schemas.curation import CaptionInput, EmptyInput, ExportInput, MaterializeInput, RegistrationInput, ReviewInput
from app.services import curation_auth as auth
from app.services import curation_plans as plans
from app.services.curation_artifacts import artifact_bytes, source_bytes
from app.services.curation_policy import authorize, dataset_for, enabled, fail, mode
from app.services.frozen_exports import admit_export, execute_export, get_export

router = APIRouter()


def bearer(request: Request) -> str:
    auth = request.headers.get('authorization', '')
    if not auth.startswith('Bearer ') or len(auth) > 256:
        fail('unauthorized', 401)
    return auth[7:]


@router.get('/status')
def status():
    return {'enabled': enabled(), 'mode': mode(), 'fixture_only': mode() == 'fixture', 'generative_enabled': False,
            'human_presence_verified': False}


@router.get('/auth/status')
def auth_status(token: str = Depends(bearer), session: Session = Depends(get_session)):
    return auth.auth_status(session, token)


@router.post('/auth/registration/options')
def registration_options(request: EmptyInput, token: str = Depends(bearer), session: Session = Depends(get_session)):
    return auth.registration_options(session, token)


@router.post('/auth/registration/verify')
def registration_verify(request: RegistrationInput, token: str = Depends(bearer), session: Session = Depends(get_session)):
    return auth.registration_verify(session, token, request)


@router.get('/datasets')
def datasets(token: str = Depends(bearer), session: Session = Depends(get_session)):
    grant = authorize(session, token)
    dataset, _ = dataset_for(session, token, grant.dataset_id)
    return [{'id': dataset.id, 'name': dataset.name, 'revision': dataset.revision}]


@router.get('/datasets/{dataset_id}')
def dataset_detail(dataset_id: str, token: str = Depends(bearer), session: Session = Depends(get_session)):
    return plans.detail(session, token, dataset_id)


@router.post('/datasets/{dataset_id}/materialize')
def materialize(dataset_id: str, request: MaterializeInput, token: str = Depends(bearer), session: Session = Depends(get_session)):
    return plans.materialize(session, token, dataset_id, request)


@router.post('/datasets/{dataset_id}/captions')
def captions(dataset_id: str, request: CaptionInput, token: str = Depends(bearer), session: Session = Depends(get_session)):
    return plans.add_caption(session, token, dataset_id, request)


@router.post('/datasets/{dataset_id}/reviews')
def reviews(dataset_id: str, request: ReviewInput, token: str = Depends(bearer), session: Session = Depends(get_session)):
    return plans.review(session, token, dataset_id, request)


@router.post('/datasets/{dataset_id}/review-options')
def review_options(dataset_id: str, request: ReviewInput, token: str = Depends(bearer), session: Session = Depends(get_session)):
    return auth.review_options(session, token, dataset_id, request)


@router.post('/datasets/{dataset_id}/exports')
def export(dataset_id: str, request: ExportInput, token: str = Depends(bearer), session: Session = Depends(get_session)):
    operation_id = admit_export(session, token, dataset_id, request)
    return execute_export(session, token, operation_id)


@router.get('/exports/{operation_id}')
def export_receipt(operation_id: str, token: str = Depends(bearer), session: Session = Depends(get_session)):
    return get_export(session, token, operation_id)


@router.post('/exports/{operation_id}/resume')
def resume(operation_id: str, token: str = Depends(bearer), session: Session = Depends(get_session)):
    return execute_export(session, token, operation_id)


@router.get('/artifacts/{artifact_id}/content')
def artifact_content(artifact_id: str, token: str = Depends(bearer), session: Session = Depends(get_session)):
    grant = authorize(session, token, operation='preview')
    dataset, _ = dataset_for(session, token, grant.dataset_id, 'preview')
    artifact = plans.artifact_for(session, dataset, artifact_id)
    return Response(artifact_bytes(dataset, artifact), media_type='image/png',
                    headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})


@router.get('/sources/{source_id}/content')
def source_content(source_id: str, token: str = Depends(bearer), session: Session = Depends(get_session)):
    grant = authorize(session, token, operation='preview')
    dataset, _ = dataset_for(session, token, grant.dataset_id, 'preview')
    source = plans.source_for(session, dataset, source_id)
    data = source_bytes(dataset, source)
    # Only safe supported raster types, never serve fixture HTML/SVG as media.
    import io
    from PIL import Image, UnidentifiedImageError
    try:
        with Image.open(io.BytesIO(data)) as image:
            mime = {'PNG': 'image/png', 'JPEG': 'image/jpeg', 'WEBP': 'image/webp'}.get(image.format)
        if not mime:
            fail('unsupported_format')
    except (UnidentifiedImageError, OSError):
        fail('corrupt_or_unsupported_image')
    return Response(data, media_type=mime, headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})
