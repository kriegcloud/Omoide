from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class NoFacesMediaItem(BaseModel):
    id: int
    filename: str
    path: str
    size: int
    thumbnail_path: str | None
    width: int | None
    height: int | None
    duration: float | None
    inserted_at: datetime
    faces_extracted: bool

    class Config:
        from_attributes = True


class NoFacesPage(BaseModel):
    items: list[NoFacesMediaItem]
    next_cursor: str | None
    total: int


class NoFacesResolveRequest(BaseModel):
    media_ids: list[int] = []
    action: Literal["DELETE_FILES", "DELETE_RECORDS", "BLACKLIST_RECORDS"]
    select_all: bool = False
    folder: str | None = None
    media_type: str | None = None
    scope: str = "processed"
