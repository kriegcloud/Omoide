"""Shared validation for person identity references in public mutations."""
from fastapi import HTTPException
from sqlmodel import Session

from app.models import Face


def validate_profile_face(session: Session, person_id: int, face_id: int | None) -> None:
    """A person's avatar may only reference their own face, or be cleared."""
    if face_id is None:
        return
    if type(face_id) is not int or not 1 <= face_id <= 9223372036854775807:
        raise HTTPException(422, "Invalid profile face id")
    face = session.get(Face, face_id)
    if face is None:
        raise HTTPException(404, "Profile face not found")
    if face.person_id != person_id:
        raise HTTPException(400, "Profile face must belong to this person")
