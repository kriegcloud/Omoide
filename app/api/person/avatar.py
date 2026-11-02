"""Face candidates for the person profile picker, with stable keyset ordering."""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import selectinload
from sqlmodel import Session, select

from app.api._media_filters import exclude_missing
from app.database import get_session
from app.models import Face, Media, Person
from app.pagination import paginate
from app.schemas.face import CursorPage, FaceRead

router = APIRouter()


@router.get("/{person_id}/avatar-faces", response_model=CursorPage)
def avatar_faces(
    person_id: int,
    sort: Literal["quality", "newest"] = "quality",
    cursor: str | None = None,
    limit: int = Query(default=40, ge=1, le=100),
    session: Session = Depends(get_session),
):
    if not session.get(Person, person_id):
        raise HTTPException(404, "Person not found")
    statement = exclude_missing(select(Face).join(Media).options(selectinload(Face.media)).where(
        Face.person_id == person_id, Face.ignored_at.is_(None),
    ))
    quality = func.coalesce(Face.det_score, 0.0) * func.coalesce(Face.frontality, 0.0)
    keys = [(quality, False), (Face.id, False)] if sort == "quality" else [(Media.created_at, False), (Face.id, False)]
    faces, next_cursor = paginate(session, statement, keys, cursor=cursor, limit=limit,
                                  tag=f"avatar:{person_id}:{sort}")
    return CursorPage(items=[FaceRead.model_validate(face) for face in faces], next_cursor=next_cursor)
