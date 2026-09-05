from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

import yaml
from sqlmodel import Session, select

from app.config import settings
from app.models import (
    DatasetExport,
    DatasetExportStatus,
    TrainingDataset,
    TrainingRun,
    TrainingRunStatus,
    TrainingSample,
)
from app.services.datasets import _ai_toolkit_config
from app.services.training_presets import apply_preset, get_preset


TERMINAL_RUN_STATUSES = {
    TrainingRunStatus.COMPLETED,
    TrainingRunStatus.FAILED,
    TrainingRunStatus.CANCELLED,
}
_SAMPLE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed
    except ValueError:
        return None


def _host_path(path: Path) -> Path:
    datasets_dir = settings.general.resolved_datasets_dir()
    if settings.general.datasets_host_root:
        try:
            return settings.general.datasets_host_root / path.relative_to(datasets_dir)
        except ValueError:
            pass
    return path


def _container_path(path: Path) -> Path:
    """Inverse of ``_host_path``: map a host-side datasets path back into the container."""
    host_root = settings.general.datasets_host_root
    if host_root is not None:
        try:
            return settings.general.resolved_datasets_dir() / path.relative_to(host_root)
        except ValueError:
            pass
    return path


def _run_config(
    dataset: TrainingDataset,
    export: DatasetExport,
    run_dir: Path,
    params: Mapping[str, Any],
    stamp: str,
) -> tuple[dict[str, Any], str]:
    export_config = Path(export.output_dir) / "config.yaml"
    if export_config.is_file():
        loaded = yaml.safe_load(export_config.read_text(encoding="utf-8"))
        config = loaded if isinstance(loaded, dict) else {}
    else:
        config = _ai_toolkit_config(dataset, Path(export.output_dir))

    try:
        process = config["config"]["process"][0]
        network = process["network"]
        train = process["train"]
        sample = process["sample"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError("Export config is not a supported ai-toolkit config") from exc

    name = f"{dataset.slug}-{stamp}"
    steps = int(params.get("steps", 2000))
    rank = int(params.get("rank", 16))
    train["steps"] = steps
    train["lr"] = float(params.get("lr", 1e-4))
    network["linear"] = rank
    network["linear_alpha"] = rank
    prompts = params.get("sample_prompts")
    if prompts is not None:
        sample["prompts"] = [str(prompt).strip() for prompt in prompts if str(prompt).strip()]
    process["training_folder"] = str(_host_path(run_dir / "output"))
    # Exports written before a host root was configured (or by an older build)
    # carry container paths; ai-toolkit runs on the host and needs host paths.
    for entry in process.get("datasets") or []:
        folder = entry.get("folder_path") if isinstance(entry, dict) else None
        if isinstance(folder, str) and folder:
            entry["folder_path"] = str(_host_path(Path(folder)))
    config["config"]["name"] = name
    preset_id = str(params.get("base_model") or settings.training.default_base_model)
    preset = get_preset(preset_id)
    if preset is None:
        raise ValueError(f"Unknown training preset: {preset_id}")
    apply_preset(config, preset)
    return config, name


def create_run(
    session: Session,
    dataset: TrainingDataset,
    export: DatasetExport,
    params: Mapping[str, Any],
) -> TrainingRun:
    """Create a host-launchable ai-toolkit run and its REQUESTED marker."""
    if export.dataset_id != dataset.id:
        raise ValueError("Export does not belong to this dataset")
    if export.status != DatasetExportStatus.COMPLETED:
        raise ValueError("Export is not completed")
    if not export.output_dir:
        raise ValueError("Export has no output directory")

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    runs_dir = Path(export.output_dir) / "runs"
    run_dir = runs_dir / stamp
    suffix = 2
    while run_dir.exists():
        run_dir = runs_dir / f"{stamp}-{suffix}"
        suffix += 1
    run_dir.mkdir(parents=True, exist_ok=False)

    config, _ = _run_config(dataset, export, run_dir, params, stamp)
    config_yaml = yaml.safe_dump(config, sort_keys=False)
    (run_dir / "config.yaml").write_text(config_yaml, encoding="utf-8")

    run = TrainingRun(
        dataset_id=dataset.id,
        export_id=export.id,
        base_model=str(params.get("base_model") or settings.training.default_base_model),
        run_dir=str(run_dir),
        config_yaml=config_yaml,
        steps=int(params.get("steps", 2000)),
        total_steps=int(params.get("steps", 2000)),
    )
    session.add(run)
    session.flush()
    requested = {
        "run_id": run.id,
        "created_at": run.created_at.isoformat(),
    }
    (run_dir / "REQUESTED").write_text(
        json.dumps(requested, indent=2) + "\n", encoding="utf-8"
    )
    session.commit()
    session.refresh(run)
    return run


def read_run_status(run: TrainingRun) -> dict[str, Any]:
    status_path = Path(run.run_dir) / "status.json"
    try:
        value = json.loads(status_path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def run_checkpoints(run: TrainingRun) -> list[str]:
    status = read_run_status(run)
    reported = status.get("checkpoints")
    if isinstance(reported, list):
        return [str(path) for path in reported]
    output = Path(run.run_dir) / "output"
    if not output.is_dir():
        return []
    return [str(path) for path in sorted(output.rglob("*.safetensors"))]


def _sample_step(path: Path, fallback: int) -> int:
    match = re.search(r"_(\d+)(?:_|$)", path.stem)
    if match is None:
        match = re.search(r"(\d+)$", path.stem)
    return int(match.group(1)) if match else fallback


def _register_samples(session: Session, run: TrainingRun) -> None:
    samples_root = Path(run.run_dir) / "output"
    if not samples_root.is_dir():
        return
    paths = sorted(
        path
        for path in samples_root.rglob("*")
        if path.is_file()
        and path.suffix.lower() in _SAMPLE_SUFFIXES
        and "samples" in path.parts
        # ai-toolkit keeps 300 px previews in samples/.thumbs and scratch files
        # in samples/.tmp; only the full-size renders are samples.
        and not any(part.startswith(".") for part in path.relative_to(samples_root).parts)
    )
    stale = [
        sample
        for sample in session.exec(
            select(TrainingSample).where(TrainingSample.run_id == run.id)
        ).all()
        if any(part.startswith(".") for part in Path(sample.path).parts)
    ]
    for sample in stale:
        session.delete(sample)
    if stale:
        session.flush()
    existing = set(
        session.exec(
            select(TrainingSample.path).where(TrainingSample.run_id == run.id)
        ).all()
    )
    for index, path in enumerate(paths, start=1):
        value = str(path)
        if value in existing:
            continue
        step = _sample_step(path, index)
        session.add(TrainingSample(run_id=run.id, step=step, path=value))
        run.last_sample_step = max(run.last_sample_step, step)


def reconcile_runs(session: Session) -> list[TrainingRun]:
    """Mirror host-owned run files into all non-terminal database rows."""
    runs = list(
        session.exec(
            select(TrainingRun).where(
                TrainingRun.status.notin_(TERMINAL_RUN_STATUSES)
            )
        ).all()
    )
    now = datetime.now()
    for run in runs:
        run_dir = Path(run.run_dir)
        status_path = run_dir / "status.json"
        status = read_run_status(run)
        cancel_path = run_dir / "CANCEL"

        status_value = status.get("status")
        try:
            parsed_status = TrainingRunStatus(status_value) if status_value else None
        except ValueError:
            parsed_status = None
        if parsed_status is not None:
            run.status = parsed_status
        if (run_dir / "DONE").exists() and parsed_status != TrainingRunStatus.CANCELLED:
            run.status = TrainingRunStatus.COMPLETED
        elif (run_dir / "FAILED").exists() and parsed_status != TrainingRunStatus.CANCELLED:
            run.status = TrainingRunStatus.FAILED

        run.current_step = max(0, int(status.get("step") or run.current_step))
        run.total_steps = max(0, int(status.get("total") or run.total_steps or run.steps))
        try:
            run.last_loss = float(status["loss"]) if status.get("loss") is not None else run.last_loss
        except (TypeError, ValueError):
            pass
        run.status_updated_at = _parse_datetime(status.get("updated_at")) or run.status_updated_at
        if status.get("error"):
            run.error = str(status["error"])
        if run.status == TrainingRunStatus.RUNNING and run.started_at is None:
            run.started_at = run.status_updated_at or now

        if cancel_path.exists() and run.status not in TERMINAL_RUN_STATUSES:
            if not status_path.exists() or status_path.stat().st_mtime <= cancel_path.stat().st_mtime:
                run.status = TrainingRunStatus.CANCELLED

        _register_samples(session, run)
        if run.status in TERMINAL_RUN_STATUSES and run.finished_at is None:
            run.finished_at = run.status_updated_at or now
        session.add(run)
    session.commit()
    for run in runs:
        session.refresh(run)
    return runs


def cancel_run(session: Session, run: TrainingRun) -> TrainingRun:
    if run.status in TERMINAL_RUN_STATUSES:
        return run
    cancel_path = Path(run.run_dir) / "CANCEL"
    cancel_path.write_text(datetime.now().isoformat() + "\n", encoding="utf-8")
    return run
