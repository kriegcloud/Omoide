import base64
import binascii

from fastapi import (
    APIRouter,
    Body,
    Depends,
    HTTPException,
    Query,
    status,
)
from pydantic import BaseModel
from sqlalchemy import func, or_
from sqlmodel import Session, delete, select, text

from app.config import settings
from app.database import get_session, safe_commit, safe_execute
from app.logger import logger
from app.models import Face, Person, PersonMediaLink, PersonRelationship, PersonTagLink
from app.schemas.face import (
    AssignSuggestedFaces,
    AssignSuggestedFacesResult,
    CursorPage,
    FaceAssign,
    OrphanFaceSuggestion,
    OrphanFaceSuggestionsPage,
    SkippedFaceAssignment,
)
from app.schemas.person import PersonMinimal
from app.services.face_matching import (
    load_prototype_index,
    load_unassigned_face_embeddings,
    score_faces,
)
from app.utils import (
    auto_select_profile_face,
    recalculate_person_appearance_counts,
    update_person_embedding,
)

router = APIRouter()


def _assign_face(
    session: Session, face: Face, person: Person, affected_person_ids: set[int]
) -> None:
    """Apply the shared per-face assignment path for manual review endpoints."""
    original_person_id = face.person_id
    if original_person_id == person.id:
        return
    affected_person_ids.add(person.id)
    if original_person_id is not None:
        affected_person_ids.add(original_person_id)

    face.person = person
    face.person_id = person.id
    session.add(face)
    if original_person_id is not None:
        old_person_can_be_deleted(session, original_person_id)
    update_face_embedding(session, face.id, person.id)


@router.post(
    "/assign",
    summary="Assign existing faces to a person",
    status_code=status.HTTP_200_OK,
)
async def assign_faces(
    body: FaceAssign = Body(...),
    session: Session = Depends(get_session),
):
    if settings.general.presentation_mode:
        raise HTTPException(
            status_code=403,
            detail="Not allowed in settings.general.presentation_mode mode.",
        )
    new_person_id = body.person_id
    new_person = session.get(Person, new_person_id)
    if not new_person:
        raise HTTPException(status_code=404, detail="Person not found")

    affected_person_ids: set[int] = set()
    if new_person_id is not None:
        affected_person_ids.add(new_person_id)

    for face_id in body.face_ids:
        face = session.get(Face, face_id)
        if not face:
            logger.warning(f"Face with ID {face_id} not found, skipping assignment.")
            continue

        _assign_face(session, face, new_person, affected_person_ids)

    recalculate_person_appearance_counts(session, affected_person_ids)
    if new_person_id and new_person_id > 0:
        update_person_embedding(session, new_person_id)
    safe_commit(session)
    return {"message": "Faces assigned successfully"}


@router.post("/assign-suggested", response_model=AssignSuggestedFacesResult)
async def assign_suggested_faces(
    body: AssignSuggestedFaces,
    session: Session = Depends(get_session),
) -> AssignSuggestedFacesResult:
    if settings.general.presentation_mode:
        raise HTTPException(
            status_code=403,
            detail="Not allowed in settings.general.presentation_mode mode.",
        )
    assigned = 0
    skipped: list[SkippedFaceAssignment] = []
    affected_person_ids: set[int] = set()
    for assignment in body.assignments:
        face = session.get(Face, assignment.face_id)
        if face is None:
            reason = "unknown_face"
        elif face.person_id is not None:
            reason = "face_already_assigned"
        elif (person := session.get(Person, assignment.person_id)) is None:
            reason = "unknown_person"
        else:
            _assign_face(session, face, person, affected_person_ids)
            assigned += 1
            continue
        skipped.append(
            SkippedFaceAssignment(face_id=assignment.face_id, reason=reason)
        )

    recalculate_person_appearance_counts(session, affected_person_ids)
    for person_id in sorted(affected_person_ids):
        update_person_embedding(session, person_id)
    safe_commit(session)
    return AssignSuggestedFacesResult(assigned=assigned, skipped=skipped)


@router.post(
    "/detach",
    summary="Detaches existing faces from their persons",
    status_code=status.HTTP_200_OK,
)
async def detach_faces(
    face_ids: list[int] = Body(..., embed=True),
    session: Session = Depends(get_session),
):
    if settings.general.presentation_mode:
        raise HTTPException(
            status_code=403,
            detail="Not allowed in settings.general.presentation_mode mode.",
        )

    affected_person_ids: set[int] = set()

    for face_id in face_ids:
        face = session.get(Face, face_id)
        if not face:
            logger.warning(f"Face with ID {face_id} not found, skipping detachment.")
            continue

        person_id = face.person_id
        if person_id:
            affected_person_ids.add(person_id)

        face.person_id = None
        session.add(face)

        if person_id:
            old_person_can_be_deleted(session, person_id)
        update_face_embedding(
            session, face_id, -1
        )  # -1 detaches face from person in embedding table
        # update embedding of person to fix suggested faces after detach
    recalculate_person_appearance_counts(session, affected_person_ids)
    for pid in affected_person_ids:
        if session.get(Person, pid):
            update_person_embedding(session, pid)
    safe_commit(session)
    return {"message": "Faces detached successfully"}

def update_face_embedding(
    session: Session,
    face_id: int,
    person_id: int | None,
    delete_face: bool = False,
):
    if settings.general.presentation_mode:
        raise HTTPException(
            status_code=403,
            detail="Not allowed in settings.general.presentation_mode mode.",
        )
    if not delete_face and person_id:
        sql = text(
            """
            UPDATE face_embeddings
            set person_id=:p_id
            WHERE face_id=:f_id
            """
        ).bindparams(p_id=person_id, f_id=face_id)
    else:
        sql = text(
            """DELETE FROM face_embeddings
                   WHERE face_id=:f_id"""
        ).bindparams(f_id=face_id)
    safe_execute(session, sql)

@router.delete(
    "/",
    summary="Delete multiple face records (and their thumbnail files)",
    status_code=status.HTTP_200_OK,
)
def delete_faces(
    face_ids: list[int] = Query(..., alias="face_ids"),
    session: Session = Depends(get_session),
):
    if settings.general.presentation_mode:
        raise HTTPException(
            status_code=403,
            detail="Not allowed in settings.general.presentation_mode mode.",
        )
    affected_person_ids: set[int] = set()
    for face_id in face_ids:
        face = session.get(Face, face_id)
        if not face:
            logger.warning(f"Face with ID {face_id} not found, skipping deletion.")
            continue

        # remove thumbnail from disk
        if face.thumbnail_path:
            thumb = settings.general.thumb_dir / face.thumbnail_path
            if thumb.exists():
                thumb.unlink()

        if person := face.person:
            person_id = face.person.id
            affected_person_ids.add(person_id)
        else:
            person_id = None
        session.delete(face)

        update_face_embedding(session, face_id, person_id, delete_face=True)
        if person_id:
            old_person_can_be_deleted(session, person_id)
    recalculate_person_appearance_counts(session, affected_person_ids)
    for pid in affected_person_ids:
        if session.get(Person, pid):
            auto_select_profile_face(session, pid)
            update_person_embedding(session, pid)
    safe_commit(session)
    return {"message": "Faces deleted successfully"}

class FaceCreatePerson(BaseModel):
    name: str | None = None


@router.get("/orphans/suggestions", response_model=OrphanFaceSuggestionsPage)
def get_orphan_face_suggestions(
    session: Session = Depends(get_session),
    cursor: str | None = None,
    limit: int = Query(48, ge=1, le=200),
    min_score: float = Query(0.0, ge=-1.0, le=1.0),
) -> OrphanFaceSuggestionsPage:
    offset = 0
    if cursor:
        try:
            decoded = base64.b64decode(
                cursor, altchars=b"-_", validate=True
            ).decode("ascii")
            if not decoded.isdecimal():
                raise ValueError("Cursor must be a non-negative integer")
            offset = int(decoded)
        except (ValueError, UnicodeError, binascii.Error) as exc:
            raise HTTPException(
                status_code=400, detail="Invalid suggestions cursor"
            ) from exc

    index = load_prototype_index(session)
    face_ids, embeddings, frontalities = load_unassigned_face_embeddings(session)
    ranked = sorted(
        (
            match
            for match in score_faces(index, face_ids, embeddings, frontalities)
            if match.score >= min_score
        ),
        key=lambda match: (-match.score, match.face_id),
    )
    page = ranked[offset : offset + limit]
    faces = {
        face.id: face
        for face in session.exec(
            select(Face).where(Face.id.in_([match.face_id for match in page]))
        ).all()
    }
    next_offset = offset + len(page)
    return OrphanFaceSuggestionsPage(
        items=[
            OrphanFaceSuggestion(
                face=faces[match.face_id],
                person_id=match.person_id,
                person_name=index.names[match.person_id],
                score=match.score,
                pose_bin=match.pose_bin,
            )
            for match in page
        ],
        next_cursor=(
            base64.urlsafe_b64encode(str(next_offset).encode("ascii")).decode("ascii")
            if next_offset < len(ranked)
            else None
        ),
    )


@router.get("/orphans/count", summary="Count faces not assigned to a person")
def get_orphan_count(session: Session = Depends(get_session)) -> dict[str, int]:
    count = session.exec(
        select(func.count()).select_from(Face).where(Face.person_id.is_(None))
    ).one()
    return {"count": count}

@router.get("/orphans", response_model=CursorPage)
def get_orphans(
    session: Session = Depends(get_session),
    cursor: str | None = Query(
        None,
        description=(
            "encoded as `<id>`; e.g. `2025-05-05T12:34:56.789012_1234` or `2500_1234`"
        ),
    ),
    limit: int = 48,
):
    before_id = None
    if cursor:
        before_id = int(cursor)
    query = select(Face).where(Face.person_id.is_(None)).order_by(Face.id.desc())
    if before_id:
        query = query.where(Face.id < before_id)
    orphans = safe_execute(session, query.limit(limit)).all()

    if len(orphans) == limit:
        next_cursor = str(orphans[-1].id)
    else:
        next_cursor = None
    return CursorPage(next_cursor=next_cursor, items=orphans)

@router.post(
    "/create_person",
    summary="Create a new person from multiple faces and assign",
    response_model=PersonMinimal,
    status_code=status.HTTP_201_CREATED,
)
async def create_person_from_faces(
    face_ids: list[int] = Body(..., embed=True),
    name: str | None = Body(None),
    session: Session = Depends(get_session),
):
    if settings.general.presentation_mode:
        raise HTTPException(
            status_code=403,
            detail="Not allowed in settings.general.presentation_mode mode.",
        )
    if not face_ids:
        raise HTTPException(status_code=400, detail="No face IDs provided")

    faces = []
    for face_id in face_ids:
        face = session.get(Face, face_id)
        if not face:
            logger.warning(f"Face with ID {face_id} not found, skipping.")
            continue
        faces.append(face)

    if not faces:
        raise HTTPException(status_code=404, detail="No valid faces found")

    media_ids = {face.media_id for face in faces}

    # Create the Person
    person = Person(
        name=name,
        profile_face_id=faces[0].id,  # Set profile to the first face
        appearance_count=len(media_ids),
    )
    session.add(person)
    session.flush()
    person_id = person.id

    previous_person_ids: set[int] = set()
    for face in faces:
        previous_person = face.person
        face.person_id = person_id
        session.add(face)
        if previous_person and previous_person.id != person_id:
            previous_person_ids.add(previous_person.id)
            old_person_can_be_deleted(session, previous_person.id)
        update_face_embedding(session, face.id, person_id)
    target_person_ids = set(previous_person_ids)
    target_person_ids.add(person_id)
    recalculate_person_appearance_counts(session, target_person_ids)
    if person_id:
        update_person_embedding(session, person_id)
    safe_commit(session)
    session.close()
    return PersonMinimal(id=person_id)

def old_person_can_be_deleted(session: Session, person_id: int | None):
    if person_id is None:
        return True
    remaining = safe_execute(
        session, select(Face).where(Face.person_id == person_id)
    ).first()
    if remaining:
        return False
    remaining_manual_link = safe_execute(
        session,
        select(PersonMediaLink).where(PersonMediaLink.person_id == person_id),
    ).first()
    if remaining_manual_link:
        return False

    # delete any tag links
    safe_execute(
        session,
        delete(PersonTagLink).where(PersonTagLink.person_id == person_id),
    )
    person = session.get(Person, person_id)
    session.exec(
        delete(PersonRelationship).where(
            or_(
                PersonRelationship.person_a_id == person_id,
                PersonRelationship.person_b_id == person_id,
            )
        )
    )
    session.delete(person)
    safe_commit(session)
    return True
