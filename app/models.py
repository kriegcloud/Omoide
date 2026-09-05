import uuid
from datetime import date, datetime
from enum import StrEnum
from typing import Any, Optional

import sqlalchemy as sa
from sqlalchemy import Column
from sqlalchemy.types import JSON
from sqlmodel import Field, Relationship, SQLModel


class Status(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    FAILED = "failed"


class AnnotationKind(StrEnum):
    CAPTION = "caption"
    TAGS = "tags"


class AnnotationAttemptStatus(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    LOST = "lost"
    UNKNOWN = "unknown"


class AnnotationAuthor(StrEnum):
    MACHINE = "machine"
    HUMAN = "human"


class AnnotationReviewStatus(StrEnum):
    CANDIDATE = "candidate"
    APPROVED = "approved"
    SUPERSEDED = "superseded"


class TrainingDatasetKind(StrEnum):
    SUBJECT = "subject"
    REGULARIZATION = "regularization"


class DatasetCaptionSource(StrEnum):
    ANNOTATION = "annotation"
    TEMPLATE = "template"
    NONE = "none"


class DatasetExportLayout(StrEnum):
    KOHYA = "kohya"
    AI_TOOLKIT = "ai_toolkit"
    ONETRAINER = "onetrainer"


class DatasetExportStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TrainingRunStatus(StrEnum):
    REQUESTED = "requested"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ImageRepairStatus(StrEnum):
    CREATED = "created"
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TimelineEvent(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    title: str = Field(index=True)
    description: str | None = None
    event_date: date

    # For recurrence, a simple string is robust and easy to start with
    # e.g., "yearly", "monthly". We'll start with just "yearly".
    recurrence: str | None = Field(default=None)

    person_id: int = Field(foreign_key="person.id", index=True)
    person: "Person" = Relationship(back_populates="timeline_events")


class MediaTagLink(SQLModel, table=True):
    media_id: int = Field(
        default=None, foreign_key="media.id", primary_key=True
    )
    tag_id: int = Field(default=None, foreign_key="tag.id", primary_key=True)
    auto_score: float | None = Field(default=None)


class PersonMediaLink(SQLModel, table=True):
    person_id: int = Field(
        default=None, foreign_key="person.id", primary_key=True
    )
    media_id: int = Field(
        default=None, foreign_key="media.id", primary_key=True
    )
    created_at: datetime = Field(default_factory=datetime.utcnow)

    person: "Person" = Relationship(back_populates="media_links")
    media: "Media" = Relationship(back_populates="person_links")


class PersonSocialLink(SQLModel, table=True):
    __table_args__ = (
        sa.UniqueConstraint(
            "person_id",
            "platform",
            "handle",
            name="uq_person_social_link",
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    person_id: int = Field(foreign_key="person.id", index=True)
    platform: str
    handle: str
    url: str
    created_at: datetime = Field(default_factory=datetime.now)

    person: "Person" = Relationship(back_populates="social_links")


class Blacklist(SQLModel, table=True):
    id: int = Field(default=None, primary_key=True)
    path: str = Field(unique=True, index=True)


class PersonTagLink(SQLModel, table=True):
    person_id: int = Field(
        default=None, foreign_key="person.id", primary_key=True
    )
    tag_id: int = Field(default=None, foreign_key="tag.id", primary_key=True)


class Tag(SQLModel, table=True):
    id: int = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True)

    media: list["Media"] = Relationship(
        back_populates="tags",
        link_model=MediaTagLink,
        sa_relationship_kwargs={"lazy": "selectin"},
    )
    persons: list["Person"] = Relationship(
        back_populates="tags",
        link_model=PersonTagLink,
        sa_relationship_kwargs={"lazy": "selectin"},
    )

    class Config:
        from_attributes = True


class Face(SQLModel, table=True):
    id: int = Field(default=None, primary_key=True)
    media_id: int = Field(foreign_key="media.id", index=True)
    person_id: int | None = Field(
        foreign_key="person.id", default=None, index=True
    )
    thumbnail_path: str | None = Field(default=None)
    bbox: list[int] = Field(sa_column=Column(JSON))
    timestamp: float | None = Field(
        default=None, nullable=True
    )  # video frame time in seconds
    # detector confidence (InsightFace det_score); NULL for faces extracted before this column existed
    det_score: float | None = Field(default=None, nullable=True)
    # 1.0 = frontal, 0.0 = extreme profile (estimated from the 5-point keypoints)
    frontality: float | None = Field(default=None, nullable=True)
    sex: str | None = Field(default=None, nullable=True)
    sex_score: float | None = Field(default=None, nullable=True)
    age: int | None = Field(default=None, nullable=True)

    media: "Media" = Relationship(back_populates="faces")
    person: Optional["Person"] = Relationship(
        back_populates="faces",
        sa_relationship_kwargs={"foreign_keys": "[Face.person_id]"},
    )

    class Config:
        from_attributes = True


class Media(SQLModel, table=True):
    id: int = Field(default=None, primary_key=True)
    path: str = Field(unique=True)
    filename: str = Field(index=True)
    thumbnail_path: str | None = Field(default=None, nullable=True)
    size: int
    duration: float | None = None
    width: int | None = None
    height: int | None = None
    views: int = Field(default=0, index=True)
    inserted_at: datetime = Field(default_factory=datetime.now, index=True)
    created_at: datetime = Field(default_factory=datetime.now, index=True)

    faces_extracted: bool = Field(default=False, index=True)
    embeddings_created: bool = Field(default=False, index=True)
    ran_auto_tagging: bool = Field(default=False)
    extracted_scenes: bool = Field(default=False)

    missing_since: datetime | None = Field(default=None, index=True)
    missing_confirmed: bool = Field(default=False, index=True)

    processing_error: str | None = Field(default=None, nullable=True)

    is_favorite: bool = Field(default=False)
    phash: str | None = Field(index=True)
    laplacian_score: float | None = Field(
        default=None, nullable=True, index=True
    )
    edit_design_state: dict | None = Field(
        default=None, sa_column=Column(JSON, nullable=True)
    )
    faces: list["Face"] = Relationship(back_populates="media")
    scenes: list["Scene"] = Relationship(back_populates="media")
    tags: list[Tag] = Relationship(
        back_populates="media", link_model=MediaTagLink
    )
    exif: "ExifData" = Relationship(back_populates="media")
    duplicate_entries: list["DuplicateMedia"] = Relationship(
        back_populates="media"
    )
    person_links: list[PersonMediaLink] = Relationship(back_populates="media")

    class Config:
        from_attributes = True

    def __eq__(self, other: "Media"):
        if not isinstance(other, Media):
            return False

        if self.id == other.id:
            return True
        return False


class AnnotationAttempt(SQLModel, table=True):
    """Durable record for one immutable annotation backend execution."""

    id: str = Field(
        default_factory=lambda: str(uuid.uuid4()), primary_key=True
    )
    media_id: int = Field(
        foreign_key="media.id", ondelete="CASCADE", index=True
    )
    kind: AnnotationKind = Field(
        sa_column=sa.Column(
            sa.Enum(
                AnnotationKind,
                values_callable=lambda obj: [item.value for item in obj],
            ),
            nullable=False,
            index=True,
        )
    )
    profile_id: str = Field(index=True)
    backend: str = Field(default="comfy")
    status: AnnotationAttemptStatus = Field(
        sa_column=sa.Column(
            sa.Enum(
                AnnotationAttemptStatus,
                values_callable=lambda obj: [item.value for item in obj],
            ),
            nullable=False,
            default=AnnotationAttemptStatus.CREATED,
            index=True,
        )
    )
    # A nullable unique lease closes the check-then-insert race across API
    # workers. Created/running attempts and unresolved unknown/lost outcomes
    # hold 1; only a proven resolved transition clears it.
    active_slot: int | None = Field(default=1, unique=True)
    external_prompt_id: str = Field(index=True, unique=True)
    predecessor_attempt_id: str | None = Field(
        default=None,
        foreign_key="annotationattempt.id",
        ondelete="SET NULL",
        index=True,
    )
    input_sha256: str | None = Field(default=None, index=True)
    workflow_sha256: str | None = Field(default=None, index=True)
    raw_result: dict[str, Any] | None = Field(
        default=None, sa_column=Column(JSON)
    )
    normalized_result: dict[str, Any] | None = Field(
        default=None, sa_column=Column(JSON)
    )
    provenance: dict[str, Any] | None = Field(
        default=None, sa_column=Column(JSON)
    )
    error_code: str | None = Field(default=None, index=True)
    error_message: str | None = Field(default=None)
    retryable: bool = Field(default=False)
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    started_at: datetime | None = Field(default=None)
    finished_at: datetime | None = Field(default=None)
    history_acknowledged_at: datetime | None = Field(default=None, index=True)

    class Config:
        from_attributes = True


class MediaAnnotation(SQLModel, table=True):
    """Immutable annotation content revision with mutable review metadata."""

    __table_args__ = (
        sa.UniqueConstraint(
            "media_id",
            "kind",
            "revision",
            name="uq_mediaannotation_revision",
        ),
    )

    id: str = Field(
        default_factory=lambda: str(uuid.uuid4()), primary_key=True
    )
    media_id: int = Field(
        foreign_key="media.id", ondelete="CASCADE", index=True
    )
    attempt_id: str | None = Field(
        default=None,
        foreign_key="annotationattempt.id",
        ondelete="SET NULL",
        index=True,
        unique=True,
    )
    parent_id: str | None = Field(
        default=None,
        foreign_key="mediaannotation.id",
        ondelete="SET NULL",
        index=True,
    )
    revision: int = Field(default=1)
    kind: AnnotationKind = Field(
        sa_column=sa.Column(
            sa.Enum(
                AnnotationKind,
                values_callable=lambda obj: [item.value for item in obj],
            ),
            nullable=False,
            index=True,
        )
    )
    author: AnnotationAuthor = Field(
        sa_column=sa.Column(
            sa.Enum(
                AnnotationAuthor,
                values_callable=lambda obj: [item.value for item in obj],
            ),
            nullable=False,
            index=True,
        )
    )
    review_status: AnnotationReviewStatus = Field(
        sa_column=sa.Column(
            sa.Enum(
                AnnotationReviewStatus,
                values_callable=lambda obj: [item.value for item in obj],
            ),
            nullable=False,
            default=AnnotationReviewStatus.CANDIDATE,
            index=True,
        )
    )
    schema_version: str = Field(default="omoide.annotation/v1")
    content: dict[str, Any] = Field(sa_column=Column(JSON, nullable=False))
    provenance: dict[str, Any] | None = Field(
        default=None, sa_column=Column(JSON)
    )
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    approved_at: datetime | None = Field(default=None)
    approved_key: str | None = Field(default=None, unique=True)

    class Config:
        from_attributes = True


class ImageRepairJob(SQLModel, table=True):
    """Durable state for one ComfyUI image repair and its derived media."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    media_id: int = Field(foreign_key="media.id", ondelete="CASCADE", index=True)
    profile: str = Field(index=True)
    params: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSON, nullable=False)
    )
    status: ImageRepairStatus = Field(
        sa_column=sa.Column(
            sa.Enum(
                ImageRepairStatus,
                values_callable=lambda obj: [item.value for item in obj],
            ),
            nullable=False,
            default=ImageRepairStatus.CREATED,
            index=True,
        )
    )
    external_prompt_id: str | None = Field(default=None, unique=True, index=True)
    result_media_id: int | None = Field(
        default=None, foreign_key="media.id", ondelete="SET NULL", index=True
    )
    mask_path: str | None = Field(default=None)
    error_code: str | None = Field(default=None, index=True)
    error_message: str | None = Field(default=None)
    retryable: bool = Field(default=False)
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    started_at: datetime | None = Field(default=None)
    finished_at: datetime | None = Field(default=None)

    class Config:
        from_attributes = True


class TrainingDataset(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    slug: str = Field(unique=True, index=True)
    description: str | None = Field(default=None)
    kind: TrainingDatasetKind = Field(default=TrainingDatasetKind.SUBJECT)
    person_id: int | None = Field(default=None, foreign_key="person.id", index=True)
    trigger_word: str
    class_token: str
    caption_source: DatasetCaptionSource = Field(
        default=DatasetCaptionSource.ANNOTATION
    )
    caption_template: str = Field(default="{trigger} {class}, {caption}")
    target_resolution: int = Field(default=1024)
    buckets: list[int] = Field(
        default_factory=lambda: [512, 768, 1024],
        sa_column=Column(JSON, nullable=False),
    )
    repeats: int = Field(default=10)
    export_layout: DatasetExportLayout = Field(
        default=DatasetExportLayout.AI_TOOLKIT
    )
    cover_media_id: int | None = Field(default=None, foreign_key="media.id")
    regularization_dataset_id: int | None = Field(
        default=None, foreign_key="trainingdataset.id"
    )
    created_at: datetime = Field(default_factory=datetime.now, index=True)
    updated_at: datetime = Field(default_factory=datetime.now, index=True)


class DatasetItem(SQLModel, table=True):
    __table_args__ = (
        sa.UniqueConstraint(
            "dataset_id", "media_id", name="uq_datasetitem_dataset_media"
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    dataset_id: int = Field(
        foreign_key="trainingdataset.id", ondelete="CASCADE", index=True
    )
    media_id: int = Field(foreign_key="media.id", index=True)
    position: int = Field(default=0, index=True)
    edit_ops: list[dict[str, Any]] | None = Field(
        default=None, sa_column=Column(JSON, nullable=True)
    )
    edit_design_state: dict[str, Any] | None = Field(
        default=None, sa_column=Column(JSON, nullable=True)
    )
    caption_override: str | None = Field(default=None)
    weight: float = Field(default=1.0)
    excluded: bool = Field(default=False, index=True)
    created_at: datetime = Field(default_factory=datetime.now, index=True)
    caption_reviewed_at: datetime | None = Field(default=None, nullable=True)


class MediaCurationStats(SQLModel, table=True):
    media_id: int = Field(primary_key=True, foreign_key="media.id")
    brightness_mean: float
    contrast_std: float
    computed_at: datetime = Field(default_factory=datetime.now)


class DatasetExport(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    dataset_id: int = Field(
        foreign_key="trainingdataset.id", ondelete="CASCADE", index=True
    )
    layout: DatasetExportLayout
    status: DatasetExportStatus = Field(
        default=DatasetExportStatus.PENDING, index=True
    )
    task_id: str | None = Field(
        default=None, foreign_key="processingtask.id", ondelete="SET NULL", index=True
    )
    output_dir: str = Field(default="")
    item_count: int = Field(default=0)
    manifest: dict[str, Any] | None = Field(
        default=None, sa_column=Column(JSON, nullable=True)
    )
    error: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.now, index=True)
    finished_at: datetime | None = Field(default=None)


class TrainingRun(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    dataset_id: int = Field(
        foreign_key="trainingdataset.id", ondelete="CASCADE", index=True
    )
    export_id: int = Field(
        foreign_key="datasetexport.id", ondelete="CASCADE", index=True
    )
    backend: str = Field(default="ai_toolkit")
    base_model: str = Field(default="flux-dev")
    status: TrainingRunStatus = Field(
        default=TrainingRunStatus.REQUESTED, index=True
    )
    run_dir: str
    config_yaml: str
    steps: int
    current_step: int = Field(default=0)
    total_steps: int = Field(default=0)
    last_loss: float | None = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.now, index=True)
    started_at: datetime | None = Field(default=None)
    finished_at: datetime | None = Field(default=None)
    status_updated_at: datetime | None = Field(default=None)
    last_sample_step: int = Field(default=0)
    likeness_best_step: int | None = Field(default=None)
    likeness_best: float | None = Field(default=None)
    likeness_summary: dict[str, Any] | None = Field(
        default=None, sa_column=Column(JSON, nullable=True)
    )
    error: str | None = Field(default=None)


class TrainingSample(SQLModel, table=True):
    __table_args__ = (
        sa.UniqueConstraint("run_id", "path", name="uq_trainingsample_run_path"),
    )

    id: int | None = Field(default=None, primary_key=True)
    run_id: int = Field(
        foreign_key="trainingrun.id", ondelete="CASCADE", index=True
    )
    step: int = Field(index=True)
    path: str
    likeness: float | None = Field(default=None)
    face_count: int | None = Field(default=None)
    face_bbox: list[int] | None = Field(
        default=None, sa_column=Column(JSON, nullable=True)
    )
    scored_at: datetime | None = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=datetime.now, index=True)


class Scene(SQLModel, table=True):
    id: int = Field(default=None, primary_key=True)
    media_id: int = Field(foreign_key="media.id", index=True)
    start_time: float  # in seconds
    end_time: float  # in seconds
    thumbnail_path: str | None = Field(
        default=None, nullable=True
    )  # relative path under THUMB_DIR
    description: str | None = Field(default=None)

    media: "Media" = Relationship(back_populates="scenes")


class Person(SQLModel, table=True):
    id: int = Field(default=None, primary_key=True)
    name: str | None = None
    age: int | None = None
    gender: str | None = None
    gender_confidence: float | None = Field(default=None, nullable=True)
    gender_manual: bool = Field(default=False, nullable=False)
    hidden_at: datetime | None = Field(default=None, index=True)
    views: int = Field(default=0, index=True)
    faces: list["Face"] = Relationship(
        back_populates="person",
        sa_relationship_kwargs={
            # same idea: this relationship uses Face.person_id to point back
            "foreign_keys": "[Face.person_id]"
        },
    )
    is_favorite: bool = Field(default=False)
    profile_face_id: int | None = Field(
        foreign_key="face.id", default=None, index=True
    )
    profile_face: Face | None = Relationship(
        sa_relationship_kwargs={
            "primaryjoin": "Person.profile_face_id==Face.id",
            "foreign_keys": "[Person.profile_face_id]",
            "uselist": False,
            "lazy": "selectin",
        }
    )
    tags: list[Tag] = Relationship(
        back_populates="persons", link_model=PersonTagLink
    )
    appearance_count: int = Field(default=None, index=True)
    timeline_events: list["TimelineEvent"] = Relationship(
        back_populates="person"
    )
    media_links: list[PersonMediaLink] = Relationship(back_populates="person")
    social_links: list[PersonSocialLink] = Relationship(
        back_populates="person",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )

    class Config:
        from_attributes = True


class ProcessingTask(SQLModel, table=True):
    id: str = Field(
        default_factory=lambda: str(uuid.uuid4()), primary_key=True
    )
    task_type: str
    status: Status = Field(
        sa_column=sa.Column(
            sa.Enum(
                Status,
                values_callable=lambda obj: [item.value for item in obj],
            ),
            default=Status.PENDING,
            nullable=False,
            index=True,
        )
    )
    total: int = Field(default=0)
    processed: int = Field(default=0)
    created_at: datetime = Field(default_factory=datetime.now)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    result: dict[str, Any] | None = Field(
        default=None, sa_column=Column(JSON, nullable=True)
    )

    class Config:
        from_attributes = True


# Read model that augments ProcessingTask with transient fields.
# These fields are not stored in the DB; we only use them for API responses.
class ProcessingTaskRead(SQLModel):
    id: str
    task_type: str
    status: Status
    total: int
    processed: int
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    result: dict[str, Any] | None = None
    current_item: str | None = None
    current_step: str | None = None
    failure_count: int | None = None
    merge_total: int | None = None
    merge_processed: int | None = None
    merge_pending: int | None = None


class ExifData(SQLModel, table=True):
    id: int = Field(default=None, primary_key=True)
    media_id: int = Field(foreign_key="media.id", index=True)

    # camera & capture
    make: str | None = Field(default=None, index=True)
    model: str | None = Field(default=None, index=True)
    timestamp: datetime | None = Field(default=None, index=True)

    # lens / exposure
    iso: int | None = Field(default=None, index=True)
    exposure_time: str | None = Field(default=None)
    aperture: str | None = Field(default=None)
    focal_length: float | None = Field(default=None)

    # GPS
    lat: float | None = Field(default=None, index=True)
    lon: float | None = Field(default=None, index=True)

    # reverse-geocoded place (filled by the geocode_places task)
    city: str | None = Field(default=None, index=True)
    country: str | None = Field(default=None, index=True)

    media: Media = Relationship(back_populates="exif")


class DuplicateGroup(SQLModel, table=True):
    id: int = Field(default=None, primary_key=True)
    created_at: datetime = Field(default_factory=datetime.now)

    media_links: list["DuplicateMedia"] = Relationship(back_populates="group")


class DuplicateIgnore(SQLModel, table=True):
    media_id_a: int = Field(foreign_key="media.id", primary_key=True)
    media_id_b: int = Field(foreign_key="media.id", primary_key=True)
    created_at: datetime = Field(default_factory=datetime.now)

    class Config:
        from_attributes = True


class DuplicateMedia(SQLModel, table=True):
    group_id: int = Field(foreign_key="duplicategroup.id", primary_key=True)
    media_id: int = Field(foreign_key="media.id", primary_key=True)

    group: DuplicateGroup = Relationship(back_populates="media_links")
    media: Media = Relationship(back_populates="duplicate_entries")


class PersonRelationship(SQLModel, table=True):
    __tablename__ = "person_relationship"

    person_a_id: int = Field(foreign_key="person.id", primary_key=True)
    person_b_id: int = Field(foreign_key="person.id", primary_key=True)
    coappearance_count: int = Field(default=0, index=True)
    last_media_id: int | None = Field(default=None, foreign_key="media.id")
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class Album(SQLModel, table=True):
    id: int = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    description: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.now)
    cover_media_id: int | None = Field(
        default=None, foreign_key="media.id", nullable=True
    )

    class Config:
        from_attributes = True


class AlbumMediaLink(SQLModel, table=True):
    album_id: int = Field(foreign_key="album.id", primary_key=True)
    media_id: int = Field(foreign_key="media.id", primary_key=True)
    created_at: datetime = Field(default_factory=datetime.now)


class Event(SQLModel, table=True):
    """Automatically clustered group of media taken close together in time."""

    id: int = Field(default=None, primary_key=True)
    # place label ("Berlin" / "Berlin, DE"); date range lives in start/end
    title: str | None = Field(default=None)
    # True once a user renames the event; lets "Rebuild events" carry the
    # title over to the best-matching new cluster instead of overwriting it
    title_is_custom: bool = Field(default=False)
    start_at: datetime = Field(index=True)
    end_at: datetime = Field(index=True)
    media_count: int = Field(default=0)
    cover_media_id: int | None = Field(
        default=None, foreign_key="media.id", nullable=True
    )

    class Config:
        from_attributes = True


class EventMediaLink(SQLModel, table=True):
    event_id: int = Field(foreign_key="event.id", primary_key=True)
    media_id: int = Field(foreign_key="media.id", primary_key=True)
