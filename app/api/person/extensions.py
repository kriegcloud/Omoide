from ._validation import validate_profile_face

from collections import deque
from datetime import UTC, date, datetime
from pathlib import Path
import shutil

from fastapi import (
    APIRouter,
    Body,
    Depends,
    HTTPException,
    Query,
    Request,
    status,
)
from sqlalchemy import (
    and_,
    desc,
    func,
    literal_column,
    or_,
    tuple_,
    union_all,
)
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, delete, distinct, select, text, update

from app.api._media_filters import exclude_missing
from app.config import require_mutation_allowed, settings
from app.database import get_session, safe_commit, safe_execute
from app.logger import logger
from app.models import (
    Face,
    FaceAssignmentSource,
    Media,
    Person,
    PersonMediaLink,
    PersonPairDecision,
    PersonRelationship,
    PersonSocialLink,
    PersonTagLink,
    Tag,
    TimelineEvent,
    TrainingDataset,
)
from app.schemas.face import CursorPage as FaceCursorPage
from app.schemas.person import (
    CursorPage,
    DetachedFace,
    FaceRead,
    MediaCursorPage,
    MergeCandidate,
    MergeCandidatesPage,
    MergePersonsBulkRequest,
    MergePersonsRequest,
    MergePersonsResult,
    PersonBulkDeleteRequest,
    PersonBulkDeleteResponse,
    PersonBulkHideRequest,
    PersonBulkHideResponse,
    PersonBulkUnhideResponse,
    PersonDetail,
    PersonMediaBulkAttachResponse,
    PersonMediaBulkDetachResponse,
    PersonMediaBulkRequest,
    PersonMediaExportRequest,
    PersonMediaReassignRequest,
    PersonMediaReassignResponse,
    PersonPairDecisionCreate,
    PersonPairDecisionRead,
    PersonRead,
    PersonReadSimple,
    PersonUpdate,
    ProfileFace,
    RelationshipEdge,
    RelationshipGraph,
    RelationshipNode,
    SimilarPerson,
    SocialLinkCreate,
    SocialLinkRead,
    SocialLinkSuggestion,
)
from app.schemas.timeline import (
    TimelineEventCreate,
    TimelineEventUpdate,
    TimelinePage,
)
from app.services.social_links import derive_url, normalize_handle, suggest_from_paths
from app.services.face_provenance import face_assignment_values, stamp_face_assignment
from app.utils import (
    _distance_to_similarity,
    auto_select_profile_face,
    get_person_embedding,
    log_person_deleted,
    recalculate_person_appearance_counts,
    remove_person,
    update_person_demographics,
    update_person_embedding,
)

router = APIRouter(dependencies=[Depends(require_mutation_allowed)])
merge_queue_router = APIRouter(dependencies=[Depends(require_mutation_allowed)])


def _require_person(person_id: int, session: Session) -> Person:
    person = session.get(Person, person_id)
    if person is None:
        raise HTTPException(status_code=404, detail="Person not found")
    return person


def _require_mutations_enabled() -> None:
    if settings.general.presentation_mode:
        raise HTTPException(
            status_code=403,
            detail="Not allowed in settings.general.presentation_mode mode.",
        )


@merge_queue_router.get("/merge-candidates", response_model=MergeCandidatesPage)
def get_merge_candidates(
    min_similarity: float = Query(default=80.0, ge=-100.0, le=100.0),
    limit: int = Query(default=50, ge=1, le=200),
    session: Session = Depends(get_session),
):
    # Stored person embeddings are unit vectors. Use the same L2-to-cosine
    # percentage (and rounding) as get_similarities, without a per-person KNN
    # cutoff that could hide pairs after decisions/hidden people are filtered.
    rows = session.exec(
        text(
            """
            WITH pair_distances AS (
                SELECT a.person_id AS person_a_id, b.person_id AS person_b_id,
                       MIN(vec_distance_L2(a.embedding, b.embedding)) AS distance
                FROM person_embeddings AS a
                JOIN person_embeddings AS b ON a.person_id < b.person_id
                JOIN person AS pa ON pa.id = a.person_id
                JOIN person AS pb ON pb.id = b.person_id
                WHERE pa.hidden_at IS NULL AND pb.hidden_at IS NULL
                  AND NOT EXISTS (
                      SELECT 1 FROM person_pair_decision AS decision
                      WHERE decision.person_a_id = a.person_id
                        AND decision.person_b_id = b.person_id
                  )
                GROUP BY a.person_id, b.person_id
            ), scored AS (
                SELECT person_a_id, person_b_id,
                       ROUND((1.0 - distance * distance / 2.0) * 100, 2) AS similarity
                FROM pair_distances
            )
            SELECT person_a_id, person_b_id, similarity FROM scored
            WHERE similarity >= :min_similarity
            ORDER BY similarity DESC, person_a_id, person_b_id
            LIMIT :limit
            """
        ).bindparams(min_similarity=min_similarity, limit=limit)
    ).all()
    if not rows:
        return MergeCandidatesPage(items=[])

    person_ids = {pid for row in rows for pid in (row.person_a_id, row.person_b_id)}
    people = {
        person.id: PersonRead.model_validate(person)
        for person in session.exec(select(Person).where(Person.id.in_(person_ids))).all()
    }
    # Count distinct shared media, including manual appearances and multiple
    # faces/frames of the same person in one media item only once.
    appearances = session.exec(
        union_all(
            select(Face.person_id, Face.media_id).where(Face.person_id.in_(person_ids)),
            select(PersonMediaLink.person_id, PersonMediaLink.media_id).where(
                PersonMediaLink.person_id.in_(person_ids)
            ),
        )
    ).all()
    media_by_person = {pid: set() for pid in person_ids}
    for person_id, media_id in appearances:
        media_by_person[person_id].add(media_id)
    return MergeCandidatesPage(
        items=[
            MergeCandidate(
                person_a=people[row.person_a_id],
                person_b=people[row.person_b_id],
                similarity=row.similarity,
                shared_media=len(
                    media_by_person[row.person_a_id] & media_by_person[row.person_b_id]
                ),
            )
            for row in rows
        ]
    )


@merge_queue_router.get("/pair-decisions", response_model=list[PersonPairDecisionRead])
def get_pair_decisions(session: Session = Depends(get_session)):
    return session.exec(
        select(PersonPairDecision).order_by(
            PersonPairDecision.created_at.desc(), PersonPairDecision.id.desc()
        )
    ).all()


@merge_queue_router.post("/pair-decisions", response_model=PersonPairDecisionRead)
def create_pair_decision(
    body: PersonPairDecisionCreate,
    session: Session = Depends(get_session),
):
    _require_mutations_enabled()
    if body.person_a_id == body.person_b_id:
        raise HTTPException(status_code=400, detail="Person IDs must differ")
    person_a_id, person_b_id = sorted((body.person_a_id, body.person_b_id))
    _require_person(person_a_id, session)
    _require_person(person_b_id, session)
    # The unique pair makes retries (including reversed IDs) atomic and keeps
    # the original row ID and creation time for the future undo list.
    statement = insert(PersonPairDecision).values(
        person_a_id=person_a_id,
        person_b_id=person_b_id,
        decision=body.decision,
    )
    safe_execute(
        session,
        statement.on_conflict_do_update(
            index_elements=["person_a_id", "person_b_id"],
            set_={"decision": statement.excluded.decision},
        ),
    )
    safe_commit(session)
    return session.exec(
        select(PersonPairDecision).where(
            PersonPairDecision.person_a_id == person_a_id,
            PersonPairDecision.person_b_id == person_b_id,
        )
    ).one()


@merge_queue_router.delete("/pair-decisions/{id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_pair_decision(id: int, session: Session = Depends(get_session)):
    _require_mutations_enabled()
    decision = session.get(PersonPairDecision, id)
    if decision is None:
        raise HTTPException(status_code=404, detail="Pair decision not found")
    session.delete(decision)
    safe_commit(session)


def _is_local_request(request: Request) -> bool:
    try:
        host = request.client.host if request.client else ""
    except Exception:
        return False
    return host in {"127.0.0.1", "::1", "localhost"}


def _resolve_media_source(path_value: str) -> Path | None:
    """Resolve a stored media path against the configured media directories."""
    raw_path = Path(path_value).expanduser()
    candidates = [raw_path] if raw_path.is_absolute() else []
    candidates.extend(
        media_dir / raw_path
        for media_dir, _read_only in settings.general.resolved_media_dirs()
    )
    for candidate in candidates:
        try:
            resolved = candidate.resolve(strict=False)
        except (OSError, RuntimeError):
            continue
        if resolved.is_file():
            return resolved
    return None


def _available_destination(destination_dir: Path, filename: str) -> Path:
    """Return a non-existing destination path without overwriting a file."""
    requested = Path(filename)
    candidate = destination_dir / requested.name
    if not candidate.exists():
        return candidate

    stem = requested.stem or "media"
    suffix = requested.suffix
    index = 1
    while True:
        candidate = destination_dir / f"{stem} ({index}){suffix}"
        if not candidate.exists():
            return candidate
        index += 1


# Timeline events


@router.post(
    "/{person_id}/media/bulk",
    response_model=PersonMediaBulkAttachResponse,
)
def attach_media_to_person_bulk(
    person_id: int,
    body: PersonMediaBulkRequest,
    session: Session = Depends(get_session),
):
    if settings.general.presentation_mode:
        raise HTTPException(status_code=403, detail="Not allowed in presentation mode.")
    if session.get(Person, person_id) is None:
        raise HTTPException(status_code=404, detail="Person not found")

    added_ids: list[int] = []
    skipped_ids: list[int] = []
    for media_id in dict.fromkeys(body.media_ids):
        if session.get(Media, media_id) is None:
            skipped_ids.append(media_id)
            continue
        has_face = session.exec(
            select(Face.id).where(
                Face.person_id == person_id,
                Face.media_id == media_id,
            )
        ).first()
        existing_link = session.exec(
            select(PersonMediaLink).where(
                PersonMediaLink.person_id == person_id,
                PersonMediaLink.media_id == media_id,
            )
        ).first()
        if has_face:
            if existing_link:
                session.delete(existing_link)
            skipped_ids.append(media_id)
            continue
        if existing_link:
            skipped_ids.append(media_id)
            continue
        session.add(PersonMediaLink(person_id=person_id, media_id=media_id))
        added_ids.append(media_id)

    recalculate_person_appearance_counts(session, [person_id])
    safe_commit(session)
    return PersonMediaBulkAttachResponse(
        added_ids=added_ids,
        skipped_ids=skipped_ids,
    )


@router.post(
    "/{person_id}/media/bulk-detach",
    response_model=PersonMediaBulkDetachResponse,
)
def detach_media_from_person_bulk(
    person_id: int,
    body: PersonMediaBulkRequest,
    session: Session = Depends(get_session),
):
    if settings.general.presentation_mode:
        raise HTTPException(status_code=403, detail="Not allowed in presentation mode.")
    if session.get(Person, person_id) is None:
        raise HTTPException(status_code=404, detail="Person not found")

    from app.api.face import update_face_embedding

    detached_ids: list[int] = []
    skipped_ids: list[int] = []
    detached_faces: list[DetachedFace] = []
    for media_id in dict.fromkeys(body.media_ids):
        if session.get(Media, media_id) is None:
            skipped_ids.append(media_id)
            continue
        faces = session.exec(
            select(Face).where(
                Face.person_id == person_id,
                Face.media_id == media_id,
            )
        ).all()
        link = session.exec(
            select(PersonMediaLink).where(
                PersonMediaLink.person_id == person_id,
                PersonMediaLink.media_id == media_id,
            )
        ).first()
        if not faces and link is None:
            skipped_ids.append(media_id)
            continue
        for face in faces:
            detached_faces.append(DetachedFace(id=face.id, media_id=face.media_id))
            stamp_face_assignment(face, None, FaceAssignmentSource.DETACH)
            session.add(face)
            update_face_embedding(session, face.id, -1)
        if link:
            session.delete(link)
        detached_ids.append(media_id)

    recalculate_person_appearance_counts(session, [person_id])
    if session.get(Person, person_id):
        update_person_embedding(session, person_id)
    safe_commit(session)
    return PersonMediaBulkDetachResponse(
        detached_ids=detached_ids,
        skipped_ids=skipped_ids,
        detached_faces=detached_faces,
    )


@router.post(
    "/{person_id}/media/{media_id}/reassign",
    response_model=PersonMediaReassignResponse,
)
def reassign_media_to_person(
    person_id: int,
    media_id: int,
    body: PersonMediaReassignRequest,
    session: Session = Depends(get_session),
):
    if settings.general.presentation_mode:
        raise HTTPException(status_code=403, detail="Not allowed in presentation mode.")
    if body.target_person_id == person_id:
        raise HTTPException(status_code=400, detail="Target must differ from source")
    if session.get(Person, person_id) is None:
        raise HTTPException(status_code=404, detail="Person not found")
    target = session.get(Person, body.target_person_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Target person not found")

    from app.api.face import old_person_can_be_deleted, update_face_embedding

    faces = session.exec(
        select(Face).where(
            Face.person_id == person_id,
            Face.media_id == media_id,
        )
    ).all()
    source_link = session.exec(
        select(PersonMediaLink).where(
            PersonMediaLink.person_id == person_id,
            PersonMediaLink.media_id == media_id,
        )
    ).first()
    reassigned = False
    if faces:
        for face in faces:
            stamp_face_assignment(face, body.target_person_id, FaceAssignmentSource.MERGE)
            session.add(face)
            update_face_embedding(session, face.id, body.target_person_id)
        if source_link:
            session.delete(source_link)
        reassigned = True
    elif source_link:
        target_link = session.exec(
            select(PersonMediaLink).where(
                PersonMediaLink.person_id == body.target_person_id,
                PersonMediaLink.media_id == media_id,
            )
        ).first()
        if target_link:
            session.delete(source_link)
        else:
            source_link.person_id = body.target_person_id
            session.add(source_link)
        reassigned = True

    if reassigned:
        recalculate_person_appearance_counts(
            session, {person_id, body.target_person_id}
        )
        old_person_can_be_deleted(session, person_id, reason="person-media-reassign")
        for affected_id in (person_id, body.target_person_id):
            if session.get(Person, affected_id):
                update_person_embedding(session, affected_id)
        safe_commit(session)

    return PersonMediaReassignResponse(
        media_id=media_id,
        source_person_id=person_id,
        target_person_id=body.target_person_id,
        reassigned=reassigned,
    )


@router.get("/all-simple", response_model=list[PersonReadSimple])
def get_all_persons_simple(session: Session = Depends(get_session)):
    """Returns a lightweight list of all persons for filter selections."""
    people = session.exec(
        select(Person).where(Person.hidden_at.is_(None)).order_by(Person.name)
    ).all()
    return [PersonReadSimple.model_validate(p) for p in people]


@router.get(
    "/{person_id}/social-links",
    response_model=list[SocialLinkRead],
)
def get_social_links(
    person_id: int,
    session: Session = Depends(get_session),
) -> list[PersonSocialLink]:
    _require_person(person_id, session)
    return list(
        session.exec(
            select(PersonSocialLink)
            .where(PersonSocialLink.person_id == person_id)
            .order_by(PersonSocialLink.created_at, PersonSocialLink.id)
        ).all()
    )


@router.post(
    "/{person_id}/social-links",
    response_model=SocialLinkRead,
    status_code=status.HTTP_201_CREATED,
)
def add_social_link(
    person_id: int,
    data: SocialLinkCreate,
    session: Session = Depends(get_session),
) -> PersonSocialLink:
    _require_mutations_enabled()
    _require_person(person_id, session)
    handle = normalize_handle(data.platform, data.handle)
    if not handle:
        raise HTTPException(status_code=400, detail="Handle is required")
    explicit_url = data.url.strip() if data.url else ""
    if data.platform == "other" and not explicit_url:
        raise HTTPException(
            status_code=400,
            detail="Platform 'other' requires an explicit URL",
        )
    url = explicit_url or derive_url(data.platform, handle)
    duplicate = session.exec(
        select(PersonSocialLink.id).where(
            PersonSocialLink.person_id == person_id,
            PersonSocialLink.platform == data.platform,
            PersonSocialLink.handle == handle,
        )
    ).first()
    if duplicate is not None:
        raise HTTPException(status_code=409, detail="Social link already exists")

    link = PersonSocialLink(
        person_id=person_id,
        platform=data.platform,
        handle=handle,
        url=url,
    )
    session.add(link)
    try:
        safe_commit(session)
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(
            status_code=409,
            detail="Social link already exists",
        ) from exc
    session.refresh(link)
    return link


@router.delete(
    "/{person_id}/social-links/{link_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_social_link(
    person_id: int,
    link_id: int,
    session: Session = Depends(get_session),
) -> None:
    _require_mutations_enabled()
    _require_person(person_id, session)
    link = session.get(PersonSocialLink, link_id)
    if link is None or link.person_id != person_id:
        raise HTTPException(status_code=404, detail="Social link not found")
    session.delete(link)
    safe_commit(session)


@router.get(
    "/{person_id}/social-links/suggestions",
    response_model=list[SocialLinkSuggestion],
)
def get_social_link_suggestions(
    person_id: int,
    session: Session = Depends(get_session),
) -> list[SocialLinkSuggestion]:
    _require_person(person_id, session)
    media_ids = union_all(
        select(Face.media_id.label("media_id")).where(Face.person_id == person_id),
        select(PersonMediaLink.media_id.label("media_id")).where(
            PersonMediaLink.person_id == person_id
        ),
    ).subquery()
    paths = session.exec(
        select(Media.path).where(
            Media.id.in_(select(media_ids.c.media_id).distinct())
        )
    ).all()
    return suggest_from_paths(paths)


@router.post("/{person_id}/profile_face", response_model=Person)
def set_profile_face(
    person_id: int,
    face_id: int | None = None,
    session: Session = Depends(get_session),
):
    if settings.general.presentation_mode:
        raise HTTPException(
            status_code=403,
            detail="Not allowed in settings.general.presentation_mode mode.",
        )
    person = session.get(Person, person_id)
    if not person:
        raise HTTPException(404, "Person not found")
    validate_profile_face(session, person_id, face_id)
    person.profile_face_id = face_id
    session.add(person)
    safe_commit(session)
    session.refresh(person)
    return person


def _merge_person_into_target(
    session: Session, source_id: int, target_id: int
) -> Person:
    if source_id == target_id:
        raise HTTPException(
            status_code=400, detail="source_id and target_id must differ"
        )

    source = session.get(Person, source_id)
    target = session.get(Person, target_id)
    if not source or not target:
        raise HTTPException(status_code=404, detail="Source or target person not found")

    session.exec(
        update(Face).where(Face.person_id == source_id).values(
            **face_assignment_values(target_id, FaceAssignmentSource.MERGE)
        )
    )
    session.exec(
        text(
            """
            UPDATE face_embeddings
            SET person_id = :target_id
            WHERE person_id = :source_id
            """
        ).bindparams(target_id=target_id, source_id=source_id)
    )
    session.exec(
        update(TimelineEvent)
        .where(TimelineEvent.person_id == source_id)
        .values(person_id=target_id)
    )

    source_tag_ids = set(
        session.exec(
            select(PersonTagLink.tag_id).where(PersonTagLink.person_id == source_id)
        ).all()
    )
    if source_tag_ids:
        target_tag_ids = set(
            session.exec(
                select(PersonTagLink.tag_id).where(PersonTagLink.person_id == target_id)
            ).all()
        )
        for tag_id in source_tag_ids - target_tag_ids:
            session.add(PersonTagLink(person_id=target_id, tag_id=tag_id))
    session.exec(delete(PersonTagLink).where(PersonTagLink.person_id == source_id))

    if target.profile_face_id is None and source.profile_face_id is not None:
        target.profile_face_id = source.profile_face_id

    source_media_ids = set(
        session.exec(
            select(PersonMediaLink.media_id).where(
                PersonMediaLink.person_id == source_id
            )
        ).all()
    )
    if source_media_ids:
        target_media_ids = set(
            session.exec(
                select(PersonMediaLink.media_id).where(
                    PersonMediaLink.person_id == target_id
                )
            ).all()
        )
        for media_id in source_media_ids - target_media_ids:
            session.add(PersonMediaLink(person_id=target_id, media_id=media_id))
    session.exec(delete(PersonMediaLink).where(PersonMediaLink.person_id == source_id))

    relationships_to_merge = session.exec(
        select(PersonRelationship).where(
            or_(
                PersonRelationship.person_a_id == source.id,
                PersonRelationship.person_b_id == source.id,
            )
        )
    ).all()

    existing_cache: dict[tuple[int, int], PersonRelationship] = {}

    for relationship in relationships_to_merge:
        other_id = (
            relationship.person_b_id
            if relationship.person_a_id == source.id
            else relationship.person_a_id
        )

        session.delete(relationship)

        if other_id == target.id:
            continue

        new_a, new_b = sorted((target.id, other_id))
        cache_key = (new_a, new_b)

        existing = existing_cache.get(cache_key)
        if existing is None:
            existing = session.exec(
                select(PersonRelationship).where(
                    and_(
                        PersonRelationship.person_a_id == new_a,
                        PersonRelationship.person_b_id == new_b,
                    )
                )
            ).first()
            if existing:
                existing_cache[cache_key] = existing

        if existing:
            existing.coappearance_count = (existing.coappearance_count or 0) + (
                relationship.coappearance_count or 0
            )
            if relationship.updated_at and (
                existing.updated_at is None
                or relationship.updated_at > existing.updated_at
            ):
                existing.updated_at = relationship.updated_at
                existing.last_media_id = relationship.last_media_id
            elif (
                existing.last_media_id is None
                and relationship.last_media_id is not None
            ):
                existing.last_media_id = relationship.last_media_id
            continue

        new_relationship = PersonRelationship(
            person_a_id=new_a,
            person_b_id=new_b,
            coappearance_count=relationship.coappearance_count,
            last_media_id=relationship.last_media_id,
            updated_at=relationship.updated_at or datetime.utcnow(),
        )
        session.add(new_relationship)
        existing_cache[cache_key] = new_relationship

    # Move ORM relationships as well as their keys so delete-orphan cascade
    # cannot remove a transferred link from an already-loaded source collection.
    target_links = {(link.platform, link.handle) for link in target.social_links}
    for link in list(source.social_links):
        key = (link.platform, link.handle)
        if key in target_links:
            session.delete(link)
        else:
            link.person = target
            target_links.add(key)
    session.exec(
        update(TrainingDataset).where(TrainingDataset.person_id == source_id)
        .values(person_id=target_id)
    )
    session.flush()
    session.expire(source, ["social_links"])

    if target.profile_face_id is None:
        auto_select_profile_face(session, target.id)

    session.delete(source)
    safe_commit(session)
    log_person_deleted(source, reason="merge-source", target_id=target_id)

    update_person_embedding(session, target_id)
    session.exec(
        text(
            """
            DELETE FROM person_embeddings
            WHERE person_id=:p_id
            """
        ).bindparams(p_id=source_id)
    )
    recalculate_person_appearance_counts(session, [target_id])
    safe_commit(session)
    session.refresh(target)
    return target


def _set_person_hidden(
    person_id: int,
    *,
    hidden: bool,
    session: Session,
) -> PersonDetail:
    if settings.general.presentation_mode:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not allowed in settings.general.presentation_mode mode.",
        )

    person = session.get(Person, person_id)
    if not person:
        raise HTTPException(status_code=404, detail="Person not found")

    person.hidden_at = datetime.now(UTC).replace(tzinfo=None) if hidden else None
    session.add(person)
    safe_commit(session)
    session.refresh(person)
    from .core import get_person

    return get_person(person_id, session)


@router.post("/bulk-hide", response_model=PersonBulkHideResponse)
def hide_persons_bulk(
    payload: PersonBulkHideRequest,
    session: Session = Depends(get_session),
):
    if settings.general.presentation_mode:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not allowed in settings.general.presentation_mode mode.",
        )

    hidden_ids: list[int] = []
    skipped_ids: list[int] = []
    for person_id in dict.fromkeys(payload.person_ids):
        person = session.get(Person, person_id)
        if not person or person.hidden_at is not None:
            skipped_ids.append(person_id)
            continue
        person.hidden_at = datetime.now(UTC).replace(tzinfo=None)
        session.add(person)
        hidden_ids.append(person_id)
    safe_commit(session)
    return PersonBulkHideResponse(hidden_ids=hidden_ids, skipped_ids=skipped_ids)


@router.post("/bulk-unhide", response_model=PersonBulkUnhideResponse)
def unhide_persons_bulk(
    payload: PersonBulkHideRequest,
    session: Session = Depends(get_session),
):
    if settings.general.presentation_mode:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not allowed in settings.general.presentation_mode mode.",
        )

    unhidden_ids: list[int] = []
    skipped_ids: list[int] = []
    for person_id in dict.fromkeys(payload.person_ids):
        person = session.get(Person, person_id)
        if not person or person.hidden_at is None:
            skipped_ids.append(person_id)
            continue
        person.hidden_at = None
        session.add(person)
        unhidden_ids.append(person_id)
    safe_commit(session)
    return PersonBulkUnhideResponse(
        unhidden_ids=unhidden_ids,
        skipped_ids=skipped_ids,
    )


@router.post("/{person_id}/hide", response_model=PersonDetail)
def hide_person(person_id: int, session: Session = Depends(get_session)):
    return _set_person_hidden(person_id, hidden=True, session=session)


@router.post("/{person_id}/unhide", response_model=PersonDetail)
def unhide_person(person_id: int, session: Session = Depends(get_session)):
    return _set_person_hidden(person_id, hidden=False, session=session)


def _ensure_person_has_no_datasets(session: Session, person_id: int) -> None:
    dependency = session.exec(
        select(TrainingDataset.id).where(TrainingDataset.person_id == person_id).limit(1)
    ).first()
    if dependency is not None:
        raise HTTPException(
            409, f"Person is the subject of dataset {dependency}; reassign or delete the dataset first."
        )
