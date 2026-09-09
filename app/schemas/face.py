from typing import Literal

from pydantic import BaseModel, ConfigDict


class FaceRead(BaseModel):
    id: int
    media_id: int
    thumbnail_path: str
    similarity: float | None = None
    timestamp: float | None = None
    kps: list[list[float]] | None = None
    yaw: float | None = None
    pitch: float | None = None

    model_config = ConfigDict(from_attributes=True)


class FaceAssign(BaseModel):
    person_id: int
    face_ids: list[int]


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
    pose_bin: Literal["frontal", "quarter", "profile", "unknown"]


class OrphanFaceSuggestionsPage(BaseModel):
    items: list[OrphanFaceSuggestion]
    next_cursor: str | None


class SuggestedFaceAssignment(BaseModel):
    face_id: int
    person_id: int


class AssignSuggestedFaces(BaseModel):
    assignments: list[SuggestedFaceAssignment]


class SkippedFaceAssignment(BaseModel):
    face_id: int
    reason: Literal["unknown_face", "face_already_assigned", "unknown_person"]


class AssignSuggestedFacesResult(BaseModel):
    assigned: int
    skipped: list[SkippedFaceAssignment]
