from __future__ import annotations

from datetime import datetime
from functools import partial
from pathlib import Path

import yaml
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.api.exports import _person_media
from app.config import settings
from app.database import get_session
from app.models import (
    AnnotationKind,
    DatasetExport,
    DatasetExportLayout,
    DatasetExportStatus,
    DatasetItem,
    Face,
    Media,
    MediaAnnotation,
    Person,
    ProcessingTask,
    ProcessingTaskRead,
    Status,
    TrainingDataset,
    TrainingRun,
    TrainingSample,
)
from app.schemas.dataset import (
    AutoSelectRequest,
    AutoSelectResult,
    DatasetBatchCropRequest,
    DatasetBatchCropResult,
    DatasetBatchCropSkipped,
    DatasetCaptionCursorPage,
    DatasetCaptionGenerateRequest,
    DatasetCaptionRead,
    DatasetCaptionReviewedRead,
    DatasetCaptionUpdate,
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
    FillGapsRequest,
    FillGapsResult,
    RegularizationRequest,
    RunLikenessRead,
    TrainingRunRead,
    TrainingRunRequest,
    TrainingHealthRead,
    TrainingPresetRead,
    TrainingSampleRead,
)
from app.schemas.media import MediaPreview
from app.services.caption_lint import lint_caption
from app.services.datasets import (
    caption_body_and_source,
    render_caption,
    resolve_caption,
    slugify,
)
from app.services.training_runs import (
    cancel_run,
    create_run,
    reconcile_runs,
    run_checkpoints,
)
from app.services.training_presets import (
    PRESETS,
    default_preset_id,
    get_preset,
    launcher_health,
)
from app.services.face_crops import bbox_to_source_pixels, suggest_crop
from app.services.curation import (
    auto_select_dataset,
    build_regularization_dataset,
    compute_dataset_analysis,
    dataset_gaps,
    fill_dataset_gaps,
)
from app.tasks.common import create_and_run_task
from app.tasks.dataset_export import export_dataset
from app.tasks.dataset_caption_generation import generate_dataset_captions
from app.tasks.backfill import run_pose_backfill


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


def _run_read(run: TrainingRun) -> TrainingRunRead:
    lr: float | None = None
    rank: int | None = None
    try:
        config = yaml.safe_load(run.config_yaml)
        process = config["config"]["process"][0]
        lr = float(process["train"]["lr"])
        rank = int(process["network"]["linear"])
    except (KeyError, IndexError, TypeError, ValueError, yaml.YAMLError):
        pass
    return TrainingRunRead.model_validate(run).model_copy(
        update={"checkpoints": run_checkpoints(run), "lr": lr, "rank": rank}
    )


def _likeness_read(session: Session, run: TrainingRun) -> RunLikenessRead:
    from app.services.likeness import likeness_counts

    summary = run.likeness_summary or {}
    steps = summary.get("steps") if isinstance(summary, dict) else []
    scored, pending = likeness_counts(session, run.id)
    return RunLikenessRead(
        run_id=run.id,
        steps=steps if isinstance(steps, list) else [],
        best_step=run.likeness_best_step,
        best=run.likeness_best,
        scored=scored,
        pending=pending,
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


@router.get("/training/health", response_model=TrainingHealthRead)
def get_training_health() -> dict:
    return launcher_health()


@router.get("/training/presets", response_model=list[TrainingPresetRead])
def list_training_presets() -> list[TrainingPresetRead]:
    health = launcher_health()
    default_id = default_preset_id()
    return [
        TrainingPresetRead(
            id=preset.id,
            label=preset.label,
            description=preset.description,
            requires_hf_token=preset.requires_hf_token,
            is_default=preset.id == default_id,
            available=(
                not preset.requires_hf_token
                or health["hf_token_configured"] is True
            ),
        )
        for preset in PRESETS.values()
    ]


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
        item.origin = "crop"
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


@router.get("/{dataset_id}/gaps")
def get_gaps(dataset_id: int, session: Session = Depends(get_session)) -> list[dict]:
    return dataset_gaps(session, _dataset_or_404(session, dataset_id))


@router.post("/{dataset_id}/fill-gaps", response_model=FillGapsResult)
def fill_gaps(
    dataset_id: int,
    payload: FillGapsRequest,
    session: Session = Depends(get_session),
) -> FillGapsResult:
    _mutating()
    added = fill_dataset_gaps(
        session,
        _dataset_or_404(session, dataset_id),
        **payload.model_dump(),
    )
    return FillGapsResult(added_ids=added)


@router.post("/{dataset_id}/pose-backfill", response_model=ProcessingTask)
def pose_backfill(
    dataset_id: int,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
) -> ProcessingTask:
    _mutating()
    _dataset_or_404(session, dataset_id)
    return create_and_run_task(
        session,
        background_tasks,
        "pose_backfill",
        partial(run_pose_backfill, dataset_id=dataset_id),
        reuse_running=False,
    )


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


@router.post("/{dataset_id}/train", response_model=TrainingRunRead)
def start_training_run(
    dataset_id: int,
    payload: TrainingRunRequest,
    session: Session = Depends(get_session),
) -> TrainingRunRead:
    _mutating()
    dataset = _dataset_or_404(session, dataset_id)
    preset_id = payload.base_model or default_preset_id()
    preset = get_preset(preset_id)
    if preset is None:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown base model preset: {preset_id}",
        )
    health = launcher_health()
    if preset.requires_hf_token and health["hf_token_configured"] is False:
        raise HTTPException(
            status_code=409,
            detail=(
                "This base model needs a Hugging Face token on the training "
                "host. Set HF_TOKEN in the launcher environment file."
            ),
        )
    if payload.export_id is not None:
        export = session.get(DatasetExport, payload.export_id)
        if export is None or export.dataset_id != dataset_id:
            raise HTTPException(status_code=404, detail="Dataset export not found")
        if export.status != DatasetExportStatus.COMPLETED:
            raise HTTPException(status_code=409, detail="Dataset export is not completed")
    else:
        export = session.exec(
            select(DatasetExport)
            .where(
                DatasetExport.dataset_id == dataset_id,
                DatasetExport.status == DatasetExportStatus.COMPLETED,
            )
            .order_by(DatasetExport.created_at.desc(), DatasetExport.id.desc())
        ).first()
        if export is None:
            raise HTTPException(
                status_code=409,
                detail="Complete a dataset export before starting training",
            )
    try:
        params = payload.model_dump()
        params["base_model"] = preset_id
        run = create_run(session, dataset, export, params)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _run_read(run)


@router.get("/{dataset_id}/runs", response_model=list[TrainingRunRead])
def list_training_runs(
    dataset_id: int, session: Session = Depends(get_session)
) -> list[TrainingRunRead]:
    _dataset_or_404(session, dataset_id)
    reconcile_runs(session)
    runs = session.exec(
        select(TrainingRun)
        .where(TrainingRun.dataset_id == dataset_id)
        .order_by(TrainingRun.created_at.desc(), TrainingRun.id.desc())
    ).all()
    return [_run_read(run) for run in runs]


@router.get("/{dataset_id}/runs/likeness", response_model=list[RunLikenessRead])
def compare_training_run_likeness(
    dataset_id: int,
    run_ids: str | None = Query(default=None),
    session: Session = Depends(get_session),
) -> list[RunLikenessRead]:
    _dataset_or_404(session, dataset_id)
    requested: list[int] | None = None
    if run_ids:
        try:
            requested = list(dict.fromkeys(int(value) for value in run_ids.split(",")))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="run_ids must be comma-separated integers") from exc
    statement = select(TrainingRun).where(TrainingRun.dataset_id == dataset_id)
    if requested is not None:
        statement = statement.where(TrainingRun.id.in_(requested))
    runs = session.exec(statement.order_by(TrainingRun.created_at, TrainingRun.id)).all()
    return [_likeness_read(session, run) for run in runs]


@router.get("/runs/{run_id}", response_model=TrainingRunRead)
def get_training_run(
    run_id: int, session: Session = Depends(get_session)
) -> TrainingRunRead:
    reconcile_runs(session)
    run = session.get(TrainingRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Training run not found")
    return _run_read(run)


@router.get("/runs/{run_id}/likeness", response_model=RunLikenessRead)
def get_training_run_likeness(
    run_id: int, session: Session = Depends(get_session)
) -> RunLikenessRead:
    run = session.get(TrainingRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Training run not found")
    return _likeness_read(session, run)


@router.post("/runs/{run_id}/rescore", status_code=202)
def rescore_training_run(
    run_id: int, session: Session = Depends(get_session)
) -> dict[str, int]:
    _mutating()
    run = session.get(TrainingRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Training run not found")
    samples = session.exec(
        select(TrainingSample).where(TrainingSample.run_id == run_id)
    ).all()
    for sample in samples:
        sample.likeness = None
        sample.face_count = None
        sample.face_bbox = None
        sample.scored_at = None
        session.add(sample)
    run.likeness_best_step = None
    run.likeness_best = None
    run.likeness_summary = None
    session.add(run)
    session.commit()
    return {"queued": len(samples)}


@router.post("/runs/{run_id}/cancel", response_model=TrainingRunRead)
def cancel_training_run(
    run_id: int, session: Session = Depends(get_session)
) -> TrainingRunRead:
    _mutating()
    run = session.get(TrainingRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Training run not found")
    try:
        return _run_read(cancel_run(session, run))
    except OSError as exc:
        raise HTTPException(status_code=409, detail=f"Could not request cancellation: {exc}") from exc


@router.get("/runs/{run_id}/samples", response_model=list[TrainingSampleRead])
def list_training_samples(
    run_id: int, session: Session = Depends(get_session)
) -> list[TrainingSampleRead]:
    run = session.get(TrainingRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Training run not found")
    reconcile_runs(session)
    return list(
        session.exec(
            select(TrainingSample)
            .where(TrainingSample.run_id == run_id)
            .order_by(TrainingSample.step, TrainingSample.id)
        ).all()
    )


@router.get("/runs/{run_id}/samples/{sample_id}/image", response_class=FileResponse)
def get_training_sample_image(
    run_id: int,
    sample_id: int,
    session: Session = Depends(get_session),
) -> FileResponse:
    run = session.get(TrainingRun, run_id)
    sample = session.get(TrainingSample, sample_id)
    if run is None or sample is None or sample.run_id != run_id:
        raise HTTPException(status_code=404, detail="Training sample not found")
    run_dir = Path(run.run_dir).resolve()
    sample_path = Path(sample.path).resolve()
    if not sample_path.is_relative_to(run_dir) or not sample_path.is_file():
        raise HTTPException(status_code=404, detail="Training sample image not found")
    return FileResponse(sample_path)


# Phase 17 caption review routes are kept together to ease stacked-branch merges.


def _caption_item_or_404(
    session: Session, dataset_id: int, item_id: int
) -> DatasetItem:
    item = session.get(DatasetItem, item_id)
    if item is None or item.dataset_id != dataset_id:
        raise HTTPException(status_code=404, detail="Dataset item not found")
    return item


@router.get("/{dataset_id}/captions", response_model=DatasetCaptionCursorPage)
def list_captions(
    dataset_id: int,
    filter: str = Query(
        default="all",
        pattern="^(all|findings|candidate|approved|missing)$",
    ),
    cursor: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    session: Session = Depends(get_session),
) -> DatasetCaptionCursorPage:
    dataset = _dataset_or_404(session, dataset_id)
    items = list(
        session.exec(
            select(DatasetItem)
            .where(DatasetItem.dataset_id == dataset_id)
            .order_by(DatasetItem.position, DatasetItem.id)
        ).all()
    )
    media_ids = [item.media_id for item in items]
    media_by_id = (
        {
            media.id: media
            for media in session.exec(select(Media).where(Media.id.in_(media_ids))).all()
        }
        if media_ids
        else {}
    )
    annotations_by_media: dict[int, list[MediaAnnotation]] = {
        media_id: [] for media_id in media_ids
    }
    if media_ids:
        annotations = session.exec(
            select(MediaAnnotation)
            .where(
                MediaAnnotation.media_id.in_(media_ids),
                MediaAnnotation.kind == AnnotationKind.CAPTION,
            )
            .order_by(MediaAnnotation.media_id, MediaAnnotation.revision.desc())
        ).all()
        for annotation in annotations:
            annotations_by_media.setdefault(annotation.media_id, []).append(annotation)
    person = session.get(Person, dataset.person_id) if dataset.person_id else None

    resolved: list[tuple[DatasetItem, Media, str, str, str | None]] = []
    for item in items:
        media = media_by_id.get(item.media_id)
        if media is None:
            continue
        body, source, _ = caption_body_and_source(
            dataset, item, annotations_by_media.get(item.media_id, [])
        )
        resolved.append((item, media, body, source, render_caption(dataset, body, person)))

    rows: list[DatasetCaptionRead] = []
    bodies = [body for _, _, body, _, _ in resolved if body]
    for item, media, body, source, effective_caption in resolved:
        other_captions = list(bodies)
        if body:
            other_captions.remove(body)
        findings = lint_caption(body, dataset, other_captions) if body else []
        latest = next(iter(annotations_by_media.get(item.media_id, [])), None)
        row = DatasetCaptionRead(
            item_id=int(item.id),
            media_id=item.media_id,
            position=item.position,
            excluded=item.excluded,
            media=_media_preview(media),
            caption=body,
            effective_caption=effective_caption,
            source=source,
            annotation_id=latest.id if latest else None,
            review_status=latest.review_status if latest else None,
            caption_reviewed_at=item.caption_reviewed_at,
            findings=[finding.__dict__ for finding in findings],
        )
        if filter == "findings" and not row.findings:
            continue
        if filter in {"candidate", "approved"} and (
            row.review_status is None or row.review_status.value != filter
        ):
            continue
        if filter == "missing" and source not in {"template", "none"}:
            continue
        rows.append(row)

    offset = int(cursor or 0)
    page = rows[offset : offset + limit + 1]
    return DatasetCaptionCursorPage(
        items=page[:limit],
        next_cursor=(
            str(offset + limit) if len(page) > limit and page[:limit] else None
        ),
    )


@router.patch(
    "/{dataset_id}/items/{item_id}/caption", response_model=DatasetItemRead
)
def update_item_caption(
    dataset_id: int,
    item_id: int,
    payload: DatasetCaptionUpdate,
    session: Session = Depends(get_session),
) -> DatasetItemRead:
    _mutating()
    dataset = _dataset_or_404(session, dataset_id)
    item = _caption_item_or_404(session, dataset_id, item_id)
    item.caption_override = payload.text.strip() or None
    item.caption_reviewed_at = datetime.now()
    session.add(item)
    session.commit()
    session.refresh(item)
    return _item_read(session, dataset, item)


@router.post(
    "/{dataset_id}/items/{item_id}/caption/reviewed",
    response_model=DatasetCaptionReviewedRead,
)
def mark_item_caption_reviewed(
    dataset_id: int,
    item_id: int,
    session: Session = Depends(get_session),
) -> DatasetCaptionReviewedRead:
    _mutating()
    _dataset_or_404(session, dataset_id)
    item = _caption_item_or_404(session, dataset_id, item_id)
    item.caption_reviewed_at = datetime.now()
    session.add(item)
    session.commit()
    session.refresh(item)
    return DatasetCaptionReviewedRead(
        item_id=int(item.id), caption_reviewed_at=item.caption_reviewed_at
    )


@router.post(
    "/{dataset_id}/captions/generate", response_model=ProcessingTaskRead
)
def start_caption_generation(
    dataset_id: int,
    payload: DatasetCaptionGenerateRequest,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
) -> ProcessingTask:
    _mutating()
    _dataset_or_404(session, dataset_id)
    if not settings.annotations.enabled:
        raise HTTPException(status_code=503, detail="The annotation backend is disabled.")
    active = session.exec(
        select(ProcessingTask).where(
            ProcessingTask.task_type == "dataset_caption_generation",
            ProcessingTask.status.in_((Status.PENDING, Status.RUNNING)),
        )
    ).all()
    if any((task.result or {}).get("dataset_id") == dataset_id for task in active):
        raise HTTPException(
            status_code=409,
            detail="Caption generation is already running for this dataset.",
        )
    task = create_and_run_task(
        session,
        background_tasks,
        "dataset_caption_generation",
        partial(
            generate_dataset_captions,
            dataset_id=dataset_id,
            only_missing=payload.only_missing,
        ),
        reuse_running=False,
    )
    task.result = {
        "dataset_id": dataset_id,
        "generated": 0,
        "skipped": 0,
        "failed": 0,
    }
    session.add(task)
    session.commit()
    session.refresh(task)
    return task
