"""Validate opaque lookup cursors at the HTTP boundary before querying rows."""
from fastapi import HTTPException, Query

from app.pagination import decode_cursor

_SQLITE_MAX_INTEGER = 9223372036854775807


def _lookup_cursor(cursor: str | None, *, person: bool) -> str | None:
    if cursor is None:
        return None
    try:
        if not cursor:
            raise ValueError('Cursor must not be empty')
        values = decode_cursor(cursor, '', 2 if person else 1)
        for index, value in enumerate(values):
            minimum = 0 if person and index == 0 else 1
            if type(value) is not int or not minimum <= value <= _SQLITE_MAX_INTEGER:
                raise ValueError('Cursor key is outside its valid integer range')
    except HTTPException as exc:
        raise ValueError('Invalid cursor format') from exc
    return cursor



def validate_tag_cursor(cursor: str | None = Query(None, min_length=1)) -> str | None:
    try:
        return _lookup_cursor(cursor, person=False)
    except ValueError as exc:
        raise HTTPException(422, "Invalid cursor format") from exc


def validate_person_cursor(cursor: str | None = Query(None, min_length=1)) -> str | None:
    try:
        return _lookup_cursor(cursor, person=True)
    except ValueError as exc:
        raise HTTPException(422, "Invalid cursor format") from exc
