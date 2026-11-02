"""Metadata and bounded manual/retry endpoints for shared brand assets."""
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from sqlmodel import Session

from app.config import require_mutation_allowed, settings
from app.database import get_session
from app.models import SocialBrand
from app.schemas.person import SocialBrandRead
from app.services.social_brands import MAX_BYTES, resolve_brand, sanitize_image, store_icon

router = APIRouter(dependencies=[Depends(require_mutation_allowed)])


def _brand(domain: str, session: Session):
    brand = session.get(SocialBrand, domain.lower())
    if brand is None:
        raise HTTPException(404, 'Unknown social brand')
    return brand


@router.get('/{domain}', response_model=SocialBrandRead)
def get_brand(domain: str, session: Session = Depends(get_session)):
    return _brand(domain, session)


@router.get('/{domain}/icon')
def get_icon(domain: str, session: Session = Depends(get_session)):
    brand = _brand(domain, session)
    if not brand.icon_path:
        raise HTTPException(404, 'Brand icon not available')
    path = Path(brand.icon_path).resolve()
    directory = (settings.general.thumb_dir / 'social-brands').resolve()
    if not path.is_relative_to(directory) or not path.is_file():
        raise HTTPException(404, 'Brand icon not available')
    return FileResponse(path, media_type='image/svg+xml' if path.suffix == '.svg' else 'image/png', headers={
        'X-Content-Type-Options': 'nosniff',
        'Content-Security-Policy': "default-src 'none'; sandbox",
        'Cache-Control': 'public, max-age=300',
    })


@router.post('/{domain}/retry', response_model=SocialBrandRead)
def retry_icon(domain: str, background_tasks: BackgroundTasks, session: Session = Depends(get_session)):
    brand = _brand(domain, session)
    # Retrying does not erase a manually supplied asset.
    if brand.source == 'manual':
        return brand
    brand.status, brand.error = 'pending', None
    session.add(brand)
    session.commit()
    background_tasks.add_task(resolve_brand, brand.domain)
    return brand


@router.post('/{domain}/icon', response_model=SocialBrandRead)
async def upload_icon(domain: str, request: Request, session: Session = Depends(get_session)):
    brand = _brand(domain, session)
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_BYTES:
            raise HTTPException(413, 'Icon exceeds 512 KiB')
    kind = request.headers.get('content-type', '').split(';')[0].lower().strip()
    try:
        data, kind = sanitize_image(bytes(body), kind)
    except Exception as error:
        raise HTTPException(422, 'Invalid or unsafe icon image') from error
    brand.icon_path = store_icon(brand.domain, data, kind)
    brand.source, brand.status, brand.error = 'manual', 'ready', None
    brand.simple_icons_slug = None
    brand.fetched_at = datetime.now(timezone.utc)
    session.add(brand)
    session.commit()
    return brand
