"""ComfyUI nodes for Omoide LoRA evaluation workflows."""

from __future__ import annotations

import json
import os
from pathlib import Path

import comfy.sd
import comfy.utils

# The only directory a LoRA may be loaded from. The Omoide bridge enforces the
# same bound before a prompt is queued; this is defence in depth inside Comfy.
DEFAULT_LORA_ROOT = "/home/elpresidank/.local/share/omoide-portal/datasets"
LORA_ROOT = Path(os.environ.get("OMOIDE_EVAL_LORA_ROOT", DEFAULT_LORA_ROOT)).resolve()

_MAX_PROMPT_CHARS = 2000


def _payload(params_json: str) -> dict:
    try:
        payload = json.loads(params_json or "{}")
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _int(payload: dict, key: str, default: int, low: int, high: int, step: int = 1) -> int:
    try:
        value = int(payload.get(key, default))
    except (TypeError, ValueError):
        value = default
    value = max(low, min(high, value))
    if step > 1:
        value -= value % step
    return max(low, value)


def _float(payload: dict, key: str, default: float, low: float, high: float) -> float:
    try:
        value = float(payload.get(key, default))
    except (TypeError, ValueError):
        value = default
    if value != value:  # NaN
        value = default
    return max(low, min(high, value))


def _text(payload: dict, key: str, default: str = "") -> str:
    value = payload.get(key, default)
    if not isinstance(value, str):
        return default
    return value[:_MAX_PROMPT_CHARS]


class OmoideEvalParams:
    """Fan a bounded JSON parameters string out into sampler inputs."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "params_json": ("STRING", {"default": "{}", "multiline": True}),
            }
        }

    RETURN_TYPES = ("STRING", "STRING", "INT", "INT", "FLOAT", "INT", "INT", "STRING")
    RETURN_NAMES = ("prompt", "negative", "seed", "steps", "cfg", "width", "height", "params_json")
    FUNCTION = "expand"
    CATEGORY = "Omoide/eval"

    def expand(self, params_json: str):
        payload = _payload(params_json)
        return (
            _text(payload, "prompt"),
            _text(payload, "negative"),
            _int(payload, "seed", 1, 0, 2**53 - 1),
            _int(payload, "steps", 28, 1, 60),
            _float(payload, "cfg", 4.0, 0.0, 15.0),
            _int(payload, "width", 1024, 512, 1536, step=16),
            _int(payload, "height", 1024, 512, 1536, step=16),
            params_json or "{}",
        )


class OmoideEvalLoraLoader:
    """Apply the LoRA named in the params JSON, only from the allowed root.

    ``lora_path`` must be an absolute ``.safetensors`` file under
    ``OMOIDE_EVAL_LORA_ROOT``; anything else leaves the model untouched and
    reports ``applied = 0`` so the evaluation is never silently run against a
    LoRA from an unexpected location. ``lora_strength`` defaults to 1.0.
    """

    def __init__(self) -> None:
        self._cache: tuple[str, object, object] | None = None

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "model": ("MODEL",),
                "params_json": ("STRING", {"default": "{}", "multiline": True}),
            }
        }

    RETURN_TYPES = ("MODEL", "INT", "STRING")
    RETURN_NAMES = ("model", "applied", "lora_path")
    FUNCTION = "apply"
    CATEGORY = "Omoide/eval"

    @staticmethod
    def _allowed_path(value: object) -> Path | None:
        if not isinstance(value, str) or not value:
            return None
        candidate = Path(value)
        if not candidate.is_absolute() or candidate.suffix.lower() != ".safetensors":
            return None
        try:
            resolved = candidate.resolve(strict=True)
        except (OSError, RuntimeError):
            return None
        try:
            resolved.relative_to(LORA_ROOT)
        except ValueError:
            return None
        return resolved if resolved.is_file() else None

    def apply(self, model, params_json: str):
        payload = _payload(params_json)
        path = self._allowed_path(payload.get("lora_path"))
        strength = _float(payload, "lora_strength", 1.0, 0.0, 2.0)
        if path is None or strength == 0.0:
            return (model, 0, "")
        key = os.fspath(path)
        if self._cache is not None and self._cache[0] == key:
            lora, metadata = self._cache[1], self._cache[2]
        else:
            lora, metadata = comfy.utils.load_torch_file(key, safe_load=True, return_metadata=True)
            self._cache = (key, lora, metadata)
        patched, _ = comfy.sd.load_lora_for_models(
            model, None, lora, strength, 0.0, lora_metadata=metadata
        )
        return (patched, 1, key)


NODE_CLASS_MAPPINGS = {
    "OmoideEvalParams": OmoideEvalParams,
    "OmoideEvalLoraLoader": OmoideEvalLoraLoader,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "OmoideEvalParams": "Omoide Eval Params",
    "OmoideEvalLoraLoader": "Omoide Eval LoRA Loader (path-bound)",
}
