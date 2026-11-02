from datetime import datetime
from typing import Literal

from pydantic import AliasChoices, AliasPath, BaseModel, ConfigDict, Field


class FaceRead(BaseModel):
    id: int
    media_id: int
    thumbnail_path: str
    # Stored xywh coordinates use the detector image (long side <= 1280).
    bbox: list[int] | None = None
    media_width: int | None = Field(default=None, validation_alias=AliasChoices("media_width", AliasPath("media", "width")))
    media_height: int | None = Field(default=None, validation_alias=AliasChoices("media_height", AliasPath("media", "height")))
    media_thumbnail_path: str | None = Field(default=None, validation_alias=AliasChoices("media_thumbnail_path", AliasPath("media", "thumbnail_path")))
    media_duration: float | None = Field(default=None, validation_alias=AliasChoices("media_duration", AliasPath("media", "duration")))
    similarity: float | None = None
    timestamp: float | None = None
    kps: list[list[float]] | None = None
    yaw: float | None = None
    pitch: float | None = None
    ignored_at: datetime | None = None
    assigned_at: datetime | None = None
    assignment_source: str | None = None

    model_config = ConfigDict(from_attributes=True)


class FaceAssign(BaseModel):
    operation_id: str | None = None
    person_id: int
    face_ids: list[int]
    source: Literal["manual", "suggestion", "undo"] = "manual"


class RecentFaceAssignment(BaseModel):
    id: int
    media_id: int
    person_id: int | None
    person_name: str | None
    thumbnail_path: str | None
    assigned_at: datetime
    assignment_source: str | None


class FaceAssignReturn(BaseModel):
    face_id: int
    person_id: int


class CursorPage(BaseModel):
    items: list[FaceRead]
    next_cursor: str | None


class OrphanFaceSuggestion(BaseModel):
    face: FaceRead
    person_id: int
    person_name: str
    score: float
    best_matching_face_id: int | None = None
    best_cos: float | None = None
    runner_up_person_cos: float | None = None
    margin: float | None = None
    pose_bin: Literal["frontal", "quarter", "profile", "unknown"]


class OrphanFaceSuggestionsPage(BaseModel):
    items: list[OrphanFaceSuggestion]
    next_cursor: str | None


class SuggestedFaceAssignment(BaseModel):
    face_id: int
    person_id: int


class AssignSuggestedFaces(BaseModel):
    assignments: list[SuggestedFaceAssignment]
    source: Literal["suggestion"] = "suggestion"


class SkippedFaceAssignment(BaseModel):
    face_id: int
    reason: Literal["unknown_face", "face_already_assigned", "face_ignored", "unknown_person"]


class AssignSuggestedFacesResult(BaseModel):
    assigned: int
    skipped: list[SkippedFaceAssignment]
