"""Sequential post-training checkpoint evaluation."""

from __future__ import annotations

import io
from datetime import datetime
from pathlib import Path
from uuid import UUID, uuid4

import yaml
from PIL import Image
from sqlmodel import Session

import app.database as db
from app.config import settings
from app.logger import logger
from app.models import EvalBatch, EvalSample, Status, TrainingDataset, TrainingRun, TrainingRunStatus
from app.services.comfy_annotation import ComfyAnnotationError
from app.services.comfy_eval import ComfyEvalClient
from app.services.likeness import score_path


def eval_client() -> ComfyEvalClient:
    return ComfyEvalClient(settings.repairs.inference_socket_path, settings.repairs.timeout_seconds)


def _sample_settings(run: TrainingRun) -> dict:
    try:
        config = yaml.safe_load(run.config_yaml)
        sample = config["config"]["process"][0]["sample"]
        return sample if isinstance(sample, dict) else {}
    except (KeyError, IndexError, TypeError, yaml.YAMLError):
        return {}


def _mark_failed(batch_id: int, message: str) -> None:
    with Session(db.engine) as session:
        batch = session.get(EvalBatch, batch_id)
        if batch is None or batch.status == Status.CANCELLED:
            return
        batch.status = Status.FAILED
        batch.error = message[:2000]
        batch.finished_at = datetime.now()
        session.add(batch)
        session.commit()


def run_eval_batch(batch_id: int) -> None:
    """Generate and score each prompt/seed cell, preserving partial results."""
    with Session(db.engine) as session:
        batch = session.get(EvalBatch, batch_id)
        if batch is None or batch.status == Status.CANCELLED:
            return
        run = session.get(TrainingRun, batch.run_id)
        if run is None or run.status != TrainingRunStatus.COMPLETED:
            _mark_failed(batch_id, "Training run must be completed before evaluation")
            return
        if not batch.lora_path or not Path(batch.checkpoint_path).suffix.lower() == ".safetensors":
            _mark_failed(batch_id, "Evaluation batch has no checkpoint")
            return
        dataset = session.get(TrainingDataset, run.dataset_id)
        if dataset is None:
            _mark_failed(batch_id, "Training dataset does not exist")
            return
        dataset_id = int(dataset.id)
        batch.status = Status.RUNNING
        batch.error = None
        session.add(batch)
        session.commit()
        prompts = list(batch.prompts)
        seeds = list(batch.seeds)
        strength = float(batch.lora_strength)
        lora_path = batch.lora_path
        run_dir = Path(run.run_dir)
        sample_settings = _sample_settings(run)

    output_dir = run_dir / "eval" / str(batch_id)
    output_dir.mkdir(parents=True, exist_ok=True)
    failures: list[str] = []
    client = eval_client()
    for prompt_index, prompt in enumerate(prompts):
        for seed in seeds:
            with Session(db.engine) as session:
                current = session.get(EvalBatch, batch_id)
                if current is None or current.status == Status.CANCELLED:
                    return
            attempt_id = uuid4()
            target = output_dir / f"{prompt_index}_{seed}.png"
            error: str | None = None
            likeness: float | None = None
            face_count: int | None = None
            scored_at: datetime | None = None
            try:
                params = {
                    "prompt": prompt,
                    "negative": str(sample_settings.get("negative_prompt") or ""),
                    "seed": int(seed),
                    "steps": int(sample_settings.get("sample_steps") or 30),
                    "cfg": float(sample_settings.get("guidance_scale") or 4.0),
                    "width": int(sample_settings.get("width") or 1024),
                    "height": int(sample_settings.get("height") or 1024),
                    "lora_path": lora_path,
                    "lora_strength": strength,
                }
                result = client.generate(attempt_id, settings.repairs.eval_profile_id, params)
                with Image.open(io.BytesIO(result.image)) as image:
                    image.load()
                    image.save(target, format="PNG")
                with Session(db.engine) as session:
                    current_dataset = session.get(TrainingDataset, dataset_id)
                    if current_dataset is None:
                        raise ValueError("Training dataset does not exist")
                    likeness, face_count, _ = score_path(session, current_dataset, target)
                scored_at = datetime.now()
                acknowledge = getattr(client, "ack_attempt", None)
                if callable(acknowledge):
                    try:
                        acknowledge(attempt_id=UUID(str(attempt_id)))
                    except ComfyAnnotationError as exc:
                        logger.warning("Evaluation attempt %s history acknowledgement failed: %s", attempt_id, exc.message)
            except Exception as exc:  # noqa: BLE001 - one cell must not abort the batch
                error = str(exc)[:2000]
                failures.append(f"prompt {prompt_index}, seed {seed}: {error}")
                logger.warning("Evaluation batch %s cell failed: %s", batch_id, error)
            with Session(db.engine) as session:
                session.add(EvalSample(
                    batch_id=batch_id,
                    prompt_index=prompt_index,
                    seed=seed,
                    path=str(target),
                    attempt_id=str(attempt_id),
                    likeness=likeness,
                    face_count=face_count,
                    scored_at=scored_at,
                    error=error,
                ))
                session.commit()

    with Session(db.engine) as session:
        batch = session.get(EvalBatch, batch_id)
        if batch is None or batch.status == Status.CANCELLED:
            return
        batch.status = Status.FAILED if failures else Status.COMPLETED
        batch.error = "; ".join(failures)[:2000] if failures else None
        batch.finished_at = datetime.now()
        session.add(batch)
        session.commit()
