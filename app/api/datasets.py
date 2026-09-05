from __future__ import annotations

from datetime import datetime
from functools import partial
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.api.exports import _person_media
from app.config import settings
from app.database import get_session
from app.models import (
    DatasetExport,
    DatasetExportLayout,
    DatasetItem,
    Face,
    Media,
    Person,
    TrainingDataset,
)
from app.schemas.dataset import (
    AutoSelectRequest,
    AutoSelectResult,
    DatasetBatchCropRequest,
    DatasetBatchCropResult,
    DatasetBatchCropSkipped,
    DatasetCreate,
    DatasetExportRead,
    DatasetExportRequest,
    DatasetItemCursorPage,
    DatasetItemRead,
    DatasetItemsRequest,
    DatasetItemsResult,
    DatasetItemUpdate,
    DatasetRead,
    DatasetUpdate,
    FaceSummary,
    RegularizationRequest,
)
from app.schemas.media import MediaPreview
from app.services.datasets import resolve_caption, slugify
from app.services.face_crops import bbox_to_source_pixels, suggest_crop
from app.services.curation import (
    auto_select_dataset,
    build_regularization_dataset,
    compute_dataset_analysis,
)
from app.tasks.common import create_and_run_task
from app.tasks.dataset_export import export_dataset


router = APIRouter()


def _mutating() -> None:
    if settings.general.presentation_mode:
        raise HTTPException(status_code=403, detail="Not allowed in presentation_mode mode.")


def _dataset_or_404(session: Session, dataset_id: int) -> TrainingDataset:
    dataset = session.get(TrainingDataset, dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="Dataset not found")
    return dataset


def _media_preview(media: Media) -> MediaPreview:
    return MediaPreview.model_validate(media)


def _host_output_dir(output_dir: str) -> str | None:
    if not output_dir or settings.general.datasets_host_root is None:
        return None
    try:
        relative = Path(output_dir).relative_to(settings.general.resolved_datasets_dir())
    except ValueError:
        return None
    return str(settings.general.datasets_host_root / relative)


def _export_read(export: DatasetExport) -> DatasetExportRead:
    return DatasetExportRead(
        **export.model_dump(),
        host_output_dir=_host_output_dir(export.output_dir),
        launch_command=("python run.py config.yaml" if export.layout == DatasetExportLayout.AI_TOOLKIT else None),
    )


def _dataset_read(session: Session, dataset: TrainingDataset) -> DatasetRead:
    item_count = int(session.exec(select(func.count(DatasetItem.id)).where(DatasetItem.dataset_id == dataset.id)).one())
    included_count = int(
        session.exec(
            select(func.count(DatasetItem.id)).where(
                DatasetItem.dataset_id == dataset.id, DatasetItem.excluded.is_(False)
            )
        ).one()
    )
    cover = session.get(Media, dataset.cover_media_id) if dataset.cover_media_id else None
    if cover is None:
        first_item = session.exec(
            select(DatasetItem)
            .where(DatasetItem.dataset_id == dataset.id)
            .order_by(DatasetItem.position, DatasetItem.id)
        ).first()
        cover = session.get(Media, first_item.media_id) if first_item else None
    last_export = session.exec(
        select(DatasetExport)
        .where(DatasetExport.dataset_id == dataset.id)
        .order_by(DatasetExport.created_at.desc())
    ).first()
    return DatasetRead(
        **dataset.model_dump(),
        item_count=item_count,
        included_count=included_count,
        cover=_media_preview(cover) if cover else None,
        last_export=_export_read(last_export) if last_export else None,
    )


def _unique_slug(session: Session, base: str) -> str:
    candidate = base or "dataset"
    suffix = 2
    while session.exec(select(TrainingDataset.id).where(TrainingDataset.slug == candidate)).first() is not None:
        candidate = f"{base or 'dataset'}-{suffix}"
        suffix += 1
    return candidate


def _create_dataset(session: Session, payload: DatasetCreate) -> TrainingDataset:
    person = session.get(Person, payload.person_id) if payload.person_id else None
    if payload.person_id and person is None:
        raise HTTPException(status_code=404, detail="Person not found")
    trigger = (payload.trigger_word or slugify(person.name or "") if person else payload.trigger_word) or slugify(payload.name)
    gender_classes = {"female": "woman", "male": "man", "f": "woman", "m": "man"}
    class_token = payload.class_token or (
        gender_classes.get(person.gender.lower(), person.gender.lower())
        if person and person.gender
        else "person"
    )
    dataset = TrainingDataset(
        **payload.model_dump(exclude={"slug", "trigger_word", "class_token"}),
        slug=_unique_slug(session, slugify(payload.slug or payload.name)),
        trigger_word=trigger,
        class_token=class_token,
    )
    session.add(dataset)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="Dataset slug already exists") from exc
    session.refresh(dataset)
    return dataset


@router.get("", response_model=list[DatasetRead])
def list_datasets(session: Session = Depends(get_session)) -> list[DatasetRead]:
    datasets = session.exec(select(TrainingDataset).order_by(TrainingDataset.updated_at.desc())).all()
    return [_dataset_read(session, dataset) for dataset in datasets]


@router.post("", response_model=DatasetRead)
def create_dataset(payload: DatasetCreate, session: Session = Depends(get_session)) -> DatasetRead:
    _mutating()
    return _dataset_read(session, _create_dataset(session, payload))


@router.get("/exports/{export_id}/manifest")
def get_export_manifest(export_id: int, session: Session = Depends(get_session)) -> JSONResponse:
    export = session.get(DatasetExport, export_id)
    if export is None:
        raise HTTPException(status_code=404, detail="Export not found")
    if export.manifest is None:
        raise HTTPException(status_code=409, detail="Manifest is not ready")
    return JSONResponse(export.manifest)


@router.post("/from-person/{person_id}", response_model=DatasetRead)
def create_from_person(person_id: int, session: Session = Depends(get_session)) -> DatasetRead:
    _mutating()
    person, media = _person_media(person_id, session)
    name = person.name or f"Person {person_id}"
    dataset = _create_dataset(session, DatasetCreate(name=name, person_id=person_id))
    position = 0
    for item in media:
        if item.duration is not None:
            continue
        session.add(DatasetItem(dataset_id=dataset.id, media_id=item.id, position=position))
        position += 1
    session.commit()
    return _dataset_read(session, dataset)


@router.get("/{dataset_id}", response_model=DatasetRead)
def get_dataset(dataset_id: int, session: Session = Depends(get_session)) -> DatasetRead:
    return _dataset_read(session, _dataset_or_404(session, dataset_id))


@router.patch("/{dataset_id}", response_model=DatasetRead)
def update_dataset(dataset_id: int, payload: DatasetUpdate, session: Session = Depends(get_session)) -> DatasetRead:
    _mutating()
    dataset = _dataset_or_404(session, dataset_id)
    values = payload.model_dump(exclude_unset=True)
    if "slug" in values and values["slug"]:
        values["slug"] = slugify(values["slug"])
    for key, value in values.items():
        setattr(dataset, key, value)
    dataset.updated_at = datetime.now()
    session.add(dataset)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="Dataset slug already exists") from exc
    session.refresh(dataset)
    return _dataset_read(session, dataset)


@router.delete("/{dataset_id}", status_code=204)
def delete_dataset(dataset_id: int, session: Session = Depends(get_session)) -> None:
    _mutating()
    dataset = _dataset_or_404(session, dataset_id)
    session.delete(dataset)
    session.commit()


@router.post("/{dataset_id}/items", response_model=DatasetItemsResult)
def add_items(dataset_id: int, payload: DatasetItemsRequest, session: Session = Depends(get_session)) -> DatasetItemsResult:
    _mutating()
    _dataset_or_404(session, dataset_id)
    existing = set(
        session.exec(select(DatasetItem.media_id).where(DatasetItem.dataset_id == dataset_id)).all()
    )
    max_position = session.exec(select(func.max(DatasetItem.position)).where(DatasetItem.dataset_id == dataset_id)).one()
    position = int(max_position if max_position is not None else -1) + 1
    added: list[int] = []
    skipped: list[int] = []
    for media_id in dict.fromkeys(payload.media_ids):
        media = session.get(Media, media_id)
        if media is None or media.duration is not None or media_id in existing:
            skipped.append(media_id)
            continue
        session.add(DatasetItem(dataset_id=dataset_id, media_id=media_id, position=position))
        existing.add(media_id)
        added.append(media_id)
        position += 1
    session.commit()
    return DatasetItemsResult(added_ids=added, skipped_ids=skipped)


@router.delete("/{dataset_id}/items", response_model=DatasetItemsResult)
def remove_items(dataset_id: int, payload: DatasetItemsRequest, session: Session = Depends(get_session)) -> DatasetItemsResult:
    _mutating()
    _dataset_or_404(session, dataset_id)
    rows = session.exec(
        select(DatasetItem).where(
            DatasetItem.dataset_id == dataset_id, DatasetItem.media_id.in_(payload.media_ids)
        )
    ).all()
    removed = [row.media_id for row in rows]
    for row in rows:
        session.delete(row)
    session.commit()
    return DatasetItemsResult(added_ids=removed, skipped_ids=[item for item in payload.media_ids if item not in removed])


@router.patch("/{dataset_id}/items/{item_id}", response_model=DatasetItemRead)
def update_item(dataset_id: int, item_id: int, payload: DatasetItemUpdate, session: Session = Depends(get_session)) -> DatasetItemRead:
    _mutating()
    dataset = _dataset_or_404(session, dataset_id)
    item = session.get(DatasetItem, item_id)
    if item is None or item.dataset_id != dataset_id:
        raise HTTPException(status_code=404, detail="Dataset item not found")
    for key, value in payload.model_dump(exclude_unset=True, mode="json").items():
        setattr(item, key, value)
    session.add(item)
    session.commit()
    session.refresh(item)
    return _item_read(session, dataset, item)


@router.post("/{dataset_id}/items/batch-crop", response_model=DatasetBatchCropResult)
def batch_crop_items(
    dataset_id: int,
    payload: DatasetBatchCropRequest,
    session: Session = Depends(get_session),
) -> DatasetBatchCropResult:
    _mutating()
    dataset = _dataset_or_404(session, dataset_id)
    query = select(DatasetItem).where(DatasetItem.dataset_id == dataset_id)
    if payload.item_ids is not None:
        query = query.where(DatasetItem.id.in_(payload.item_ids))
    items = list(session.exec(query.order_by(DatasetItem.position, DatasetItem.id)).all())
    updated_ids: list[int] = []
    skipped: list[DatasetBatchCropSkipped] = []

    requested = set(payload.item_ids or [])
    found = {int(item.id) for item in items}
    for missing_id in sorted(requested - found):
        skipped.append(DatasetBatchCropSkipped(item_id=missing_id, reason="Dataset item not found"))

    for item in items:
        item_id = int(item.id)
        if item.edit_ops and not payload.overwrite_existing_ops:
            skipped.append(DatasetBatchCropSkipped(item_id=item_id, reason="Existing edit ops"))
            continue
        if dataset.person_id is None:
            skipped.append(DatasetBatchCropSkipped(item_id=item_id, reason="No subject person"))
            continue
        media = session.get(Media, item.media_id)
        if media is None:
            skipped.append(DatasetBatchCropSkipped(item_id=item_id, reason="Media not found"))
            continue
        if not media.width or not media.height:
            skipped.append(DatasetBatchCropSkipped(item_id=item_id, reason="Media dimensions unavailable"))
            continue
        faces = list(
            session.exec(
                select(Face).where(
                    Face.media_id == media.id,
                    Face.person_id == dataset.person_id,
                )
            ).all()
        )
        if not faces:
            skipped.append(DatasetBatchCropSkipped(item_id=item_id, reason="No subject face"))
            continue
        face = max(faces, key=lambda candidate: candidate.bbox[2] * candidate.bbox[3])
        face_px = bbox_to_source_pixels(face.bbox, media.width, media.height)
        crop, _ = suggest_crop(
            face_px,
            media.width,
            media.height,
            payload.framing,
            payload.aspect,
        )
        item.edit_ops = [crop.model_dump(mode="json")]
        item.edit_design_state = None
        session.add(item)
        updated_ids.append(item_id)
    session.commit()
    return DatasetBatchCropResult(updated_ids=updated_ids, skipped=skipped)


def _item_read(
    session: Session,
    dataset: TrainingDataset,
    item: DatasetItem,
    metrics: dict | None = None,
) -> DatasetItemRead:
    media = session.get(Media, item.media_id)
    if media is None:
        raise HTTPException(status_code=404, detail="Media not found")
    face_query = select(Face).where(Face.media_id == media.id)
    if dataset.person_id is not None:
        face_query = face_query.where(Face.person_id == dataset.person_id)
    faces = list(session.exec(face_query).all())
    return DatasetItemRead(
        **item.model_dump(),
        media=_media_preview(media),
        effective_caption=resolve_caption(dataset, item, media, session.get(Person, dataset.person_id) if dataset.person_id else None, session),
        has_ops=bool(item.edit_ops),
        face_summary=FaceSummary(
            det_score=max((face.det_score for face in faces if face.det_score is not None), default=None),
            frontality=max((face.frontality for face in faces if face.frontality is not None), default=None),
            face_count=len(faces),
        ),
        metrics=metrics,
    )


@router.get("/{dataset_id}/items", response_model=DatasetItemCursorPage)
def list_items(
    dataset_id: int,
    cursor: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    include_excluded: bool = True,
    sort: str = Query(
        default="position",
        pattern="^(position|sharpness|frontality|face_ratio|identity_distance|brightness)$",
    ),
    session: Session = Depends(get_session),
) -> DatasetItemCursorPage:
    dataset = _dataset_or_404(session, dataset_id)
    query = select(DatasetItem).where(DatasetItem.dataset_id == dataset_id)
    if not include_excluded:
        query = query.where(DatasetItem.excluded.is_(False))
    rows = list(session.exec(query).all())
    analysis = compute_dataset_analysis(session, dataset)
    metrics = {entry["item_id"]: entry for entry in analysis["items"]}
    if sort == "position":
        rows.sort(key=lambda item: (item.position, item.id))
    else:
        metric_name = "brightness_mean" if sort == "brightness" else sort
        rows.sort(
            key=lambda item: (
                metrics.get(item.id, {}).get(metric_name) is not None,
                metrics.get(item.id, {}).get(metric_name) or 0,
                -item.id,
            ),
            reverse=True,
        )
    offset = int(cursor or 0)
    rows = rows[offset : offset + limit + 1]
    page = rows[:limit]
    return DatasetItemCursorPage(
        items=[_item_read(session, dataset, item, metrics.get(item.id)) for item in page],
        next_cursor=str(offset + limit) if len(rows) > limit and page else None,
    )


@router.get("/{dataset_id}/analysis")
def get_analysis(dataset_id: int, session: Session = Depends(get_session)) -> dict:
    return compute_dataset_analysis(session, _dataset_or_404(session, dataset_id))


@router.post("/{dataset_id}/auto-select", response_model=AutoSelectResult)
def auto_select(
    dataset_id: int,
    payload: AutoSelectRequest,
    session: Session = Depends(get_session),
) -> AutoSelectResult:
    _mutating()
    result = auto_select_dataset(
        session,
        _dataset_or_404(session, dataset_id),
        **payload.model_dump(),
    )
    return AutoSelectResult(**result)


@router.post("/{dataset_id}/regularization", response_model=DatasetRead)
def create_regularization(
    dataset_id: int,
    payload: RegularizationRequest,
    session: Session = Depends(get_session),
) -> DatasetRead:
    _mutating()
    regularization = build_regularization_dataset(
        session,
        _dataset_or_404(session, dataset_id),
        **payload.model_dump(),
    )
    return _dataset_read(session, regularization)


@router.post("/{dataset_id}/export", response_model=DatasetExportRead)
def start_export(
    dataset_id: int,
    payload: DatasetExportRequest,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
) -> DatasetExportRead:
    _mutating()
    dataset = _dataset_or_404(session, dataset_id)
    export = DatasetExport(dataset_id=dataset_id, layout=payload.layout or dataset.export_layout)
    session.add(export)
    session.commit()
    session.refresh(export)
    task = create_and_run_task(
        session,
        background_tasks,
        "export_dataset",
        partial(export_dataset, export_id=export.id),
        reuse_running=False,
    )
    export.task_id = task.id
    session.add(export)
    session.commit()
    session.refresh(export)
    return _export_read(export)


@router.get("/{dataset_id}/exports", response_model=list[DatasetExportRead])
def list_exports(dataset_id: int, session: Session = Depends(get_session)) -> list[DatasetExportRead]:
    _dataset_or_404(session, dataset_id)
    exports = session.exec(
        select(DatasetExport)
        .where(DatasetExport.dataset_id == dataset_id)
        .order_by(DatasetExport.created_at.desc())
    ).all()
    return [_export_read(export) for export in exports]
