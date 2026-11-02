from datetime import datetime

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from app.schemas.face import FaceRead
from sqlmodel import SQLModel

from app.schemas.face import FaceRead


class ProfileFace(FaceRead):
    """Profile crop with the same parent geometry as gallery faces."""


class TagSimple(SQLModel):
    id: int
    name: str

    model_config = ConfigDict(from_attributes=True)


SocialPlatform = Literal[
    "instagram", "tiktok", "x", "youtube", "onlyfans", "threads", "facebook",
    "snapchat", "reddit", "twitch", "linkedin", "pinterest", "fansly", "patreon",
    "linktree", "beacons", "imdb", "spotify", "soundcloud", "github", "bluesky",
    "mastodon", "telegram", "discord", "vsco", "cashapp", "venmo", "other",
]


class SocialBrandRead(BaseModel):
    domain: str
    display_name: str
    simple_icons_slug: str | None = None
    colour: str | None = None
    icon_path: str | None = None
    source: str | None = None
    fetched_at: datetime | None = None
    status: str = "pending"
    error: str | None = None

    model_config = ConfigDict(from_attributes=True)


class SocialLinkPreviewRequest(BaseModel):
    url: str = Field(min_length=1, max_length=4096)


class SocialLinkPreview(BaseModel):
    platform: SocialPlatform
    handle: str
    url: str
    normalized_url: str
    brand_domain: str
    brand: SocialBrandRead | None = None


class SocialLinkCreate(BaseModel):
    platform: SocialPlatform | None = None
    handle: str | None = None
    url: str | None = Field(default=None, max_length=4096)
    label: str | None = Field(default=None, max_length=200)
    sort_order: int | None = Field(default=None, ge=0)


class SocialLinkUpdate(BaseModel):
    url: str | None = Field(default=None, max_length=4096)
    label: str | None = Field(default=None, max_length=200)
    sort_order: int | None = Field(default=None, ge=0)


class SocialLinkReorder(BaseModel):
    link_ids: list[int]


class SocialLinkRead(BaseModel):
    id: int
    platform: SocialPlatform
    handle: str
    url: str
    normalized_url: str | None = None
    brand_domain: str | None = None
    label: str | None = None
    sort_order: int = 0
    brand: SocialBrandRead | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SocialLinkSuggestion(BaseModel):
    platform: SocialPlatform | None
    handle: str
    source_folder: str
    media_count: int


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
    social_links: list[SocialLinkRead]
    is_favorite: bool = False


class PersonUpdate(BaseModel):
    operation_id: str | None = None
    name: str | None = None
    profile_face_id: int | None = None
    gender: Literal["female", "male"] | None = None


class PersonMediaExportRequest(BaseModel):
    destination_path: str
    mode: Literal["copy", "move"]


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
    is_favorite: bool = False

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


class MergeCandidate(BaseModel):
    person_a: PersonRead
    person_b: PersonRead
    similarity: float
    shared_media: int


class MergeCandidatesPage(BaseModel):
    items: list[MergeCandidate]


class PersonPairDecisionCreate(BaseModel):
    person_a_id: int
    person_b_id: int
    decision: Literal["not_same"]


class PersonPairDecisionRead(PersonPairDecisionCreate):
    id: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class MergePersonsRequest(BaseModel):
    operation_id: str | None = None
    source_id: int
    target_id: int


class MergePersonsBulkRequest(BaseModel):
    operation_id: str | None = None
    source_ids: list[int]


class MergePersonsResult(BaseModel):
    merged_ids: list[int]
    skipped_ids: list[int]


class PersonBulkDeleteRequest(BaseModel):
    operation_id: str | None = None
    person_ids: list[int]


class PersonBulkDeleteResponse(BaseModel):
    deleted_ids: list[int]
    skipped_ids: list[int]


class PersonBulkHideRequest(BaseModel):
    operation_id: str | None = None
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


class DetachedFace(BaseModel):
    id: int
    media_id: int


class PersonMediaBulkDetachResponse(BaseModel):
    detached_ids: list[int]
    skipped_ids: list[int]
    detached_faces: list[DetachedFace] = Field(default_factory=list)


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
    profile_face: FaceRead | None = None


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
