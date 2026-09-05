"""Deterministic CPU inference support for Omoide's pinned WD tagger.

This module intentionally has no ComfyUI dependency so its data and image
contracts can be tested in Omoide without importing a ComfyUI checkout.
"""

from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol, Sequence

import numpy as np
from PIL import Image


SCHEMA_ID = "omoide.annotation/v1"
PROFILE_ID = "omoide-tags-v1"
MODEL_REPO_ID = "SmilingWolf/wd-eva02-large-tagger-v3"
MODEL_REVISION = "c5303bb7139430db980e4c680a778fe79d72b541"
MODEL_FILENAME = "model.onnx"
LABEL_FILENAME = "selected_tags.csv"
MODEL_DIRECTORY_NAME = "wd-eva02-large-tagger-v3-v1.0"
MODEL_SHA256 = "9e768793060c7939b277ccb382783e8670e8a042d29d77aa736be0c8cc898bfc"
LABEL_SHA256 = "298633d94d0031d2081c0893f29c82eab7f0df00b08483ba8f29d1e979441217"

# The model emits float32 scores, so the policy thresholds are converted once
# to their exact float32 values and that effective representation is emitted in
# provenance. Omoide pins the same values for its authoritative projection.
GENERAL_THRESHOLD = float(np.float32(0.5296))
CHARACTER_THRESHOLD = float(np.float32(0.85))

CATEGORY_GENERAL = 0
CATEGORY_CHARACTER = 4
CATEGORY_RATING = 9
CATEGORY_NAMES = {
    CATEGORY_GENERAL: "general",
    CATEGORY_CHARACTER: "character",
    CATEGORY_RATING: "rating",
}


@dataclass(frozen=True, slots=True)
class TagLabel:
    output_index: int
    tag_id: int
    name: str
    category: int

    @property
    def namespace(self) -> str:
        return CATEGORY_NAMES[self.category]


@dataclass(frozen=True, slots=True)
class ModelBundle:
    session: "InferenceSession"
    labels: tuple[TagLabel, ...]
    input_name: str
    output_name: str
    target_size: int


class SessionValue(Protocol):
    name: str
    shape: Sequence[object]


class InferenceSession(Protocol):
    def get_inputs(self) -> Sequence[SessionValue]: ...

    def get_outputs(self) -> Sequence[SessionValue]: ...

    def run(
        self,
        output_names: Sequence[str],
        input_feed: dict[str, np.ndarray],
    ) -> Sequence[np.ndarray]: ...


def load_labels(path: str | Path) -> tuple[TagLabel, ...]:
    """Load and validate the model's output vocabulary in source order."""

    labels: list[TagLabel] = []
    with Path(path).open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        required = {"tag_id", "name", "category"}
        if not required.issubset(reader.fieldnames or []):
            raise ValueError("selected_tags.csv is missing required columns")

        for output_index, row in enumerate(reader):
            category = int(row["category"])
            if category not in CATEGORY_NAMES:
                raise ValueError(
                    f"unsupported category {category} at model output {output_index}"
                )
            labels.append(
                TagLabel(
                    output_index=output_index,
                    tag_id=int(row["tag_id"]),
                    name=row["name"],
                    category=category,
                )
            )

    if not labels:
        raise ValueError("selected_tags.csv contains no labels")
    return tuple(labels)


def verify_artifact(path: str | Path, expected_sha256: str) -> None:
    """Fail closed when a cached artifact is not the reviewed exact file."""

    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != expected_sha256:
        raise ValueError(f"artifact checksum mismatch: {Path(path).name}")


def _as_uint8_image(image: np.ndarray) -> Image.Image:
    if image.ndim != 3 or image.shape[2] not in (1, 3, 4):
        raise ValueError("image must have shape HxWx1, HxWx3, or HxWx4")

    if np.issubdtype(image.dtype, np.floating):
        if not np.isfinite(image).all():
            raise ValueError("image contains non-finite values")
        pixels = np.rint(np.clip(image, 0.0, 1.0) * 255.0).astype(np.uint8)
    else:
        pixels = np.clip(image, 0, 255).astype(np.uint8)

    if pixels.shape[2] == 1:
        pixels = np.repeat(pixels, 3, axis=2)

    mode = "RGBA" if pixels.shape[2] == 4 else "RGB"
    return Image.fromarray(pixels, mode=mode)


def prepare_image(image: np.ndarray, target_size: int) -> np.ndarray:
    """Apply the official WD ONNX preprocessing and return NHWC BGR float32."""

    if target_size <= 0:
        raise ValueError("target_size must be positive")

    pil_image = _as_uint8_image(image)
    if pil_image.mode == "RGBA":
        canvas = Image.new("RGBA", pil_image.size, (255, 255, 255, 255))
        canvas.alpha_composite(pil_image)
        pil_image = canvas.convert("RGB")
    else:
        pil_image = pil_image.convert("RGB")

    width, height = pil_image.size
    square_size = max(width, height)
    square = Image.new("RGB", (square_size, square_size), (255, 255, 255))
    square.paste(pil_image, ((square_size - width) // 2, (square_size - height) // 2))
    if square_size != target_size:
        square = square.resize((target_size, target_size), Image.Resampling.BICUBIC)

    rgb = np.asarray(square, dtype=np.float32)
    return np.ascontiguousarray(rgb[:, :, ::-1])


def prepare_batch(images: np.ndarray, target_size: int) -> np.ndarray:
    """Prepare a ComfyUI BHWC image tensor represented as a NumPy array."""

    if images.ndim == 3:
        images = images[np.newaxis, ...]
    if images.ndim != 4 or images.shape[0] == 0:
        raise ValueError("images must have shape BxHxWxC with a non-empty batch")
    return np.stack(
        [prepare_image(image, target_size) for image in images],
        axis=0,
    ).astype(np.float32, copy=False)


def _score_entry(label: TagLabel, score: float) -> dict[str, Any]:
    return {
        "name": label.name,
        "score": float(score),
    }


def _score_order(entry: dict[str, Any]) -> tuple[float, str]:
    return (-entry["score"], entry["name"])


def build_documents(
    probabilities: np.ndarray,
    labels: Sequence[TagLabel],
) -> list[dict[str, Any]]:
    """Create one exact, stable annotation document per batch item."""

    scores = np.asarray(probabilities, dtype=np.float32)
    if scores.ndim == 1:
        scores = scores[np.newaxis, ...]
    if scores.ndim != 2 or scores.shape[1] != len(labels):
        raise ValueError(
            f"model returned {scores.shape!r}; expected Bx{len(labels)} probabilities"
        )
    if not np.isfinite(scores).all():
        raise ValueError("model returned non-finite probabilities")
    if np.any(scores < 0.0) or np.any(scores > 1.0):
        raise ValueError("model returned values outside the probability range")

    documents: list[dict[str, Any]] = []
    for row in scores:
        observations: dict[str, list[dict[str, Any]]] = {
            "rating": [],
            "general": [],
            "character": [],
        }
        for label, score in zip(labels, row, strict=True):
            observations[label.namespace].append(_score_entry(label, float(score)))

        for namespace in observations:
            observations[namespace].sort(key=_score_order)

        documents.append(
            {
                "schema": SCHEMA_ID,
                "kind": "tags",
                "profile_id": PROFILE_ID,
                "model": {
                    "artifacts": [MODEL_FILENAME, LABEL_FILENAME],
                    "character_threshold": CHARACTER_THRESHOLD,
                    "general_threshold": GENERAL_THRESHOLD,
                    "preprocessing": {
                        "alpha_background": "white",
                        "channel_order": "BGR",
                        "layout": "NHWC",
                        "resize": "bicubic",
                        "square_padding": "centered-white",
                        "value_range": "0..255-float32",
                    },
                    "repo_id": MODEL_REPO_ID,
                    "revision": MODEL_REVISION,
                    "runtime": "onnxruntime-cpu",
                    "threshold_comparison": "strictly-greater-than",
                },
                "tags": observations,
            }
        )
    return documents


def canonical_json(document: dict[str, Any]) -> str:
    return json.dumps(
        document,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def preview_text(documents: Sequence[dict[str, Any]]) -> str:
    lines = [
        "Omoide WD EVA02-Large Tagger v3",
        f"Pinned revision: {MODEL_REVISION}",
        (
            "Character candidates are retained in JSON for review; "
            "they are not exported as training tags."
        ),
    ]
    for batch_index, document in enumerate(documents):
        tags = document["tags"]
        rating = tags["rating"][0] if tags["rating"] else None
        rating_text = (
            f"{rating['name']} ({rating['score']:.4f})" if rating else "unavailable"
        )
        selected_general = [
            entry["name"]
            for entry in tags["general"]
            if entry["score"] > GENERAL_THRESHOLD
        ]
        character_candidates = [
            entry["name"]
            for entry in tags["character"]
            if entry["score"] > CHARACTER_THRESHOLD
        ]
        shown = (
            ", ".join(selected_general[:40])
            if selected_general
            else "none above threshold"
        )
        suffix = (
            f" (+{len(selected_general) - 40} more)"
            if len(selected_general) > 40
            else ""
        )
        lines.append(
            f"Batch {batch_index}: rating={rating_text}; "
            f"general tags={shown}{suffix}; "
            f"character candidates={len(character_candidates)}"
        )
    return "\n".join(lines)


def run_session(
    session: InferenceSession,
    input_name: str,
    output_name: str,
    images: np.ndarray,
) -> np.ndarray:
    outputs = session.run([output_name], {input_name: images})
    if len(outputs) != 1:
        raise ValueError(f"expected one model output, received {len(outputs)}")
    return np.asarray(outputs[0], dtype=np.float32)


@lru_cache(maxsize=1)
def load_model_bundle(model_directory: str | Path) -> ModelBundle:
    """Load pre-staged, checksum-pinned artifacts into one CPU session."""

    import onnxruntime as ort

    root = Path(model_directory)
    model_path = root / MODEL_FILENAME
    label_path = root / LABEL_FILENAME
    if not model_path.is_file() or not label_path.is_file():
        raise FileNotFoundError(
            "Omoide WD artifacts are not staged; run the tracked model installer"
        )
    verify_artifact(model_path, MODEL_SHA256)
    verify_artifact(label_path, LABEL_SHA256)
    labels = load_labels(label_path)

    session = ort.InferenceSession(
        model_path,
        providers=["CPUExecutionProvider"],
    )
    inputs = session.get_inputs()
    outputs = session.get_outputs()
    if len(inputs) != 1 or len(outputs) != 1:
        raise ValueError("pinned WD model must expose exactly one input and one output")

    shape = inputs[0].shape
    if (
        len(shape) != 4
        or not isinstance(shape[1], int)
        or not isinstance(shape[2], int)
    ):
        raise ValueError(f"unexpected WD model input shape: {shape!r}")
    if shape[1] != shape[2]:
        raise ValueError(f"WD model input must be square, got {shape!r}")
    output_shape = outputs[0].shape
    if (
        len(output_shape) != 2
        or not isinstance(output_shape[1], int)
        or output_shape[1] != len(labels)
    ):
        raise ValueError(
            "WD model output width does not match selected_tags.csv: "
            f"{output_shape!r} vs {len(labels)} labels"
        )

    return ModelBundle(
        session=session,
        labels=labels,
        input_name=inputs[0].name,
        output_name=outputs[0].name,
        target_size=shape[1],
    )


def annotate_numpy_batch(
    images: np.ndarray,
    model_directory: str | Path,
) -> tuple[list[str], str]:
    bundle = load_model_bundle(model_directory)
    prepared = prepare_batch(images, bundle.target_size)
    probabilities = run_session(
        bundle.session,
        bundle.input_name,
        bundle.output_name,
        prepared,
    )
    documents = build_documents(probabilities, bundle.labels)
    return [canonical_json(document) for document in documents], preview_text(documents)
