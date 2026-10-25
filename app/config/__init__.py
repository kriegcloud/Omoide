import gc
import math
import os
import shutil
import sys
import threading
from enum import Enum
from pathlib import Path
from typing import Annotated, Any, TypeVar

import yaml
from fastapi import HTTPException, Request
from pydantic import (
    BaseModel,
    Field,
    PlainSerializer,
    computed_field,
    field_validator,
)

from app.logger import configure_file_logging, logger

E = TypeVar("E", bound=Enum)
IS_DOCKER = os.getenv("IS_DOCKER", False)
ENV_PREFIX = "OMOIDE_"


from .profiles import *
from .schema import *
from .schema import _resolve_path_safe, _is_within

def load_config_from_file() -> dict:
    """Loads settings from the user's YAML file."""
    config_path = get_user_data_path() / "config.yaml"
    if not config_path.exists():
        base_settings = AppSettings()
        seeded_data = base_settings.model_dump(mode="json")
        _apply_env_overrides(seeded_data)
        seeded_settings = AppSettings.model_validate(seeded_data)
        save_settings(seeded_settings)
    with open(config_path, "r") as f:
        return yaml.safe_load(f) or {}


def _coerce_env_value(value: str, current_value: Any = None) -> Any:
    """
    Convert raw env strings into reasonable Python types.

    Uses the existing config value to disambiguate list overrides so that
    blank list values (e.g., CUSTOM_TAGS=) become [] instead of "".
    """
    trimmed = value.strip()

    # Keep list overrides usable (comma-separated or explicit empty)
    if isinstance(current_value, list) or "," in trimmed:
        if trimmed == "":
            return []
        return [v.strip() for v in trimmed.split(",") if v.strip()]

    lowered = trimmed.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    if lowered == "null":
        return None
    try:
        if "_" not in trimmed:
            return int(trimmed)
    except ValueError:
        pass
    try:
        return float(trimmed)
    except ValueError:
        pass
    return trimmed


def _matching_config_key(target: dict, segment: str) -> str:
    """Preserve an existing config key's casing when applying an env path."""
    segment_folded = segment.casefold()
    for existing_key in target:
        if (
            isinstance(existing_key, str)
            and existing_key.casefold() == segment_folded
        ):
            return existing_key
    return segment.lower()


def _overlay_config(target: dict, overrides: dict) -> None:
    """Recursively overlay persisted values onto a default config tree."""
    for key, value in overrides.items():
        config_key = _matching_config_key(target, key)
        current = target.get(config_key)
        if isinstance(current, dict) and isinstance(value, dict):
            _overlay_config(current, value)
        else:
            target[config_key] = value


def _apply_env_overrides(config_data: dict) -> None:
    for key, raw_value in os.environ.items():
        if not key.startswith(ENV_PREFIX):
            continue
        path_segments = key[len(ENV_PREFIX) :].split("__")
        if not path_segments:
            continue
        target = config_data
        for segment in path_segments[:-1]:
            config_key = _matching_config_key(target, segment)
            current = target.get(config_key)
            if not isinstance(current, dict):
                current = {}
                target[config_key] = current
            target = current
        final_key = _matching_config_key(target, path_segments[-1])
        current_value = target.get(final_key)
        target[final_key] = _coerce_env_value(raw_value, current_value)


def load_settings() -> AppSettings:
    """
    Loads settings with a clear priority:
    1. Environment variables (for Docker)
    2. User's config.yaml file (for desktop app)
    3. Pydantic model defaults (hardcoded fallback)
    """
    # Build from lowest to highest priority. Seed every schema field so
    # environment paths preserve canonical key casing even when config.yaml is
    # empty or only contains part of a section.
    config_data = AppSettings().model_dump(mode="json")

    # Overlay the user's persisted settings without replacing whole sections.
    file_config = load_config_from_file()
    if file_config:
        _overlay_config(config_data, file_config)

    # Runtime environment overrides have the highest priority.
    _apply_env_overrides(config_data)
    settings_model = AppSettings.model_validate(config_data)
    # Runtime-only flags should not be overridden by persisted config.
    settings_model.general.is_binary = bool(getattr(sys, "frozen", False))
    settings_model.general.is_docker = bool(os.environ.get("IS_DOCKER", False))
    return settings_model


def _sanitize_for_save(settings_model: AppSettings) -> dict:
    """Return a dict for config.yaml that excludes derived fields and enforces
    data_dir from the active bootstrap profile.

    We avoid persisting computed paths (database_dir, omoide_dir, thumb_dir,
    models_dir, static_dir, database_url) and the mutable data_dir itself.
    On load, data_dir always derives from the active profile in bootstrap.
    """
    data = settings_model.model_dump(mode="json")
    general = data.get("general", {})
    # Force data_dir to the active profile path (or omit entirely)
    # Omitting keeps YAML clean; runtime will compute from bootstrap.
    for k in [
        "data_dir",
        "database_dir",
        "omoide_dir",
        "thumb_dir",
        "models_dir",
        "static_dir",
        "database_url",
        "is_binary",
        "is_docker",
    ]:
        general.pop(k, None)
    data["general"] = general
    return data


def save_settings(settings_model: AppSettings):
    """Saves the provided settings model to the config.yaml file."""
    validate_clip_settings_change(settings_model)
    config_file = get_user_data_path() / "config.yaml"
    with open(config_file, "w") as f:
        yaml.dump(
            _sanitize_for_save(settings_model),
            f,
            sort_keys=False,
            indent=2,
        )


def get_model(settings: AppSettings):
    """Create a new OpenCLIP model bundle on the selected device.

    Note: This constructs a fresh model; callers should prefer the
    acquire_clip()/get_clip_bundle() helpers below to reuse a shared instance.
    """
    import open_clip

    from app.accelerators import resolve_torch_device

    device = resolve_torch_device(settings.processors.prefer_gpu)
    model, preprocess, _ = open_clip.create_model_and_transforms(
        settings.ai.clip_model.model_name,
        pretrained=settings.ai.clip_model_pretrained,
        device=device,
    )
    tokenizer = open_clip.get_tokenizer(settings.ai.clip_model.model_name)
    return model, preprocess, tokenizer


# ----- Lazy, shared CLIP bundle management -----
_clip_lock = threading.RLock()
_clip_model = None
_clip_preprocess = None
_clip_tokenizer = None
_clip_refs = 0
_clip_search_warm = False


def retain_clip_for_search(model):
    """Keep the shared tower warm once interactive search has used it."""
    global _clip_search_warm
    with _clip_lock:
        if model is _clip_model:
            _clip_search_warm = True


def get_clip_bundle():
    """Return a shared CLIP bundle, loading it lazily if needed.

    Does not change the reference count; use acquire_clip()/release_clip()
    when you want lifecycle management (e.g., processors and long tasks).
    """
    global _clip_model, _clip_preprocess, _clip_tokenizer
    with _clip_lock:
        if _clip_model is None:
            logger.info("Loading OpenCLIP bundle (lazy)...")
            _clip_model, _clip_preprocess, _clip_tokenizer = get_model(
                settings
            )
        return _clip_model, _clip_preprocess, _clip_tokenizer


def acquire_clip():
    """Acquire a reference to the shared CLIP bundle and return it.

    While at least one reference is held, the bundle remains loaded.
    """
    global _clip_refs
    with _clip_lock:
        model, preprocess, tokenizer = get_clip_bundle()
        _clip_refs += 1
        return model, preprocess, tokenizer


def release_clip():
    """Release a previously acquired CLIP bundle reference.

    When the refcount drops to zero, unload the bundle to free memory.
    """
    global _clip_refs, _clip_model, _clip_preprocess, _clip_tokenizer
    with _clip_lock:
        if _clip_refs > 0:
            _clip_refs -= 1
        if _clip_refs == 0 and not _clip_search_warm and _clip_model is not None:
            logger.info("Releasing OpenCLIP bundle from memory")
            try:
                _clip_model = None
                _clip_preprocess = None
                _clip_tokenizer = None
            finally:
                gc.collect()


def _reset_clip_after_settings_change():
    """Clear the shared CLIP bundle if nothing is using it.

    If references are held, defer reset until they are released.
    """
    global _clip_model, _clip_preprocess, _clip_tokenizer, _clip_search_warm
    with _clip_lock:
        _clip_search_warm = False
        if _clip_refs == 0:
            _clip_model = None
            _clip_preprocess = None
            _clip_tokenizer = None
        else:
            logger.info(
                "Deferring CLIP reload until current users release it (refs=%s)",
                _clip_refs,
            )


def validate_clip_settings_change(incoming, *, profile_switch=False):
    """Refuse incompatible vectors before saving or mutating runtime settings."""
    if "settings" not in globals():
        # Initial configuration seeding runs before the database module exists.
        return
    from app.database import validate_clip_vector_space

    # Save payloads cannot select their database: data_dir is ignored on save.
    same_profile = not profile_switch or incoming.general.database_url == settings.general.database_url
    validate_clip_vector_space(
        incoming.ai.clip_model,
        previous_model=settings.ai.clip_model if same_profile else None,
        database_url=None if same_profile else incoming.general.database_url,
    )


class SettingsActivationError(ValueError):
    """A candidate profile could not be made ready; prior runtime was retained."""


def _apply_runtime_settings(values):
    """Update existing section objects so imported settings references stay valid."""
    for section_name in type(values).model_fields:
        if not hasattr(settings, section_name):
            continue
        section = getattr(settings, section_name)
        assignable = set(type(section).model_fields)
        incoming_section = getattr(values, section_name)
        for key in incoming_section.model_dump():
            if key not in assignable:
                continue
            value = getattr(incoming_section, key)
            if section_name == "ai" and key == "clip_model":
                value = ClipModel(value)
            setattr(section, key, value)


def reload_settings(*, before_publish=None):
    """Activate ready settings, retaining the previous runtime on preparation failure."""
    from app import database as db

    logger.info("Reloading settings...")
    previous_settings = settings.model_copy(deep=True)
    previous_engine = db.engine
    previous_migration_state = db.get_migration_state()
    candidate_engine = None
    settings_changed = False
    preparing_database = False
    same_database = False
    try:
        new_settings = load_settings()
        # Ignore stale data_dir values copied into a profile configuration.
        new_settings.general.data_dir = get_user_data_path()
        validate_clip_settings_change(new_settings, profile_switch=True)
        for directory in (new_settings.general.database_dir, new_settings.general.omoide_dir,
                          new_settings.general.thumb_dir, new_settings.general.models_dir):
            directory.mkdir(parents=True, exist_ok=True)

        # Keep the old engine/pool usable until the candidate passes migrations
        # and vector preparation. reset_engine() would dispose it too early.
        candidate_engine = db._make_engine(new_settings.general.database_url)
        previous_file = previous_engine.url.database
        candidate_file = candidate_engine.url.database
        if previous_file not in (None, "", ":memory:") and candidate_file not in (None, "", ":memory:"):
            same_database = Path(previous_file).resolve() == Path(candidate_file).resolve()
        preparing_database = True
        if same_database:
            # Retaining an engine cannot isolate DDL on the same physical DB.
            db._set_migration_state(db.MigrationState.NOT_ATTEMPTED)
        db.run_migrations(
            database_url=new_settings.general.database_url,
            clip_embedding_size=new_settings.ai.clip_model_embedding_size,
            track_state=False,
        )
        db.ensure_vec_tables(target_engine=candidate_engine, clip_model=new_settings.ai.clip_model)
        preparing_database = False
        if before_publish is not None:
            before_publish()
        # Ordinary requests retain the old runtime throughout preparation.
        # Publish only an already migrated, vector-compatible candidate.
        settings_changed = True
        _apply_runtime_settings(new_settings)
        db.engine = candidate_engine
        db._set_migration_state(db.MigrationState.APPLIED)
    except Exception as exc:
        if settings_changed:
            _apply_runtime_settings(previous_settings)
            db.engine = previous_engine
        # A failed migration may have changed the same physical database.
        # Retaining its old engine does not prove that schema is still ready.
        db._set_migration_state(db.MigrationState.FAILED if same_database and preparing_database
                                else previous_migration_state)
        if candidate_engine is not None:
            try:
                candidate_engine.dispose()
            except Exception as cleanup_error:
                logger.warning("Could not retire rejected profile connection pool: %s", cleanup_error)
        if isinstance(exc, ValueError):
            # Preserve useful existing validation errors, such as vector-space
            # refusal, and the ValueError contract of direct reload callers.
            raise
        detail = ("Profile initialization failed; the active database requires schema recovery."
                  if same_database and preparing_database else
                  "Profile initialization failed; the previous profile remains active.")
        raise SettingsActivationError(detail) from exc

    try:
        previous_engine.dispose()
    except Exception as exc:
        logger.warning("Could not retire the previous profile connection pool: %s", exc)

    # Invalidate CLIP bundle to reflect possible AI model changes; will reload lazily
    _reset_clip_after_settings_change()
    try:
        from app.processor_registry import reset_processors

        reset_processors()
    except Exception as e:
        logger.warning("Could not reset processors after config reload: %s", e)
    try:
        configure_file_logging(settings.general.data_dir / "logs")
    except Exception as e:
        logger.warning("Could not reconfigure file logging: %s", e)
    try:
        logger.info(
            "Active profile: data_dir=%s omoide_dir=%s thumb_dir=%s db_dir=%s",
            settings.general.data_dir,
            settings.general.omoide_dir,
            settings.general.thumb_dir,
            settings.general.database_dir,
        )
    except Exception:
        pass
    from app.clip_onnx import reset_clip_encoder

    reset_clip_encoder()
    logger.info("Settings reloaded successfully.")


settings = load_settings()


async def require_mutation_allowed(request: Request) -> None:
    """Default API writes to disabled in presentation mode, before validation.

    Config retains its own access policy so presentation mode can be exited.
    Annotation routes retain their independent, explicit controls. Image search
    accepts an upload as query input without saving it or changing the library.
    """
    path = request.url.path
    if not settings.general.presentation_mode or request.method not in {
        "POST", "PATCH", "PUT", "DELETE"
    } or not (path == "/api" or path.startswith("/api/")):
        return
    if path in {"/api/config", "/api/annotations"} or path.startswith(
        ("/api/config/", "/api/annotations/")
    ):
        return
    if request.method == "POST" and path == "/api/search/by-image":
        return
    raise HTTPException(403, "Mutations are disabled in presentation mode.")

from .profiles import _default_local_profile_dir


def get_clip_model():
    """Compatibility accessor for the shared ONNX CLIP encoder."""
    from app.clip_onnx import get_clip_encoder
    return get_clip_encoder()
