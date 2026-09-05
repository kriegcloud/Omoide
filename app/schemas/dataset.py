from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models import (
    DatasetCaptionSource,
    DatasetExportLayout,
    DatasetExportStatus,
    TrainingDatasetKind,
)
from app.schemas.media import EditOp, MediaPreview


class DatasetCreate(BaseModel):
    name: str
    slug: str | None = None
    description: str | None = None
    kind: TrainingDatasetKind = TrainingDatasetKind.SUBJECT
    person_id: int | None = None
    trigger_word: str | None = None
    class_token: str | None = None
    caption_source: DatasetCaptionSource = DatasetCaptionSource.ANNOTATION
    caption_template: str = "{trigger} {class}, {caption}"
    target_resolution: int = Field(default=1024, gt=0)
    buckets: list[int] = Field(default_factory=lambda: [512, 768, 1024])
    repeats: int = Field(default=10, gt=0)
    export_layout: DatasetExportLayout = DatasetExportLayout.AI_TOOLKIT
    cover_media_id: int | None = None


class DatasetUpdate(BaseModel):
    name: str | None = None
    slug: str | None = None
    description: str | None = None
    kind: TrainingDatasetKind | None = None
    person_id: int | None = None
    trigger_word: str | None = None
    class_token: str | None = None
    caption_source: DatasetCaptionSource | None = None
    caption_template: str | None = None
    target_resolution: int | None = Field(default=None, gt=0)
    buckets: list[int] | None = None
    repeats: int | None = Field(default=None, gt=0)
    export_layout: DatasetExportLayout | None = None
    cover_media_id: int | None = None


class DatasetRead(BaseModel):
    id: int
    name: str
    slug: str
    description: str | None
    kind: TrainingDatasetKind
    person_id: int | None
    trigger_word: str
    class_token: str
    caption_source: DatasetCaptionSource
    caption_template: str
    target_resolution: int
    buckets: list[int]
    repeats: int
    export_layout: DatasetExportLayout
    cover_media_id: int | None
    regularization_dataset_id: int | None
    created_at: datetime
    updated_at: datetime
    item_count: int = 0
    included_count: int = 0
    cover: MediaPreview | None = None
    last_export: "DatasetExportRead | None" = None

    model_config = ConfigDict(from_attributes=True)


class FaceSummary(BaseModel):
    det_score: float | None = None
    frontality: float | None = None
    face_count: int = 0


class DatasetItemRead(BaseModel):
    id: int
    dataset_id: int
    media_id: int
    position: int
    edit_ops: list[dict] | None
    edit_design_state: dict | None
    caption_override: str | None
    weight: float
    excluded: bool
    created_at: datetime
    media: MediaPreview
    effective_caption: str | None
    has_ops: bool
    face_summary: FaceSummary
    metrics: dict | None = None

    model_config = ConfigDict(from_attributes=True)


class DatasetItemUpdate(BaseModel):
    caption_override: str | None = None
    edit_ops: list[EditOp] | None = None
    edit_design_state: dict | None = None
    weight: float | None = Field(default=None, gt=0)
    excluded: bool | None = None
    position: int | None = Field(default=None, ge=0)


class DatasetItemsRequest(BaseModel):
    media_ids: list[int]


class DatasetItemsResult(BaseModel):
    added_ids: list[int] = Field(default_factory=list)
    skipped_ids: list[int] = Field(default_factory=list)


class DatasetBatchCropRequest(BaseModel):
    item_ids: list[int] | None = None
    framing: Literal["closeup", "portrait", "half_body", "full_body"]
    aspect: Literal["1:1", "2:3", "3:4", "4:5", "9:16", "free"]
    overwrite_existing_ops: bool = False


class DatasetBatchCropSkipped(BaseModel):
    item_id: int
    reason: str


class DatasetBatchCropResult(BaseModel):
    updated_ids: list[int] = Field(default_factory=list)
    skipped: list[DatasetBatchCropSkipped] = Field(default_factory=list)


class DatasetItemCursorPage(BaseModel):
    items: list[DatasetItemRead]
    next_cursor: str | None


class DatasetExportRequest(BaseModel):
    layout: DatasetExportLayout | None = None


class DatasetExportRead(BaseModel):
    id: int
    dataset_id: int
    layout: DatasetExportLayout
    status: DatasetExportStatus
    task_id: str | None
    output_dir: str
    host_output_dir: str | None = None
    item_count: int
    manifest: dict | None
    error: str | None
    created_at: datetime
    finished_at: datetime | None
    launch_command: str | None = None

    model_config = ConfigDict(from_attributes=True)


class AutoSelectRequest(BaseModel):
    target_count: int = Field(gt=0)
    min_frontality: float | None = Field(default=None, ge=0, le=1)
    min_sharpness: float | None = Field(default=None, ge=0)
    max_other_people: int | None = Field(default=None, ge=0)
    drop_duplicates: bool = True
    dry_run: bool = False


class AutoSelectResult(BaseModel):
    selected_item_ids: list[int]
    excluded_item_ids: list[int]


class RegularizationRequest(BaseModel):
    target_count: int = Field(gt=0)
    gender: str | None = None
    exclude_person_ids: list[int] = Field(default_factory=list)


DatasetRead.model_rebuild()
