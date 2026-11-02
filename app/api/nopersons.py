from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_
from sqlmodel import Session, select

from app.api._resolve import resolve_media_action
from app.api._media_filters import exclude_missing, folder_filter
from app.database import get_session
from app.models import Media
from app.pagination import count_rows, paginate
from app.schemas.nopersons import NoPersonsMediaItem, NoPersonsPage, NoPersonsResolveRequest

router = APIRouter()


class NoPersonsPageResponse(NoPersonsPage):
    """Totals stay exact by default; opt-in count-free pages report null."""

    total: int | None = None


def _base_filter(media_type: str | None, scope: str):
    if scope == "pending":
        base_filter = Media.persons_detected.is_(False)
    else:
        base_filter = and_(Media.persons_detected.is_(True), Media.has_persons.is_(False))

    if media_type == "image":
        base_filter = and_(base_filter, Media.duration.is_(None))
    elif media_type == "video":
        base_filter = and_(base_filter, Media.duration.isnot(None))
    return base_filter


@router.get("", response_model=NoPersonsPageResponse)
def get_no_persons_media(
    session: Session = Depends(get_session),
    cursor: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    media_type: str | None = Query(None, description="'image', 'video', or omit for all"),
    scope: str = Query("processed", description="'processed' (hybrid scan found no persons) or 'pending'"),
    folder: str | None = None,
    include_total: bool = True,
):
    """Paginated list of confirmed person-free media, or pending hybrid scans."""
    base_filter = _base_filter(media_type, scope)
    base_filter = and_(base_filter, folder_filter(folder), Media.missing_since.is_(None))

    total = count_rows(session, Media.id, base_filter) if include_total else None
    rows, next_cursor = paginate(
        session,
        select(Media).where(base_filter),
        [(Media.id, False)],
        cursor=cursor,
        limit=limit,
    )

    items = [
        NoPersonsMediaItem(
            id=m.id,
            filename=m.filename,
            path=m.path,
            size=m.size or 0,
            thumbnail_path=m.thumbnail_path,
            width=m.width,
            height=m.height,
            duration=m.duration,
            inserted_at=m.inserted_at,
            faces_extracted=m.faces_extracted,
            persons_detected=m.persons_detected,
            has_persons=m.has_persons,
            person_score=m.person_score,
            person_siglip_margin=m.person_siglip_margin,
        )
        for m in rows
    ]

    return NoPersonsPageResponse(items=items, next_cursor=next_cursor, total=total)


@router.post("/resolve")
def resolve_no_persons(
    request: NoPersonsResolveRequest,
    session: Session = Depends(get_session),
):
    return resolve_media_action(
        session,
        action=request.action,
        media_ids=request.media_ids,
        select_all=request.select_all,
        base_filter=and_(_base_filter(request.media_type, request.scope), folder_filter(request.folder), Media.missing_since.is_(None)),
    )
