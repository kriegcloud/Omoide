from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models import (
    AnnotationReviewStatus,
    DatasetCaptionSource,
    DatasetExportLayout,
    DatasetExportStatus,
    TrainingRunStatus,
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
    composition_targets: dict[str, dict[str, float]] | None = None


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
    composition_targets: dict[str, dict[str, float]] | None
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
    origin: str
    created_at: datetime
    media: MediaPreview
    effective_caption: str | None
    has_ops: bool
    face_summary: FaceSummary
    metrics: dict | None = None
    caption_reviewed_at: datetime | None = None
    reviewed_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class DatasetItemUpdate(BaseModel):
    caption_override: str | None = None
    edit_ops: list[EditOp] | None = None
    edit_design_state: dict | None = None
    weight: float | None = Field(default=None, gt=0)
    excluded: bool | None = None
    position: int | None = Field(default=None, ge=0)
    reviewed_at: datetime | None = None


class DatasetItemsRequest(BaseModel):
    media_ids: list[int]


class DatasetItemsResult(BaseModel):
    added_ids: list[int] = Field(default_factory=list)
    skipped_ids: list[int] = Field(default_factory=list)


class FillGapsRequest(BaseModel):
    max_add: int = Field(gt=0)
    dimensions: list[str] | None = None


class FillGapsResult(BaseModel):
    added_ids: list[int] = Field(default_factory=list)


class FrameMiningRequest(BaseModel):
    video_media_ids: list[int] | None = None
    max_per_video: int = Field(default=12, ge=1, le=100)
    min_face_px: int = Field(default=160, ge=1)
    fps: float = Field(default=2.0, gt=0, le=30)
    selected_timestamps_ms: dict[int, list[int]] | None = None


class FrameMiningVideoRead(BaseModel):
    media_id: int
    filename: str
    duration: float | None
    thumbnail_path: str | None
    detected_face_count: int
    already_mined_count: int


class FrameCandidateRead(BaseModel):
    timestamp: float
    timestamp_ms: int
    likeness: float
    bbox: list[int]
    yaw: float | None
    pitch: float | None
    sharpness: float
    face_size: float
    phash: str
    novelty: float
    score: float
    preview_data_url: str | None


class FrameMiningCandidatesRead(BaseModel):
    videos: list[FrameMiningVideoRead]
    video_media_id: int | None = None
    candidates: list[FrameCandidateRead] = Field(default_factory=list)


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


class DatasetTriageItem(BaseModel):
    id: int
    position: int
    excluded: bool
    weight: float
    reviewed_at: datetime | None
    caption_override: str | None


class DatasetTriageMedia(BaseModel):
    id: int
    filename: str
    path: str
    width: int | None
    height: int | None
    thumbnail_path: str | None
    thumbnail_url: str | None
    original_url: str


class DatasetTriageEntry(BaseModel):
    item: DatasetTriageItem
    media: DatasetTriageMedia
    face_bbox: tuple[int, int, int, int] | None
    metrics: dict
    caption: str
    effective_caption: str | None
    caption_source: Literal["override", "approved", "candidate", "template", "none"]
    findings: list["CaptionLintFindingRead"] = Field(default_factory=list)
    face_crop_suggestion: dict | None = None


class DatasetTriageCursorPage(BaseModel):
    items: list[DatasetTriageEntry]
    next_cursor: str | None
    reviewed_count: int
    total_count: int


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


class TrainingRunRequest(BaseModel):
    export_id: int | None = None
    base_model: str | None = None
    steps: int = Field(default=2000, gt=0)
    lr: float = Field(default=1e-4, gt=0)
    rank: int = Field(default=16, gt=0)
    sample_prompts: list[str] | None = None


class TrainingSampleRead(BaseModel):
    id: int
    run_id: int
    step: int
    likeness: float | None
    face_count: int | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TrainingRunRead(BaseModel):
    id: int
    dataset_id: int
    export_id: int
    backend: str
    base_model: str
    status: TrainingRunStatus
    run_dir: str
    steps: int
    current_step: int
    total_steps: int
    last_loss: float | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    status_updated_at: datetime | None
    last_sample_step: int
    likeness_best_step: int | None
    likeness_best: float | None
    error: str | None
    lr: float | None = None
    rank: int | None = None
    checkpoints: list[str] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class LikenessStepRead(BaseModel):
    step: int
    mean: float
    max: float
    n: int


class RunLikenessRead(BaseModel):
    run_id: int
    steps: list[LikenessStepRead]
    best_step: int | None
    best: float | None
    scored: int
    pending: int


class TrainingHealthRead(BaseModel):
    launcher_seen_at: datetime | None
    launcher_ok: bool
    hf_token_configured: bool | None
    stale_after_seconds: int


class TrainingPresetRead(BaseModel):
    id: str
    label: str
    description: str
    requires_hf_token: bool
    is_default: bool
    available: bool


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


class CaptionLintFindingRead(BaseModel):
    code: str
    severity: Literal["info", "warn", "error"]
    message: str
    start: int
    end: int


class DatasetCaptionRead(BaseModel):
    item_id: int
    media_id: int
    position: int
    excluded: bool
    media: MediaPreview
    caption: str
    effective_caption: str | None
    source: Literal["override", "approved", "candidate", "template", "none"]
    annotation_id: str | None = None
    review_status: AnnotationReviewStatus | None = None
    caption_reviewed_at: datetime | None = None
    findings: list[CaptionLintFindingRead] = Field(default_factory=list)


class DatasetCaptionCursorPage(BaseModel):
    items: list[DatasetCaptionRead]
    next_cursor: str | None


class DatasetCaptionUpdate(BaseModel):
    text: str = Field(max_length=32_768)


class DatasetCaptionGenerateRequest(BaseModel):
    only_missing: bool = True


class DatasetCaptionReviewedRead(BaseModel):
    item_id: int
    caption_reviewed_at: datetime


DatasetRead.model_rebuild()
DatasetTriageEntry.model_rebuild()
