from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from app.config import settings


@dataclass(frozen=True)
class TrainingPreset:
    id: str
    label: str
    description: str
    requires_hf_token: bool
    local_file_setting: str | None
    hub_id: str
    model_block: dict[str, Any]
    train_overrides: dict[str, Any]
    sample_overrides: dict[str, Any]


PRESETS: Mapping[str, TrainingPreset] = {
    "flux-dev": TrainingPreset(
        id="flux-dev",
        label="FLUX.1 Dev",
        description="Full FLUX.1-dev base model for high-quality LoRA training.",
        requires_hf_token=True,
        local_file_setting=None,
        hub_id="black-forest-labs/FLUX.1-dev",
        model_block={
            "name_or_path": "black-forest-labs/FLUX.1-dev",
            "is_flux": True,
            "quantize": True,
        },
        train_overrides={},
        sample_overrides={"guidance_scale": 4, "sample_steps": 20},
    ),
    "z-image": TrainingPreset(
        id="z-image",
        label="Z-Image",
        description="Ungated Z-Image base model for standard LoRA training.",
        requires_hf_token=False,
        local_file_setting="z_image_path",
        hub_id="Tongyi-MAI/Z-Image",
        model_block={
            "arch": "zimage",
            "name_or_path": "Tongyi-MAI/Z-Image",
            "extras_name_or_path": "Tongyi-MAI/Z-Image",
            "quantize": False,
            "quantize_te": False,
            "low_vram": False,
        },
        train_overrides={"timestep_type": "weighted"},
        sample_overrides={"guidance_scale": 4, "sample_steps": 30},
    ),
    "z-image-turbo": TrainingPreset(
        id="z-image-turbo",
        label="Z-Image Turbo",
        description="Ungated accelerated Z-Image preset with the training adapter.",
        requires_hf_token=False,
        local_file_setting="z_image_turbo_path",
        hub_id="Tongyi-MAI/Z-Image-Turbo",
        model_block={
            "arch": "zimage",
            "name_or_path": "Tongyi-MAI/Z-Image-Turbo",
            "extras_name_or_path": "Tongyi-MAI/Z-Image-Turbo",
            "assistant_lora_path": (
                "ostris/zimage_turbo_training_adapter/"
                "zimage_turbo_training_adapter_v2.safetensors"
            ),
            "quantize": False,
            "quantize_te": False,
            "low_vram": False,
        },
        train_overrides={"timestep_type": "weighted"},
        sample_overrides={"guidance_scale": 1, "sample_steps": 9},
    ),
}


def get_preset(preset_id: str) -> TrainingPreset | None:
    return PRESETS.get(preset_id)


def default_preset_id() -> str:
    return settings.training.default_base_model


def apply_preset(config: dict, preset: TrainingPreset) -> None:
    process = config["config"]["process"][0]
    local_path = (
        getattr(settings.training, preset.local_file_setting)
        if preset.local_file_setting
        else None
    )
    process["model"] = {
        **preset.model_block,
        "name_or_path": local_path or preset.hub_id,
    }
    process["train"].update(preset.train_overrides)
    process["sample"].update(preset.sample_overrides)


def read_launcher_heartbeat() -> dict | None:
    path = (
        settings.general.resolved_datasets_dir()
        / ".launcher"
        / "heartbeat.json"
    )
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def launcher_health() -> dict:
    heartbeat = read_launcher_heartbeat()
    stale_after = settings.training.launcher_stale_after_seconds
    seen_at = heartbeat.get("seen_at") if heartbeat else None
    parsed_seen_at = None
    if isinstance(seen_at, str):
        try:
            parsed_seen_at = datetime.fromisoformat(seen_at.replace("Z", "+00:00"))
            if parsed_seen_at.tzinfo is None:
                parsed_seen_at = parsed_seen_at.replace(tzinfo=timezone.utc)
        except ValueError:
            seen_at = None
    else:
        seen_at = None
    launcher_ok = bool(
        parsed_seen_at
        and (datetime.now(timezone.utc) - parsed_seen_at).total_seconds()
        <= stale_after
    )
    token_configured = heartbeat.get("hf_token_configured") if heartbeat else None
    if not isinstance(token_configured, bool):
        token_configured = None
    return {
        "launcher_seen_at": seen_at,
        "launcher_ok": launcher_ok,
        "hf_token_configured": token_configured,
        "stale_after_seconds": stale_after,
    }
