from datetime import datetime

from typing import Literal

from pydantic import BaseModel, ConfigDict
from app.schemas.face import FaceRead
from sqlmodel import SQLModel


class ProfileFace(BaseModel):
    id: int
    thumbnail_path: str


class TagSimple(SQLModel):
    id: int
    name: str

    model_config = ConfigDict(from_attributes=True)


class PersonDetail(BaseModel):
    id: int
    name: str | None
    profile_face_id: int | None
    profile_face: ProfileFace | None
    tags: list[TagSimple]
    appearance_count: int
    hidden_at: datetime | None = None
    gender: str | None = None
    gender_confidence: float | None = None
    gender_manual: bool = False
    age: int | None = None


class PersonUpdate(BaseModel):
    name: str | None = None
    profile_face_id: int | None = None
    gender: Literal["female", "male"] | None = None


class PersonMedia(SQLModel):
    id: int
    path: str
    duration: float | None
    filename: str
    width: int | None
    height: int | None
    thumbnail_path: str | None


class PersonMinimal(SQLModel):
    id: int
    name: str | None = None


class PersonRead(SQLModel):
    id: int
    name: str | None
    profile_face: FaceRead | None
    appearance_count: int | None
    hidden_at: datetime | None = None
    gender: str | None = None
    gender_confidence: float | None = None
    gender_manual: bool = False
    age: int | None = None

    model_config = ConfigDict(from_attributes=True)


class PersonReadSimple(SQLModel):
    id: int
    name: str | None
    profile_face: FaceRead | None
    hidden_at: datetime | None = None
    gender: str | None = None
    gender_confidence: float | None = None
    gender_manual: bool = False
    age: int | None = None

    model_config = ConfigDict(from_attributes=True)


class MergePersonsRequest(BaseModel):
    source_id: int
    target_id: int


class MergePersonsBulkRequest(BaseModel):
    source_ids: list[int]


class MergePersonsResult(BaseModel):
    merged_ids: list[int]
    skipped_ids: list[int]


class PersonBulkDeleteRequest(BaseModel):
    person_ids: list[int]


class PersonBulkDeleteResponse(BaseModel):
    deleted_ids: list[int]
    skipped_ids: list[int]


class PersonBulkHideRequest(BaseModel):
    person_ids: list[int]


class PersonBulkHideResponse(BaseModel):
    hidden_ids: list[int]
    skipped_ids: list[int]


class PersonBulkUnhideResponse(BaseModel):
    unhidden_ids: list[int]
    skipped_ids: list[int]


class PersonMediaBulkRequest(BaseModel):
    media_ids: list[int]


class PersonMediaBulkAttachResponse(BaseModel):
    added_ids: list[int]
    skipped_ids: list[int]


class PersonMediaBulkDetachResponse(BaseModel):
    detached_ids: list[int]
    skipped_ids: list[int]


class PersonMediaReassignRequest(BaseModel):
    target_person_id: int


class PersonMediaReassignResponse(BaseModel):
    media_id: int
    source_person_id: int
    target_person_id: int
    reassigned: bool


class SimilarPerson(SQLModel):
    id: int
    name: str | None
    similarity: float
    thumbnail: str | None = None


class RelationshipNode(BaseModel):
    id: int
    name: str | None
    profile_thumbnail: str | None = None
    depth: int


class RelationshipEdge(BaseModel):
    source: int
    target: int
    weight: int


class RelationshipGraph(BaseModel):
    nodes: list[RelationshipNode]
    edges: list[RelationshipEdge]
    root_id: int
    max_depth: int


class CursorPage(BaseModel):
    items: list[PersonRead]
    next_cursor: str | None


class MediaCursorPage(BaseModel):
    items: list[PersonMedia]
    next_cursor: str | None
