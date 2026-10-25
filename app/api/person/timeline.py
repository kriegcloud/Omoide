from .extensions import (
    _require_person,
    Query,
    exclude_missing,
    tuple_,
)

"""Person timeline: dated media and user-defined life events."""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path
from sqlalchemy import func, literal_column, union_all
from sqlmodel import Session, select

from app.database import get_session
from app.models import Face, Media, PersonMediaLink, TimelineEvent
from app.schemas.timeline import TimelineEventCreate, TimelineEventUpdate, TimelinePage

router = APIRouter()


@router.get("/{person_id}/timeline", response_model=TimelinePage)
def get_person_timeline(
    person_id: int,
    session: Session = Depends(get_session),
    cursor: str | None = None,
    limit: int = Query(1000, ge=1, le=1000),
):
    from sqlalchemy import Integer, String, column, values

    cursor_key = None
    if cursor is not None:
        try:
            raw_date, cursor_type, raw_id = cursor.split("|", 2)
            cursor_date = date.fromisoformat(raw_date)
            cursor_id = int(raw_id)
            if cursor_type not in {"media", "event"} or not 1 <= cursor_id <= 9223372036854775807:
                raise ValueError("Invalid timeline cursor")
            cursor_key = (cursor_date.isoformat(), cursor_type, cursor_id)
        except (ValueError, TypeError):
            raise HTTPException(
                status_code=422,
                detail="Invalid cursor format. Use YYYY-MM-DD|item_type|item_id.",
            )

    media_ids_union = union_all(
        select(Face.media_id.label("media_id")).where(Face.person_id == person_id),
        select(PersonMediaLink.media_id.label("media_id")).where(
            PersonMediaLink.person_id == person_id
        ),
    ).subquery()
    media_ids_subquery = select(media_ids_union.c.media_id).distinct()
    media_query = (
        select(
            Media.id.label("item_id"),
            func.date(Media.created_at).label("timeline_date"),
            literal_column("'media'").label("item_type"),
        )
        .where(Media.id.in_(media_ids_subquery))
        .where(Media.created_at.is_not(None))
    )

    media_query = exclude_missing(media_query)

    # Subquery for one-time TimelineEvent items
    events_query = (
        select(
            TimelineEvent.id.label("item_id"),
            func.date(TimelineEvent.event_date).label("timeline_date"),
            literal_column("'event'").label("item_type"),
        )
        .where(TimelineEvent.person_id == person_id)
        .where(TimelineEvent.recurrence.is_(None))
    )
    base_timeline = union_all(media_query, events_query).cte("base_timeline")
    minimum, maximum = session.exec(
        select(func.min(base_timeline.c.timeline_date), func.max(base_timeline.c.timeline_date))
    ).one()
    if minimum is None or maximum is None:
        # Yearly events overlay the recorded timeline; they do not create an
        # unbounded timeline when there are no media or one-time events.
        return {"items": [], "next_cursor": None}
    first_date, last_date = date.fromisoformat(minimum), date.fromisoformat(maximum)

    recurring_events = session.exec(
        select(TimelineEvent).where(
            TimelineEvent.person_id == person_id, TimelineEvent.recurrence == "yearly"
        )
    ).all()
    occurrences: list[tuple[int, str]] = []
    for event in recurring_events:
        for year in range(first_date.year, last_date.year + 1):
            try:
                occurrence = event.event_date.replace(year=year)
            except ValueError:
                # February 29 has no occurrence in a non-leap year.
                continue
            if first_date <= occurrence <= last_date:
                occurrences.append((event.id, occurrence.isoformat()))

    timeline_cte = base_timeline
    if occurrences:
        yearly_rows = values(
            column("item_id", Integer), column("timeline_date", String)
        ).data(occurrences).cte("yearly_occurrences")
        timeline_cte = union_all(
            select(base_timeline.c.item_id, base_timeline.c.timeline_date, base_timeline.c.item_type),
            select(yearly_rows.c.item_id, yearly_rows.c.timeline_date, literal_column("'event'")),
        ).cte("timeline")

    final_query = select(
        timeline_cte.c.item_id, timeline_cte.c.timeline_date, timeline_cte.c.item_type
    )
    if cursor_key is not None:
        final_query = final_query.where(
            tuple_(timeline_cte.c.timeline_date, timeline_cte.c.item_type, timeline_cte.c.item_id)
            < cursor_key
        )
    final_query = final_query.order_by(
        timeline_cte.c.timeline_date.desc(), timeline_cte.c.item_type.desc(),
        timeline_cte.c.item_id.desc(),
    ).limit(limit + 1)
    page_result = session.exec(final_query).all()
    page_items = page_result[:limit]
    if not page_items:
        return {"items": [], "next_cursor": None}
    last = page_items[-1]
    next_cursor = (
        f"{last.timeline_date}|{last.item_type}|{last.item_id}"
        if len(page_result) > limit else None
    )

    media_ids = [item.item_id for item in page_items if item.item_type == "media"]
    event_ids = [item.item_id for item in page_items if item.item_type == "event"]
    media_map = {row.id: row for row in session.exec(select(Media).where(Media.id.in_(media_ids)))}
    event_map = {row.id: row for row in session.exec(select(TimelineEvent).where(TimelineEvent.id.in_(event_ids)))}
    timeline_items = []
    for item in page_items:
        occurrence_date = date.fromisoformat(item.timeline_date)
        if item.item_type == "media":
            media = media_map.get(item.item_id)
            if media is not None:
                timeline_items.append({"type": "media", "date": occurrence_date, "items": media})
        else:
            event = event_map.get(item.item_id)
            if event is not None:
                if event.recurrence == "yearly":
                    event = TimelineEvent.model_validate({**event.model_dump(), "event_date": occurrence_date})
                timeline_items.append({"type": "event", "date": occurrence_date, "event": event})
    return {"items": timeline_items, "next_cursor": next_cursor}


@router.post("/{person_id}/events", response_model=TimelineEvent)
def create_person_event(
    person_id: Annotated[int, Path(gt=0)], event: TimelineEventCreate, session: Session = Depends(get_session)
):
    """Create a new timeline event for a specific person."""
    _require_person(person_id, session)
    db_event = TimelineEvent.model_validate(event, update={"person_id": person_id})
    session.add(db_event)
    session.commit()
    session.refresh(db_event)
    return db_event


@router.put("/{person_id}/events/{event_id}", response_model=TimelineEvent)
def update_person_event(
    person_id: Annotated[int, Path(gt=0)],  # Included for RESTful consistency, though not strictly needed for the query
    event_id: Annotated[int, Path(gt=0)],
    event_update: TimelineEventUpdate,
    session: Session = Depends(get_session),
):
    """Update an existing timeline event."""
    _require_person(person_id, session)
    db_event = session.get(TimelineEvent, event_id)
    if not db_event or db_event.person_id != person_id:
        raise HTTPException(status_code=404, detail="Event not found")

    update_data = event_update.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(db_event, key, value)

    session.add(db_event)
    session.commit()
    session.refresh(db_event)
    return db_event


@router.delete("/{person_id}/events/{event_id}", status_code=204)
def delete_person_event(
    person_id: Annotated[int, Path(gt=0)], event_id: Annotated[int, Path(gt=0)], session: Session = Depends(get_session)
):
    """Delete a timeline event."""
    _require_person(person_id, session)
    db_event = session.get(TimelineEvent, event_id)
    if db_event and db_event.person_id == person_id:
        session.delete(db_event)
        session.commit()
    return {"ok": True}  # Return nothing on 204
