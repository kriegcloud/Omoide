import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated
from urllib.parse import quote

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Query,
    Request,
    status,
)
from fastapi.responses import PlainTextResponse
from sqlalchemy import and_, case, func, or_, text, tuple_, union_all
from sqlalchemy.orm import aliased, selectinload
from sqlmodel import Session, col, select

from app.config import settings
from app.api._media_filters import exclude_missing, folder_filter
from app.api._resolve import resolve_media_action
from app.schemas.media import MediaBulkDeleteRequest, MediaBulkDeleteResponse
from app.database import get_session, safe_commit, safe_execute
from app.logger import logger
from app.models import (
    ExifData,
    Face,
    Media,
    Person,
    PersonMediaLink,
    ProcessingTask,
    Scene,
    Tag,
)
from app.schemas.face import FaceRead
from app.schemas.media import (
    CursorPage,
    FavoriteUpdate,
    GeoUpdate,
    MediaDetail,
    FaceCropSuggestion,
    MediaBatchEditRequest,
    MediaEditRequest,
    MediaFolderBreadcrumb,
    MediaFolderCreateRequest,
    MediaFolderCreateResponse,
    MediaFolderEntry,
    MediaFolderListing,
    MediaFolderPreview,
    MediaLocation,
    MediaNeighbors,
    MediaPreview,
    MediaRead,
    MediaBulkMoveRequest,
    MediaBulkMoveResponse,
    MediaBulkMoveSkipped,
    MediaMoveRequest,
    MediaRenameRequest,
)
from app.schemas.person import PersonRead
from app.schemas.scene import PersonInScene, SceneCreate, SceneRead
from app.services.media_files import (
    InvalidMediaPathError,
    MediaFileCollisionError,
    MediaFileMissingError,
    ReadOnlyMediaRootError,
    create_media_folder,
    move_media_file,
    rename_media_file,
)
from app.services.face_crops import CropAspect, Framing, bbox_to_source_pixels, suggest_crop
from app.services.image_edits import edit_media_record
from app.utils import (
    delete_file,
    delete_record,
    extract_scene_frame_and_thumbnail,
    generate_perceptual_hash,
    generate_thumbnail,
    update_exif_gps,
)

router = APIRouter()


def _require_media_mutations_allowed() -> None:
    if settings.general.presentation_mode:
        raise HTTPException(
            status_code=403,
            detail="Not allowed in settings.general.presentation_mode mode.",
        )


def _media_file_conflict(exc: Exception) -> HTTPException:
    return HTTPException(status_code=409, detail=str(exc))


def _move_media(media: Media, destination_dir: str) -> Path:
    if media.missing_since is not None:
        raise MediaFileMissingError("Media file is missing")
    return move_media_file(
        media.path,
        destination_dir,
        settings.general.resolved_media_dirs(),
    )


def _rename_media(media: Media, filename: str) -> Path:
    if media.missing_since is not None:
        raise MediaFileMissingError("Media file is missing")
    return rename_media_file(
        media.path,
        filename,
        settings.general.resolved_media_dirs(),
    )


def _queue_edited_media(
    media_id: int, session: Session, background_tasks: BackgroundTasks
) -> None:
    # Lazy imports avoid the existing tasks.maintenance -> api.media cycle.
    from app.tasks import create_and_run_task, run_processors_for_media
    from app.tasks.media_processing import edit_processor_names

    create_and_run_task(
        session=session,
        background_tasks=background_tasks,
        task_type="run_processor_for_media",
        callable_task=lambda task_id: run_processors_for_media(
            task_id, edit_processor_names(), [media_id]
        ),
    )


def _normalize_relative_path(value: str | None) -> str:
    if value is None:
        return ""
    normalized = value.replace("\\", "/").strip("/")
    if not normalized:
        return ""
    parts = [segment for segment in normalized.split("/") if segment]
    if any(part in {"..", "."} for part in parts):
        raise HTTPException(status_code=400, detail="Invalid folder path")
    return "/".join(parts)


def _normalized_media_roots() -> tuple[str, ...]:
    roots: dict[str, str] = {}
    for base, _ in settings.general.resolved_media_dirs():
        normalized = os.fspath(base).replace("\\", "/").rstrip("/")
        if not normalized:
            normalized = "/"
        key = normalized.casefold() if os.name == "nt" else normalized
        roots.setdefault(key, normalized)
    return tuple(sorted(roots.values(), key=len, reverse=True))


def _relative_media_path_expr():
    """Return a SQL expression for a media path relative to its media root."""
    normalized_path = func.replace(Media.path, "\\", "/")
    comparison_path = (
        func.lower(normalized_path) if os.name == "nt" else normalized_path
    )
    root_cases = []
    for root in _normalized_media_roots():
        prefix = "/" if root == "/" else f"{root}/"
        comparison_prefix = prefix.casefold() if os.name == "nt" else prefix
        root_cases.append(
            (
                func.substr(comparison_path, 1, len(prefix))
                == comparison_prefix,
                func.substr(normalized_path, len(prefix) + 1),
            )
        )

    # Preserve legacy relative database paths, but do not expose stale absolute
    # paths that no longer belong to any configured media root.
    is_relative = and_(
        func.substr(normalized_path, 1, 1) != "/",
        func.substr(normalized_path, 2, 2) != ":/",
    )
    return case(
        *root_cases,
        (is_relative, func.ltrim(normalized_path, "/")),
        else_=None,
    )


def _folder_prefix_clause(path_expr, folder: str):
    prefix = f"{folder}/"
    return func.substr(path_expr, 1, len(prefix)) == prefix


def _is_local_request(request: Request) -> bool:
    try:
        host = request.client.host if request.client else ""
    except Exception:
        return False
    return host in {"127.0.0.1", "::1", "localhost"}


@router.post("/folders", response_model=MediaFolderCreateResponse)
def create_folder(
    body: MediaFolderCreateRequest,
):
    _require_media_mutations_allowed()
    if (
        not body.name.strip()
        or body.name.strip() in {".", ".."}
        or "/" in body.name
        or "\\" in body.name
    ):
        raise HTTPException(
            status_code=400,
            detail="Folder name must not contain separators or '..'",
        )
    try:
        target = create_media_folder(
            _normalize_relative_path(body.parent_path),
            body.name,
            settings.general.resolved_media_dirs(),
        )
    except InvalidMediaPathError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except (MediaFileCollisionError, ReadOnlyMediaRootError) as exc:
        raise _media_file_conflict(exc)
    relative_path = "/".join(
        part for part in [body.parent_path.strip("/\\"), target.name] if part
    )
    return MediaFolderCreateResponse(path=relative_path, name=target.name)


@router.post("/bulk-delete", response_model=MediaBulkDeleteResponse)
def bulk_delete_media(body: MediaBulkDeleteRequest, session: Session = Depends(get_session)):
    _require_media_mutations_allowed()
    return resolve_media_action(
        session, action=body.action, media_ids=body.media_ids,
        select_all=False, base_filter=True, filter_ids=False, allow_empty=True,
    )


@router.post("/bulk-move", response_model=MediaBulkMoveResponse)
def bulk_move_media(
    body: MediaBulkMoveRequest,
    session: Session = Depends(get_session),
):
    _require_media_mutations_allowed()
    moved_ids: list[int] = []
    skipped: list[MediaBulkMoveSkipped] = []
    from app.services.media_files import rollback_media_moves

    moves: list[tuple[Path, Path]] = []
    try:
        for media_id in dict.fromkeys(body.media_ids):
            media = session.get(Media, media_id)
            if media is None:
                skipped.append(MediaBulkMoveSkipped(id=media_id, reason="Media not found"))
                continue
            source = Path(media.path).expanduser().resolve()
            try:
                target = _move_media(media, body.destination_dir)
            except (
                InvalidMediaPathError,
                MediaFileCollisionError,
                MediaFileMissingError,
                ReadOnlyMediaRootError,
            ) as exc:
                skipped.append(MediaBulkMoveSkipped(id=media_id, reason=str(exc)))
                continue
            moves.append((source, target))
            media.path = os.fspath(target)
            session.add(media)
            moved_ids.append(media_id)
        safe_commit(session)
    except Exception:
        session.rollback()
        rollback_media_moves(moves)
        raise
    return MediaBulkMoveResponse(moved_ids=moved_ids, skipped=skipped)


@router.post("/{media_id}/move", response_model=MediaPreview)
def move_media(
    media_id: int,
    body: MediaMoveRequest,
    session: Session = Depends(get_session),
):
    _require_media_mutations_allowed()
    media = session.get(Media, media_id)
    if media is None:
        raise HTTPException(status_code=404, detail="Media not found")
    source = Path(media.path).expanduser().resolve()
    try:
        target = _move_media(media, body.destination_dir)
    except (
        InvalidMediaPathError,
        MediaFileCollisionError,
        MediaFileMissingError,
        ReadOnlyMediaRootError,
    ) as exc:
        raise _media_file_conflict(exc)
    media.path = os.fspath(target)
    session.add(media)
    try:
        safe_commit(session)
    except Exception:
        from app.services.media_files import rollback_media_moves

        session.rollback()
        rollback_media_moves([(source, target)])
        raise
    session.refresh(media)
    return media


@router.post("/{media_id}/rename", response_model=MediaPreview)
def rename_media(
    media_id: int,
    body: MediaRenameRequest,
    session: Session = Depends(get_session),
):
    _require_media_mutations_allowed()
    media = session.get(Media, media_id)
    if media is None:
        raise HTTPException(status_code=404, detail="Media not found")
    source = Path(media.path).expanduser().resolve()
    try:
        target = _rename_media(media, body.filename)
    except InvalidMediaPathError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except (
        MediaFileCollisionError,
        MediaFileMissingError,
        ReadOnlyMediaRootError,
    ) as exc:
        raise _media_file_conflict(exc)
    media.path = os.fspath(target)
    media.filename = target.name
    session.add(media)
    try:
        safe_commit(session)
    except Exception:
        from app.services.media_files import rollback_media_moves

        session.rollback()
        rollback_media_moves([(source, target)])
        raise
    session.refresh(media)
    return media


@router.post("/{media_id}/edit", response_model=MediaDetail)
def edit_media(
    media_id: int,
    body: MediaEditRequest,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
):
    _require_media_mutations_allowed()
    media = session.get(Media, media_id)
    if media is None:
        raise HTTPException(status_code=404, detail="Media not found")
    if media.duration is not None:
        raise HTTPException(status_code=400, detail="Videos cannot be edited")
    if not body.ops:
        raise HTTPException(status_code=400, detail="At least one edit is required")

    try:
        result_media = edit_media_record(
            session,
            media,
            body.ops,
            body.mode,
            design_state=body.design_state,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (
        InvalidMediaPathError,
        MediaFileMissingError,
        ReadOnlyMediaRootError,
        OSError,
    ) as exc:
        raise _media_file_conflict(exc) from exc

    _queue_edited_media(result_media.id, session, background_tasks)
    from .core import get_media

    return get_media(result_media.id, session)


@router.get("/{media_id}/face-crops", response_model=list[FaceCropSuggestion])
def get_face_crops(
    media_id: int,
    person_id: int | None = None,
    framing: Framing = Query(default="portrait"),
    aspect: CropAspect = Query(default="free"),
    session: Session = Depends(get_session),
) -> list[FaceCropSuggestion]:
    media = session.get(Media, media_id)
    if media is None:
        raise HTTPException(status_code=404, detail="Media not found")
    if not media.width or not media.height:
        raise HTTPException(status_code=409, detail="Media dimensions are unavailable")
    faces = list(session.exec(select(Face).where(Face.media_id == media_id)).all())
    if person_id is not None:
        faces.sort(key=lambda face: (face.person_id != person_id, face.id))
    else:
        faces.sort(key=lambda face: face.id)
    suggestions: list[FaceCropSuggestion] = []
    for face in faces:
        face_px = bbox_to_source_pixels(face.bbox, media.width, media.height)
        crop, output = suggest_crop(
            face_px,
            media.width,
            media.height,
            framing,
            aspect,
        )
        suggestions.append(
            FaceCropSuggestion(
                face_id=face.id,
                person_id=face.person_id,
                face_bbox=face_px,
                crop_op=crop,
                output=output,
            )
        )
    return suggestions


@router.post("/batch-edit", response_model=ProcessingTask)
def start_batch_edit(
    body: MediaBatchEditRequest,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
):
    _require_media_mutations_allowed()
    if not body.media_ids:
        raise HTTPException(status_code=400, detail="At least one media id is required")
    if not body.ops:
        raise HTTPException(status_code=400, detail="At least one edit is required")
    from app.tasks import create_and_run_task
    from app.tasks.batch_edit import batch_edit_media

    payload = body.model_dump(mode="json")
    return create_and_run_task(
        session=session,
        background_tasks=background_tasks,
        task_type="batch_edit_media",
        callable_task=lambda task_id: batch_edit_media(task_id, payload),
        reuse_running=False,
    )


@router.get("/{media_id}/neighbors", response_model=MediaNeighbors)
def get_neighbors(
    media_id: int,
    session: Session = Depends(get_session),
    sort: Annotated[str, Query(enum=["newest", "latest"])] = "newest",
    filter_people: list[int] | None = Query(
        [], description="Provide a persons context for navigation"
    ),
):
    if sort == "newest":
        sort_col = Media.created_at
        sort_col_name = "created_at"
    elif sort == "latest":
        sort_col = Media.inserted_at
        sort_col_name = "inserted_at"
    else:
        raise ValueError(f"Unsupported sort option: {sort}")

    original = session.get(Media, media_id)
    if not original:
        raise HTTPException(404, "Media not found")

    original_sort_value = getattr(original, sort_col_name)
    q = exclude_missing(select(Media))

    if filter_people:
        media_links_union = union_all(
            select(Face.media_id.label("media_id")).where(
                Face.person_id.in_(filter_people)
            ),
            select(PersonMediaLink.media_id.label("media_id")).where(
                PersonMediaLink.person_id.in_(filter_people)
            ),
        ).subquery()
        q = q.where(Media.id.in_(select(media_links_union.c.media_id)))

    previous_query = (
        q
        .where(tuple_(sort_col, Media.id) > (original_sort_value, original.id))
        .order_by(sort_col.asc(), Media.id.asc())
        .limit(1)
    )
    next_query = (
        q
        .where(tuple_(sort_col, Media.id) < (original_sort_value, original.id))
        .order_by(sort_col.desc(), Media.id.desc())
        .limit(1)
    )
    prev_row = session.exec(previous_query).first()
    next_row = session.exec(next_query).first()
    next_media = None
    previous_media = None
    if next_row:
        next_media = MediaPreview.model_validate(next_row)
    if prev_row:
        previous_media = MediaPreview.model_validate(prev_row)
    return MediaNeighbors(
        next_media=next_media,
        previous_media=previous_media,
    )


@router.get("/exif/{media_id}", response_model=ExifData)
def read_exif(media_id: int, session=Depends(get_session)):
    ex = session.exec(
        select(ExifData).where(ExifData.media_id == media_id)
    ).first()
    if not ex:
        raise HTTPException(404, "No EXIF data")
    return ex
