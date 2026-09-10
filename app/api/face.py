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
from sqlalchemy import func
from sqlalchemy.dialects.sqlite import insert
from sqlmodel import Session, delete, select, text, update

from app.config import settings
from app.database import get_session, safe_commit, safe_execute
from app.logger import logger
from app.models import Face, FaceAssignmentSource, FaceSuggestionRejection, Person, PersonMediaLink
from app.schemas.face import (
    AssignSuggestedFaces,
    AssignSuggestedFacesResult,
    CursorPage,
    FaceAssign,
    OrphanFaceSuggestion,
    OrphanFaceSuggestionsPage,
    SkippedFaceAssignment,
    RecentFaceAssignment,
)
from app.schemas.person import PersonMinimal
from app.services.face_matching import (
    load_prototype_index,
    load_suggestion_rejections,
    load_unassigned_face_embeddings,
    score_faces,
)
from app.services.face_provenance import stamp_face_assignment
from app.utils import (
    log_person_deleted,
    refresh_persons,
    remove_person,
)

router = APIRouter()


@router.get("/assignments/recent", response_model=list[RecentFaceAssignment])
def recent_face_assignments(
    session: Session = Depends(get_session),
    limit: int = 50,
    source: FaceAssignmentSource | None = None,
    person_id: int | None = None,
) -> list[RecentFaceAssignment]:
    query = (
        select(Face, Person.name)
        .outerjoin(Person, Face.person_id == Person.id)
        .where(Face.assigned_at.is_not(None))
    )
    if source is not None:
        query = query.where(Face.assignment_source == source.value)
    if person_id is not None:
        query = query.where(Face.person_id == person_id)
    rows = session.exec(
        query.order_by(Face.assigned_at.desc(), Face.id.desc()).limit(
            max(1, min(limit, 500))
        )
    ).all()
    return [
        RecentFaceAssignment(
            id=face.id,
            media_id=face.media_id,
            person_id=face.person_id,
            person_name=person_name,
            thumbnail_path=face.thumbnail_path,
            assigned_at=face.assigned_at,
            assignment_source=face.assignment_source,
        )
        for face, person_name in rows
    ]


def _assign_face(
    session: Session, face: Face, person: Person, affected_person_ids: set[int],
    source: FaceAssignmentSource = FaceAssignmentSource.MANUAL,
) -> None:
    """Apply the shared per-face assignment path for manual review endpoints."""
    original_person_id = face.person_id
    if original_person_id == person.id:
        return
    affected_person_ids.add(person.id)
    if original_person_id is not None:
        affected_person_ids.add(original_person_id)

    face.person = person
    stamp_face_assignment(face, person.id, source)
    session.add(face)
    if original_person_id is not None:
        old_person_can_be_deleted(session, original_person_id, reason="faces-assign")
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

        _assign_face(
            session, face, new_person, affected_person_ids, FaceAssignmentSource(body.source)
        )

    refresh_persons(session, affected_person_ids)
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
            _assign_face(
                session, face, person, affected_person_ids, FaceAssignmentSource.SUGGESTION
            )
            assigned += 1
            continue
        skipped.append(
            SkippedFaceAssignment(face_id=assignment.face_id, reason=reason)
        )

    refresh_persons(session, affected_person_ids)
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

        stamp_face_assignment(face, None, FaceAssignmentSource.DETACH)
        session.add(face)

        if person_id:
            old_person_can_be_deleted(session, person_id, reason="faces-detach")
        update_face_embedding(
            session, face_id, -1
        )  # -1 detaches face from person in embedding table
        # update embedding of person to fix suggested faces after detach
    refresh_persons(session, affected_person_ids)
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
    _delete_face_batch(session, list(dict.fromkeys(face_ids)))
    return {"message": "Faces deleted successfully"}


def _delete_face_batch(session: Session, face_ids: list[int], *, only_orphans: bool = False) -> int:
    conditions = [Face.id.in_(face_ids)]
    if only_orphans:
        conditions.append(Face.person_id.is_(None))
    # Acquire a SQLite write lease before reading the faces to clean. Assignment
    # writers cannot race between this predicate check and vector/row removal.
    session.exec(update(Face).where(*conditions).values(id=Face.id))
    faces = session.exec(select(Face).where(*conditions)).all()
    ids = [face.id for face in faces]
    affected_person_ids = {face.person_id for face in faces if face.person_id is not None}
    profile_person_ids = session.exec(select(Person.id).where(Person.profile_face_id.in_(ids))).all()
    affected_person_ids.update(profile_person_ids)
    thumbnails = [settings.general.thumb_dir / face.thumbnail_path for face in faces if face.thumbnail_path]
    # Profile references must be cleared before a face delete can autoflush.
    session.exec(update(Person).where(Person.profile_face_id.in_(ids)).values(profile_face_id=None))
    for face in faces:
        update_face_embedding(session, face.id, face.person_id, delete_face=True)
    session.exec(delete(Face).where(Face.id.in_(ids)))
    deleted_people = []
    for person_id in sorted(affected_person_ids):
        person = session.get(Person, person_id)
        if old_person_can_be_deleted(session, person_id, reason="faces-delete", commit=False) and person is not None:
            deleted_people.append(person)
    refresh_persons(session, affected_person_ids)
    safe_commit(session)
    for person in deleted_people:
        log_person_deleted(person, reason="empty-after-faces-delete")
    for thumbnail in thumbnails:
        try:
            thumbnail.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            logger.warning("Could not remove face thumbnail %s: %s", thumbnail, exc)
    return len(ids)


class FaceCreatePerson(BaseModel):
    name: str | None = None


class FaceSuggestionRejectionRequest(BaseModel):
    person_id: int


def _validate_suggestion_rejection(
    session: Session, face_id: int, person_id: int
) -> None:
    if settings.general.presentation_mode:
        raise HTTPException(status_code=403, detail="Not allowed in presentation mode.")
    if session.get(Face, face_id) is None:
        raise HTTPException(status_code=404, detail="Face not found")
    if session.get(Person, person_id) is None:
        raise HTTPException(status_code=404, detail="Person not found")


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
            for match in score_faces(
                index, face_ids, embeddings, frontalities,
                rejected_persons=load_suggestion_rejections(session, face_ids),
            )
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

@router.delete("/orphans", summary="Delete all unassigned face records")
def delete_all_orphans(session: Session = Depends(get_session)) -> dict[str, int]:
    if settings.general.presentation_mode:
        raise HTTPException(status_code=403, detail="Not allowed in presentation mode.")
    count = session.exec(select(func.count(Face.id)).where(Face.person_id.is_(None))).one()
    if count > 20_000:
        raise HTTPException(status_code=409, detail="More than 20000 orphan faces; delete in batches using face selection.")
    deleted = 0
    last_id = 0
    while True:
        ids = session.exec(
            select(Face.id).where(Face.person_id.is_(None), Face.id > last_id)
            .order_by(Face.id).limit(500)
        ).all()
        if not ids:
            break
        deleted += _delete_face_batch(session, ids, only_orphans=True)
        last_id = ids[-1]
    return {"deleted": deleted}


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
        stamp_face_assignment(face, person_id, FaceAssignmentSource.MANUAL)
        session.add(face)
        if previous_person and previous_person.id != person_id:
            previous_person_ids.add(previous_person.id)
            old_person_can_be_deleted(session, previous_person.id, reason="face-reassign")
        update_face_embedding(session, face.id, person_id)
    target_person_ids = set(previous_person_ids)
    target_person_ids.add(person_id)
    refresh_persons(session, target_person_ids)
    safe_commit(session)
    session.close()
    return PersonMinimal(id=person_id)

def old_person_can_be_deleted(
    session: Session, person_id: int | None, *, reason: str, commit: bool = True
):
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

    person = session.get(Person, person_id)
    if person is None:
        return True

    remove_person(person_id, session, reason=f"empty-after-{reason}", commit=commit)
    return True


@router.post("/{face_id}/reject-suggestion")
def reject_face_suggestion(
    face_id: int,
    body: FaceSuggestionRejectionRequest,
    session: Session = Depends(get_session),
) -> dict[str, int | bool]:
    """Remember a review decision without changing the face's ownership."""
    _validate_suggestion_rejection(session, face_id, body.person_id)
    session.exec(
        insert(FaceSuggestionRejection)
        .values(face_id=face_id, person_id=body.person_id)
        .on_conflict_do_nothing(index_elements=["face_id", "person_id"])
    )
    safe_commit(session)
    return {"face_id": face_id, "person_id": body.person_id, "rejected": True}


@router.delete("/{face_id}/reject-suggestion")
def undo_face_suggestion_rejection(
    face_id: int,
    body: FaceSuggestionRejectionRequest,
    session: Session = Depends(get_session),
) -> dict[str, int | bool]:
    _validate_suggestion_rejection(session, face_id, body.person_id)
    session.exec(
        delete(FaceSuggestionRejection).where(
            FaceSuggestionRejection.face_id == face_id,
            FaceSuggestionRejection.person_id == body.person_id,
        )
    )
    safe_commit(session)
    return {"face_id": face_id, "person_id": body.person_id, "rejected": False}
