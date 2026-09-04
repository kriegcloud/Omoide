"""Read-only exports for downstream dataset-curation tools."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlalchemy import union_all
from sqlmodel import Session, select

from app.config import settings
from app.database import get_session
from app.models import Face, Media, Person, PersonMediaLink

router = APIRouter()


class MediaPathExportItem(BaseModel):
    media_id: int
    container_path: str
    host_path: str | None
    kind: Literal["image", "video"]


class PersonPathExport(BaseModel):
    person_id: int
    person_name: str | None
    count: int
    items: list[MediaPathExportItem]


def _host_path(container_path: str) -> str | None:
    path = Path(container_path)
    mappings = (
        (Path("/app/media/T7"), settings.general.media_export_host_root_t7),
        (Path("/app/media/T7XFER"), settings.general.media_export_host_root_t7xfer),
    )
    for container_root, host_root in mappings:
        if host_root is None:
            continue
        try:
            relative = path.relative_to(container_root)
        except ValueError:
            continue
        return str(host_root / relative)
    return None


def _person_media(person_id: int, session: Session) -> tuple[Person, list[Media]]:
    person = session.get(Person, person_id)
    if person is None:
        raise HTTPException(status_code=404, detail="Person not found")
    media_ids = union_all(
        select(Face.media_id.label("media_id")).where(Face.person_id == person_id),
        select(PersonMediaLink.media_id.label("media_id")).where(
            PersonMediaLink.person_id == person_id
        ),
    ).subquery()
    media = session.exec(
        select(Media)
        .where(Media.id.in_(select(media_ids.c.media_id).distinct()))
        .order_by(Media.path)
    ).all()
    return person, list(media)


@router.get("/person/{person_id}/paths", response_model=PersonPathExport)
def export_person_paths(
    person_id: int,
    session: Session = Depends(get_session),
) -> PersonPathExport:
    """Export every catalog path in which a person appears."""
    person, media = _person_media(person_id, session)
    items = [
        MediaPathExportItem(
            media_id=item.id,
            container_path=item.path,
            host_path=_host_path(item.path),
            kind="video" if item.duration is not None else "image",
        )
        for item in media
    ]
    return PersonPathExport(
        person_id=person.id,
        person_name=person.name,
        count=len(items),
        items=items,
    )


@router.get("/person/{person_id}/paths.txt", response_class=PlainTextResponse)
def export_person_paths_text(
    person_id: int,
    host_paths: bool = Query(
        default=True,
        description="Prefer host paths; fall back to container paths when unavailable.",
    ),
    session: Session = Depends(get_session),
) -> str:
    """Export newline-delimited paths suitable for shell and training tools."""
    _, media = _person_media(person_id, session)
    paths = [
        (_host_path(item.path) if host_paths else None) or item.path for item in media
    ]
    return "".join(f"{path}\n" for path in paths)
