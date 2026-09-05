"""Serialization primitives shared by media deletion and annotation admission."""

from __future__ import annotations

from threading import RLock

from sqlalchemy import update
from sqlmodel import Session

from app.models import Media


# The supported workstation deployment runs one Uvicorn process. This lock
# closes same-process read/write interleavings, while the no-op row UPDATE below
# also acquires the database writer/row lock before either caller makes its
# admission decision.
MEDIA_ANNOTATION_MUTATION_LOCK = RLock()


def lock_media_annotation_mutation(session: Session, media_id: int) -> bool:
    """Hold the target media's database write lease until commit or rollback."""

    result = session.execute(
        update(Media)
        .where(Media.id == media_id)
        .values(id=Media.id)
        .execution_options(synchronize_session=False)
    )
    return result.rowcount == 1
