from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field as PydanticField
from sqlmodel import Field, SQLModel

from app.models import Face, Person
from app.schemas.face import FaceRead
from app.schemas.person import PersonRead, PersonReadSimple
from app.schemas.scene import SceneRead
from app.schemas.tag import TagSimple


class MediaRead(SQLModel):
    id: int
    path: str
    filename: str
    size: int
    duration: float | None
    width: int | None
    height: int | None
    views: int
    inserted_at: datetime
    created_at: datetime
    faces: list[FaceRead]
    persons: list[PersonReadSimple] = []  # we'll fill this in manually
    scenes: list[SceneRead]
    tags: list[TagSimple]
    extracted_scenes: bool
    thumbnail_path: str | None = None
    is_favorite: bool = False
    edit_design_state: dict | None = None

    model_config = ConfigDict(from_attributes=True)


class MediaPreview(SQLModel):
    id: int
    filename: str
    duration: float | None
    width: int | None
    height: int | None
    views: int
    path: str
    inserted_at: datetime
    created_at: datetime
    thumbnail_path: str | None
    size: int | None
    is_favorite: bool = False


class RotateEditOp(BaseModel):
    op: Literal["rotate"]
    degrees: Literal[90, 180, 270]


class FlipEditOp(BaseModel):
    op: Literal["flip"]
    axis: Literal["horizontal", "vertical"]


class CropEditOp(BaseModel):
    op: Literal["crop"]
    x: int = PydanticField(ge=0)
    y: int = PydanticField(ge=0)
    width: int = PydanticField(gt=0)
    height: int = PydanticField(gt=0)


class ResizeEditOp(BaseModel):
    op: Literal["resize"]
    width: int = PydanticField(gt=0)
    height: int = PydanticField(gt=0)


class AdjustEditOp(BaseModel):
    op: Literal["adjust"]
    brightness: int | None = PydanticField(default=None, ge=-100, le=100)
    contrast: int | None = PydanticField(default=None, ge=-100, le=100)
    saturation: int | None = PydanticField(default=None, ge=-100, le=100)


EditOp = Annotated[
    RotateEditOp | FlipEditOp | CropEditOp | ResizeEditOp | AdjustEditOp,
    PydanticField(discriminator="op"),
]


class MediaEditRequest(BaseModel):
    ops: list[EditOp]
    mode: Literal["copy", "overwrite"] = "copy"
    design_state: dict | None = None


class MediaLocation(SQLModel):
    id: int
    latitude: float
    longitude: float
    thumbnail: str


class FaceWithPerson(SQLModel):
    id: int
    thumbnail_path: str
    person: Person  # your Person model


class MediaDetail(SQLModel):
    media: MediaRead
    persons: list[PersonRead] = Field(default_factory=list)
    faces: list[Face] = Field(default_factory=list)
    orphans: list[Face] = Field(default_factory=list)
    model_config = ConfigDict(from_attributes=True)


class MediaNeighbors(SQLModel):
    next_media: MediaPreview | None
    previous_media: MediaPreview | None


class GeoUpdate(SQLModel):
    latitude: float
    longitude: float


class FavoriteUpdate(SQLModel):
    is_favorite: bool


class MediaMoveRequest(BaseModel):
    destination_dir: str


class MediaRenameRequest(BaseModel):
    filename: str


class MediaBulkMoveRequest(BaseModel):
    media_ids: list[int]
    destination_dir: str


class MediaBulkMoveSkipped(BaseModel):
    id: int
    reason: str


class MediaBulkMoveResponse(BaseModel):
    moved_ids: list[int]
    skipped: list[MediaBulkMoveSkipped]


class MediaFolderCreateRequest(BaseModel):
    parent_path: str
    name: str


class MediaFolderCreateResponse(BaseModel):
    path: str
    name: str


class CursorPage(BaseModel):
    items: list[MediaPreview]
    next_cursor: str | None


class MediaFolderPreview(BaseModel):
    id: int
    path: str
    filename: str
    thumbnail_path: str | None = None


class MediaFolderEntry(BaseModel):
    path: str
    name: str
    parent_path: str | None
    depth: int
    media_count: int
    subfolder_count: int
    previews: list[MediaFolderPreview] = Field(default_factory=list)


class MediaFolderBreadcrumb(BaseModel):
    name: str
    path: str | None


class MediaFolderListing(BaseModel):
    current_path: str | None
    parent_path: str | None
    depth: int
    direct_media_count: int
    folders: list[MediaFolderEntry] = Field(default_factory=list)
    breadcrumbs: list[MediaFolderBreadcrumb] = Field(default_factory=list)
