from __future__ import annotations

import hashlib
import json
import re
import subprocess
import unicodedata
from datetime import datetime
from pathlib import Path

import yaml
from PIL import Image, ImageOps
from pydantic import TypeAdapter
from sqlmodel import Session, select

from app.config import settings
from app.models import (
    AnnotationKind,
    AnnotationReviewStatus,
    DatasetCaptionSource,
    DatasetExport,
    DatasetExportLayout,
    DatasetItem,
    Media,
    MediaAnnotation,
    Person,
    ProcessingTask,
    TrainingDataset,
)
from app.schemas.media import EditOp
from app.services.image_edits import apply_edit_ops
from app.tasks.state import set_task_progress


_EDIT_OPS = TypeAdapter(list[EditOp])


def slugify(value: str) -> str:
    """Return a stable, filesystem-safe lower-case slug."""
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", ascii_value.lower())).strip("-")


def _annotation_text(annotation: MediaAnnotation | None) -> str:
    if annotation is None:
        return ""
    content = annotation.content or {}
    value = content.get("caption") or content.get("text") or ""
    return str(value).strip()


def resolve_caption(
    dataset: TrainingDataset,
    item: DatasetItem,
    media: Media,
    person: Person | None,
    session: Session | None = None,
) -> str | None:
    """Resolve an item's effective caption without mutating annotations."""
    if dataset.caption_source == DatasetCaptionSource.NONE:
        return None

    caption = (item.caption_override or "").strip()
    if not caption and dataset.caption_source == DatasetCaptionSource.ANNOTATION and session:
        annotations = session.exec(
            select(MediaAnnotation)
            .where(
                MediaAnnotation.media_id == media.id,
                MediaAnnotation.kind == AnnotationKind.CAPTION,
            )
            .order_by(MediaAnnotation.revision.desc())
        ).all()
        approved = next(
            (
                annotation
                for annotation in annotations
                if annotation.review_status == AnnotationReviewStatus.APPROVED
            ),
            None,
        )
        caption = _annotation_text(approved or (annotations[0] if annotations else None))

    if caption and person and person.name:
        candidates = sorted(
            {person.name.strip(), slugify(person.name)}, key=len, reverse=True
        )
        for candidate in candidates:
            if candidate:
                caption = re.sub(re.escape(candidate), dataset.trigger_word, caption, flags=re.IGNORECASE)

    rendered = dataset.caption_template.format(
        trigger=dataset.trigger_word,
        **{"class": dataset.class_token},
        caption=caption,
    )
    rendered = re.sub(r"\s+", " ", rendered).strip()
    rendered = re.sub(r"\s*[,;:]\s*$", "", rendered).strip()
    return rendered or None


def pick_bucket(width: int, height: int, buckets: list[int]) -> int:
    """Pick the largest configured long side that does not upscale."""
    longest = max(width, height)
    eligible = [bucket for bucket in buckets if 0 < bucket <= longest]
    return max(eligible) if eligible else longest


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_revision() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parents[2],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def _ai_toolkit_config(dataset: TrainingDataset, output_dir: Path) -> dict:
    template_path = Path(__file__).resolve().parents[1] / "templates" / "ai_toolkit_lora.yaml"
    config = yaml.safe_load(template_path.read_text(encoding="utf-8"))
    process = config["config"]["process"][0]
    process["trigger_word"] = dataset.trigger_word
    container_dataset = output_dir / "dataset"
    datasets_dir = settings.general.resolved_datasets_dir()
    if settings.general.datasets_host_root:
        relative = container_dataset.relative_to(datasets_dir)
        folder_path = settings.general.datasets_host_root / relative
    else:
        folder_path = container_dataset
    process["datasets"][0]["folder_path"] = str(folder_path)
    process["datasets"][0]["resolution"] = sorted(set(dataset.buckets))
    process["sample"]["prompts"] = [
        f"photo of {dataset.trigger_word} {dataset.class_token}",
        f"portrait of {dataset.trigger_word} {dataset.class_token}, natural light",
        f"{dataset.trigger_word} {dataset.class_token}, full body, outdoors",
    ]
    config["config"]["name"] = dataset.slug
    return config


def build_export(session: Session, export_id: int, task_id: str) -> dict:
    """Materialize one immutable training dataset export."""
    export = session.get(DatasetExport, export_id)
    if export is None:
        raise ValueError(f"Dataset export {export_id} not found")
    dataset = session.get(TrainingDataset, export.dataset_id)
    if dataset is None:
        raise ValueError(f"Dataset {export.dataset_id} not found")
    task = session.get(ProcessingTask, task_id)
    if task is None:
        raise ValueError(f"Task {task_id} not found")

    items = list(
        session.exec(
            select(DatasetItem)
            .where(DatasetItem.dataset_id == dataset.id, DatasetItem.excluded.is_(False))
            .order_by(DatasetItem.position, DatasetItem.id)
        ).all()
    )
    regularization_dataset = (
        session.get(TrainingDataset, dataset.regularization_dataset_id)
        if dataset.regularization_dataset_id and export.layout == DatasetExportLayout.KOHYA
        else None
    )
    regularization_items = (
        list(
            session.exec(
                select(DatasetItem)
                .where(
                    DatasetItem.dataset_id == regularization_dataset.id,
                    DatasetItem.excluded.is_(False),
                )
                .order_by(DatasetItem.position, DatasetItem.id)
            ).all()
        )
        if regularization_dataset
        else []
    )
    root = settings.general.resolved_datasets_dir()
    output_dir = root / dataset.slug / datetime.now().strftime("%Y%m%d-%H%M%S")
    if output_dir.exists():
        output_dir = output_dir.with_name(f"{output_dir.name}-{export.id:02d}")
    output_dir.mkdir(parents=True, exist_ok=False)
    export.output_dir = str(output_dir)
    export.item_count = len(items)
    task.total = len(items) + len(regularization_items)
    session.add(export)
    session.add(task)
    session.commit()

    if export.layout == DatasetExportLayout.AI_TOOLKIT:
        images_dir = output_dir / "dataset"
    elif export.layout == DatasetExportLayout.KOHYA:
        images_dir = output_dir / "img" / f"{dataset.repeats}_{dataset.trigger_word} {dataset.class_token}"
    else:
        images_dir = output_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    person = session.get(Person, dataset.person_id) if dataset.person_id else None
    manifest_items: list[dict] = []
    for index, item in enumerate(items, start=1):
        session.refresh(task)
        if str(task.status) == "cancelled":
            break
        media = session.get(Media, item.media_id)
        if media is None:
            continue
        source_path = Path(media.path)
        with Image.open(source_path) as opened:
            image = ImageOps.exif_transpose(opened)
            if item.edit_ops:
                image = apply_edit_ops(image, _EDIT_OPS.validate_python(item.edit_ops))
            target_longest = pick_bucket(image.width, image.height, dataset.buckets)
            if max(image.size) > target_longest:
                scale = target_longest / max(image.size)
                image = image.resize(
                    (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
                    Image.Resampling.LANCZOS,
                )
            is_png = source_path.suffix.lower() == ".png"
            suffix = ".png" if is_png else ".jpg"
            output_path = images_dir / f"{index:04d}_{media.id}{suffix}"
            save_options = {"quality": 95} if not is_png else {}
            save_image = image if is_png else image.convert("RGB")
            save_image.save(
                output_path, format="PNG" if is_png else "JPEG", **save_options
            )

        caption = resolve_caption(dataset, item, media, person, session)
        if caption is not None:
            output_path.with_suffix(".txt").write_text(caption + "\n", encoding="utf-8")
        manifest_items.append(
            {
                "index": index,
                "media_id": media.id,
                "source_path": media.path,
                "source_sha256": _sha256(source_path),
                "output_file": str(output_path.relative_to(output_dir)),
                "output_sha256": _sha256(output_path),
                "width": image.width,
                "height": image.height,
                "ops": item.edit_ops or [],
                "caption": caption,
            }
        )
        task.processed = index
        session.add(task)
        session.commit()
        set_task_progress(
            task_id,
            current_step="exporting_dataset",
            current_item=f"{index} of {len(items)}",
        )

    manifest_regularization: list[dict] = []
    if regularization_dataset and regularization_items:
        reg_dir = output_dir / "reg" / f"{regularization_dataset.repeats}_{regularization_dataset.class_token}"
        reg_dir.mkdir(parents=True, exist_ok=True)
        for index, item in enumerate(regularization_items, start=1):
            media = session.get(Media, item.media_id)
            if media is None:
                continue
            source_path = Path(media.path)
            with Image.open(source_path) as opened:
                image = ImageOps.exif_transpose(opened)
                if item.edit_ops:
                    image = apply_edit_ops(image, _EDIT_OPS.validate_python(item.edit_ops))
                target_longest = pick_bucket(image.width, image.height, regularization_dataset.buckets)
                if max(image.size) > target_longest:
                    scale = target_longest / max(image.size)
                    image = image.resize(
                        (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
                        Image.Resampling.LANCZOS,
                    )
                is_png = source_path.suffix.lower() == ".png"
                output_path = reg_dir / f"{index:04d}_{media.id}{'.png' if is_png else '.jpg'}"
                (image if is_png else image.convert("RGB")).save(
                    output_path,
                    format="PNG" if is_png else "JPEG",
                    **({} if is_png else {"quality": 95}),
                )
            caption = resolve_caption(regularization_dataset, item, media, None, session)
            if caption is not None:
                output_path.with_suffix(".txt").write_text(caption + "\n", encoding="utf-8")
            manifest_regularization.append(
                {
                    "index": index,
                    "media_id": media.id,
                    "source_path": media.path,
                    "source_sha256": _sha256(source_path),
                    "output_file": str(output_path.relative_to(output_dir)),
                    "output_sha256": _sha256(output_path),
                    "width": image.width,
                    "height": image.height,
                    "ops": item.edit_ops or [],
                    "caption": caption,
                }
            )
            task.processed = len(items) + index
            session.add(task)
            session.commit()

    manifest = {
        "dataset": {
            "id": dataset.id,
            "name": dataset.name,
            "slug": dataset.slug,
            "trigger_word": dataset.trigger_word,
            "class_token": dataset.class_token,
            "buckets": dataset.buckets,
            "repeats": dataset.repeats,
            "layout": export.layout.value,
        },
        "app_revision": _git_revision(),
        "items": manifest_items,
        "regularization_items": manifest_regularization,
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    if export.layout == DatasetExportLayout.AI_TOOLKIT:
        (output_dir / "config.yaml").write_text(
            yaml.safe_dump(_ai_toolkit_config(dataset, output_dir), sort_keys=False),
            encoding="utf-8",
        )
        (output_dir / "README.md").write_text(
            "# Training\n\n```sh\npython run.py config.yaml\n```\n", encoding="utf-8"
        )
    export.manifest = manifest
    export.item_count = len(manifest_items)
    session.add(export)
    session.commit()
    return manifest
