"""Curated background instructions for identity-safe image repairs."""

from __future__ import annotations

from functools import cache
from pathlib import Path

import yaml

_PROMPTS_PATH = Path(__file__).resolve().parents[1] / "templates" / "background_prompts.yaml"


@cache
def load_prompts() -> tuple[str, ...]:
    """Load and validate the bundled background prompt library once."""
    loaded = yaml.safe_load(_PROMPTS_PATH.read_text(encoding="utf-8"))
    if not isinstance(loaded, list):
        raise ValueError("Background prompts template must be a list")
    prompts = tuple(prompt.strip() for prompt in loaded if isinstance(prompt, str) and prompt.strip())
    if len(prompts) < 25 or len(prompts) != len(loaded):
        raise ValueError("Background prompts template must contain at least 25 non-empty strings")
    if any(len(prompt) > 2000 for prompt in prompts):
        raise ValueError("Background prompts must not exceed 2000 characters")
    return prompts
