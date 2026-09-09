"""Keep the latest person assignment and its provenance in the same write."""

from datetime import datetime, timezone

from app.models import Face, FaceAssignmentSource


def face_assignment_values(
    person_id: int | None, source: FaceAssignmentSource
) -> dict[str, int | str | datetime | None]:
    """Build a bulk update; callers must select only changed assignments."""
    return {
        "person_id": person_id,
        "assigned_at": datetime.now(timezone.utc).replace(tzinfo=None),
        "assignment_source": FaceAssignmentSource(source).value,
    }


def stamp_face_assignment(
    face: Face, person_id: int | None, source: FaceAssignmentSource
) -> None:
    """Assign an ORM face, preserving provenance when its person is unchanged."""
    if face.person_id == person_id:
        return
    for field, value in face_assignment_values(person_id, source).items():
        setattr(face, field, value)
