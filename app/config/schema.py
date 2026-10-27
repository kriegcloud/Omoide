import gc
import math
import os
import shutil
import sys
import threading
from enum import Enum
from pathlib import Path
from typing import Annotated, Any, Literal, TypeVar

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
from app.scan_scope import normalize_scope_list

E = TypeVar("E", bound=Enum)
IS_DOCKER = os.getenv("IS_DOCKER", False)
ENV_PREFIX = "OMOIDE_"


from .profiles import *

class FaceDetectionModel(str, Enum):
    # SCRFD-10G from InsightFace's buffalo_l pack: most accurate, slowest
    BUFFALO_L = "buffalo_l"
    # SCRFD-500M from the buffalo_s pack: ~4x faster detection on CPU,
    # misses more small/hard faces
    BUFFALO_S = "buffalo_s"


def _runtime_is_docker():
    return getattr(sys.modules.get("app.config"), "IS_DOCKER", IS_DOCKER)


class DuplicateKeepRule(Enum):
    """Defines the set of valid rules for keeping a duplicate file."""

    BIGGEST = "biggest"
    SMALLEST = "smallest"
    HIGHEST_RES = "highest_res"
    LOWEST_RES = "lowest_res"
    OLDEST = "oldest"
    NEWEST = "newest"

    @classmethod
    def get_default(cls):
        return cls.OLDEST

    def __str__(self):
        return self.value


class DuplicateHandlingRule(Enum):
    """Defines the set of valid rules for keeping a duplicate file."""

    KEEP = "keep"
    REMOVE = "remove"
    BLACKLIST = "blacklist"
    DELETE = "delete"

    @classmethod
    def get_default(cls):
        return cls.KEEP

    def __str__(self):
        return self.value


class MediaDirectory(BaseModel):
    path: Path
    read_only: bool = False


class HomeWidgetSetting(BaseModel):
    id: str
    enabled: bool = True


class ReadOnlyMediaError(PermissionError):
    def __init__(self, target: Path, base: Path):
        self.target = target
        self.base = base
        super().__init__(f"Media directory '{base}' is read-only.")


class MediaPathNotMountedError(PermissionError):
    def __init__(self, target: Path):
        self.target = target
        super().__init__(
            f"Media path '{target}' is not inside any currently configured media directory."
        )


def _resolve_path_safe(value: Path) -> Path:
    try:
        return value.expanduser().resolve()
    except Exception:
        try:
            return value.expanduser()
        except Exception:
            return value


def _is_within(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
        return True
    except ValueError:
        return False


class ClipModel(Enum):
    ROBERTA_LARGE_VIT_H_14 = (
        "xlm-roberta-large-ViT-H-14",
        1024,
        "frozen_laion5b_s13b_b90k",
        "immich-app/XLM-Roberta-Large-ViT-H-14__frozen_laion5b_s13b_b90k",
    )
    ROBERTA_BASE_VIT_B_32 = (
        "xlm-roberta-base-ViT-B-32",
        512,
        "laion5b_s13b_b90k",
        "immich-app/XLM-Roberta-Base-ViT-B-32__laion5b_s13b_b90k",
    )
    VIT_L_14 = (
        "ViT-L-14",
        768,
        "laion2b_s32b_b82k",
        "immich-app/ViT-L-14__laion2b-s32b-b82k",
    )
    VIT_B_32 = (
        "ViT-B-32",
        512,
        "laion2b_s34b_b79k",
        "immich-app/ViT-B-32__laion2b-s34b-b79k",
    )

    def __init__(
        self, model_name: str, embedding_size: int, pretrained: str, hf_repo: str
    ):
        """
        This initializer is called for each member, assigning the tuple
        values to attributes on the member itself.
        """
        self.model_name = model_name
        self.embedding_size = embedding_size
        self.pretrained = pretrained
        # Hugging Face repo holding the ONNX export of these weights
        self.hf_repo = hf_repo

    @classmethod
    def _missing_(cls, value: object):
        """This custom hook correctly finds an enum member from a string."""
        if isinstance(value, str):
            for member in cls:
                if member.model_name.lower() == value.lower():
                    return member
            if value.lower() == "convnext_base_w":
                # No ONNX export exists for this (never tested) option.
                logger.warning(
                    "CLIP model convnext_base_w is no longer supported; "
                    "using xlm-roberta-base-ViT-B-32"
                )
                return cls.ROBERTA_BASE_VIT_B_32
        return None

    @classmethod
    def get_default(cls):
        # Define the default model
        return cls.ROBERTA_LARGE_VIT_H_14

    def __str__(self):
        return self.model_name



class GeneralSettings(BaseModel):
    port: int = 8123
    # Run system in presentation_mode mode or not
    presentation_mode: bool = False
    # Runtime-only declaration that the Docker media bind mount is read-only.
    # Excluded from persisted config so deployment metadata remains authoritative.
    docker_media_read_only: bool = Field(default=False, exclude=True)
    # Optional host-side roots used only by the local path-export API.
    media_export_host_root_t7: Path | None = Field(default=None, exclude=True)
    media_export_host_root_t7xfer: Path | None = Field(default=None, exclude=True)
    # Enable face recognition and other person related features
    enable_people: bool = True
    meme_mode: bool = False
    is_docker: bool = bool(os.environ.get("IS_DOCKER", False))
    # Whether the app is running as a packaged/binary executable (e.g., PyInstaller)
    is_binary: bool = bool(getattr(sys, "frozen", False))
    # Prefer GPU acceleration when available.
    # Maximum nodes to request for the person relationship graph
    person_relationship_max_nodes: int = 100
    # Which host the system runs on. Mostly only relevant if hosted online.
    domain: str = f"http://localhost:{port}"
    # Optional override to point at a specific ffmpeg binary
    ffmpeg_path: Path | None = None
    # maximum number of thumbnails per folder, adjust according to your systems inodes
    thumb_dir_folder_size: int = 1000
    # GitHub repo (owner/name) used for update checks. Set to None to disable.
    update_check_repo: str | None = "EinAeffchen/Omoide"
    # How long (in minutes) to cache the latest release lookup.
    update_check_cache_minutes: int = 180
    # Timeout (seconds) for contacting the GitHub API.
    update_check_timeout_seconds: float = 5.0
    # Homepage widget visibility + order (list order = display order).
    # Empty means "use the built-in defaults".
    home_widgets: list[HomeWidgetSetting] = Field(default_factory=list)

    data_dir: Path = Field(default_factory=get_user_data_path)
    datasets_dir: Path | None = None
    datasets_host_root: Path | None = None

    # Derived paths (computed fields) keep in sync with data_dir
    @computed_field
    @property
    def database_dir(self) -> Path:
        return self.data_dir / "database"

    @computed_field
    @property
    def omoide_dir(self) -> Path:
        return self.data_dir / ".omoide"

    @computed_field
    @property
    def thumb_dir(self) -> Path:
        return self.omoide_dir / "thumbnails"

    # only relevant when run as binary
    media_dirs: list[MediaDirectory] = Field(default_factory=list)
    static_dir: Path = get_static_assets_dir()

    @computed_field
    @property
    def models_dir(self) -> Path:
        if _runtime_is_docker():
            return self.omoide_dir / "models"
        return get_os_app_config_dir() / "models"

    @computed_field
    @property
    def database_url(self) -> str:
        return (
            f"sqlite:///{self.database_dir}/omoide.db?cache=shared&mode=rwc"
            "&_journal_mode=WAL&_synchronous=NORMAL"
        )

    @field_validator("media_dirs", mode="before")
    @classmethod
    def _coerce_media_dirs(cls, value):
        if value is None:
            return []
        if not isinstance(value, list):
            return value
        normalized: list[dict[str, object]] = []
        for item in value:
            if isinstance(item, dict):
                path_value = (
                    item.get("path") or item.get("dir") or item.get("value")
                )
                if path_value is None:
                    continue
                read_only = bool(item.get("read_only", False))
                normalized.append({"path": path_value, "read_only": read_only})
            elif isinstance(item, (str, Path)):
                normalized.append({"path": item, "read_only": False})
        return normalized

    def model_post_init(self, context) -> None:
        # Presentation/read-only mode is only configurable via Docker runtime env.
        # Force it off in binaries/local runs so the UI/config file cannot enable it.
        if not _runtime_is_docker():
            self.presentation_mode = False

        # Ensure required directories exist based on current data_dir
        self.database_dir.mkdir(parents=True, exist_ok=True)
        self.omoide_dir.mkdir(parents=True, exist_ok=True)
        self.thumb_dir.mkdir(parents=True, exist_ok=True)
        self.models_dir.mkdir(parents=True, exist_ok=True)
        if not _runtime_is_docker():
            legacy_models_dir = self.omoide_dir / "models"
            try:
                if (
                    legacy_models_dir.exists()
                    and legacy_models_dir != self.models_dir
                ):
                    migrated_any = False
                    for item in legacy_models_dir.iterdir():
                        dest = self.models_dir / item.name
                        if dest.exists():
                            continue
                        try:
                            shutil.move(str(item), dest)
                            migrated_any = True
                        except Exception as move_err:
                            logger.warning(
                                "Could not migrate legacy model %s -> %s: %s",
                                item,
                                dest,
                                move_err,
                            )
                    if migrated_any:
                        logger.info(
                            "Migrated profile models into shared cache: %s",
                            self.models_dir,
                        )
                    try:
                        legacy_models_dir.rmdir()
                    except OSError:
                        pass
            except Exception as e:
                logger.warning(
                    "Could not migrate legacy models directory: %s", e
                )
        if _runtime_is_docker():
            self.media_dirs = [
                MediaDirectory(
                    path=Path("/app/media"),
                    read_only=self.docker_media_read_only,
                )
            ]

    def resolved_media_dirs(self) -> list[tuple[Path, bool]]:
        """Return unique, resolved media directories paired with read-only flag."""
        resolved: list[tuple[Path, bool]] = []
        seen: set[Path] = set()
        for entry in self.media_dirs:
            base = _resolve_path_safe(entry.path)
            if base in seen:
                continue
            seen.add(base)
            resolved.append((base, bool(entry.read_only)))
        return resolved

    def resolved_datasets_dir(self) -> Path:
        """Return the configured export root or the active profile default."""
        return _resolve_path_safe(self.datasets_dir or self.data_dir / "datasets")

    def ensure_media_path_writable(self, target: Path) -> None:
        """Raise if the path is not writable due to mount/read-only restrictions."""
        normalized = _resolve_path_safe(target)
        matches = [
            (base, read_only) for base, read_only in self.resolved_media_dirs()
            if _is_within(normalized, base)
        ]
        if not matches:
            raise MediaPathNotMountedError(normalized)
        base, read_only = max(matches, key=lambda entry: len(entry[0].parts))
        if read_only:
            raise ReadOnlyMediaError(normalized, base)

    write_location_to_files: bool = True



class TaggingSettings(BaseModel):
    auto_tagging: bool = True
    # whether to use the default system defined tags.
    use_default_tags: bool = True
    # add any tags in any language supported by the chosen clip model
    custom_tags: list[str] = []


class AnnotationSettings(BaseModel):
    """Host-bridge settings for opt-in, single-media dataset annotation."""

    enabled: bool = False
    inference_socket_path: Path = Path(
        "/app/data/run/omoide-comfy.sock"
    )
    inference_timeout_seconds: float = Field(
        # The bridge profile may spend 900 seconds after submission. Keep the
        # socket caller strictly above that plus readiness/upload/reconcile
        # margin so the client cannot abandon a still-running exact UUID first.
        default=1080.0, gt=0.0, le=3600.0
    )
    caption_profile_id: str = "omoide-caption-v1"
    tags_profile_id: str = "omoide-tags-v1"


class TrainingSettings(BaseModel):
    default_base_model: str = "z-image"
    z_image_path: str | None = None
    z_image_turbo_path: str | None = None
    launcher_stale_after_seconds: int = 120

    @field_validator("z_image_path", "z_image_turbo_path", mode="before")
    @classmethod
    def _empty_path_is_none(cls, value):
        return None if value == "" else value


class RepairSettings(BaseModel):
    """Opt-in settings for host-side ComfyUI image repair."""

    enabled: bool = False
    inference_socket_path: Path = Path("/app/data/run/omoide-comfy.sock")
    timeout_seconds: float = Field(default=1080.0, gt=0.0, le=3600.0)
    remove_text_profile_id: str = "omoide-remove-text-v1"
    upscale_profile_id: str = "omoide-upscale-v1"
    remove_people_profile_id: str = "omoide-remove-people-v1"
    background_swap_profile_id: str = "omoide-background-swap-v1"
    eval_profile_id: str = "omoide-eval-zimage-v1"


class ScanSettings(BaseModel):
    # Opt in explicitly: interrupted processors can include expensive CLIP backfills.
    auto_resume_interrupted_tasks: bool = False
    # enables automatic background scans for new files
    auto_scan: bool = False
    # How often to scan for files in minutes
    scan_interval_minutes: int = 15
    # If autoscan should also automatically clean the database from missing files
    auto_clean_on_scan: bool = False
    # Automatically delete records for files that remain missing without manual review
    auto_cleanup_without_review: bool = False
    # Grace period in hours before auto cleanup removes a missing record
    auto_cleanup_grace_hours: int = 72
    # If autoscan should also automatically cluster all faces.
    # Not recommended if you manually adjusted people
    auto_cluster_on_scan: bool = False
    # sometimes images are rotated based on their exif data
    # this is automatically caught by most apps and the user doesn't notice
    # this option automatically rotates them to the correct direction and removes
    # the exif rotation information
    auto_rotate: bool = True
    # Pillow refuses to decode images above twice this many pixels (its
    # "decompression bomb" guard). The library default (~89 MP) rejects modern
    # phone photos such as the 200 MP Samsung S24 Ultra, so default to 400 MP.
    # A value <= 0 disables the guard entirely.
    max_image_pixels: int = 400_000_000
    # Skip files that look like thumbnails while scanning media directories
    skip_thumbnails_on_scan: bool = True
    # Scan scope, relative to each media directory (see app/scan_scope.py).
    # Folders to scan; empty scans the whole media directory.
    include_folders: list[str] = Field(default_factory=list)
    # Folders never scanned, with everything below them. Wins over includes.
    exclude_folders: list[str] = Field(default_factory=list)
    # Folder-name globs skipped at any depth, case-insensitive (e.g. "@eaDir").
    exclude_folder_patterns: list[str] = Field(default_factory=list)
    VIDEO_SUFFIXES: list[str] = [
        ".mp4",
        ".mov",
        ".wmv",
        ".avi",
        ".flv",
        ".mkv",
        ".webm",
        ".gp3",
        ".gif",
        ".ts",
        ".mpeg",
    ]
    IMAGE_SUFFIXES: list[str] = [
        ".jpg",
        ".jpeg",
        ".png",
        ".tiff",
        ".bmp",
        ".heic",
        ".heif",
        ".webp",
    ]

    @field_validator("include_folders", "exclude_folders", mode="before")
    @classmethod
    def _normalize_scope_folders(cls, value):
        return normalize_scope_list(value, patterns=False)

    @field_validator("exclude_folder_patterns", mode="before")
    @classmethod
    def _normalize_scope_patterns(cls, value):
        return normalize_scope_list(value, patterns=True)


class AISettings(BaseModel):
    # which model to use for image processing. Options:
    # 1. xlm-roberta-large-ViT-H-14 -> Cross language large model
    # 2. xlm-roberta-base-ViT-B-32 -> Cross language small model
    # Below models are not tested yet and might need adjustments on the database
    # vector sizes
    # 3. ViT-L-14 -> english only large model
    # 4. laion2b_s32b_b82k -> english only large model
    # 5. ViT-B-32 -> english only base model
    # 6. convnext_base_w -> english only convolution base model
    clip_model: Annotated[
        ClipModel,
        PlainSerializer(lambda x: x.model_name, return_type=str),
    ] = ClipModel.ROBERTA_BASE_VIT_B_32

    # @computed_field
    @property
    def clip_model_embedding_size(self) -> int:
        return self.clip_model.embedding_size

    # @computed_field
    @property
    def clip_model_pretrained(self) -> str:
        return self.clip_model.pretrained

    # Strictness of the search results. Higher -> more accurate but less hits.
    # Internally: max_dist = 2.0 - min_search_dist. For L2 distance on unit
    # vectors this translates to a minimum cosine similarity of
    # 1 - (max_dist² / 2).  At 0.68 that is ~13 %; at 0.80 it is ~28 %.
    min_search_dist: float = 0.80
    # Defines the maximum distance for similarity between two images.
    # Used to reduce/increase number of similar images. Higher -> stronger similarity
    min_similarity_dist: float = 1.2
    # (cluster-specific knobs moved to face_recognition)


class FaceClusteringPreset(str, Enum):
    STRICT = "strict"
    NORMAL = "normal"
    LOOSE = "loose"
    CUSTOM = "custom"


class FaceEmbeddingBackend(str, Enum):
    """Face embedding implementation used for one coherent vector space."""

    BUFFALO_L = "buffalo_l"
    ADAFACE_KPRPE = "adaface_kprpe"


FACE_RECOGNITION_PRESETS: dict[
    FaceClusteringPreset, dict[str, float | int | str | bool]
] = {
    FaceClusteringPreset.STRICT: {
        "match_new_faces_on_index": True,
        "face_recognition_min_confidence": 0.6,
        "existing_person_cosine_threshold": 0.68,
        "existing_person_min_cosine_margin": 0.07,
        "rerun_face_iou_dedupe_threshold": 0.70,
        "face_recognition_min_face_pixels": 1600,
        "person_min_face_count": 3,
        "person_min_media_count": 2,
        "person_merge_search_k": 30,
        "person_cluster_max_l2_radius": 0.95,
        "person_merge_percent_similarity": 78,
        "cluster_batch_size": 15000,
        "cluster_seed_min_det_score": 0.65,
        "cluster_seed_min_frontality": 0.35,
        "person_matching_max_prototypes": 3,
        "hdbscan_min_cluster_size": 6,
        "hdbscan_min_samples": 12,
        "hdbscan_cluster_selection_method": "eom",
        "hdbscan_cluster_selection_epsilon": 0.07,
        "hdbscan_min_membership_probability": 0.40,
        "cw_threshold": 0.70,
        "cw_k_neighbors": 10,
        "cw_iterations": 20,
        "cw_min_member_similarity": 0.55,
    },
    FaceClusteringPreset.NORMAL: {
        "match_new_faces_on_index": True,
        "face_recognition_min_confidence": 0.5,
        "existing_person_cosine_threshold": 0.62,
        "existing_person_min_cosine_margin": 0.05,
        "rerun_face_iou_dedupe_threshold": 0.70,
        "face_recognition_min_face_pixels": 1600,
        "person_min_face_count": 2,
        "person_min_media_count": 2,
        "person_merge_search_k": 40,
        "person_cluster_max_l2_radius": 1,
        "person_merge_percent_similarity": 72,
        "cluster_batch_size": 15000,
        "cluster_seed_min_det_score": 0.60,
        "cluster_seed_min_frontality": 0.30,
        "person_matching_max_prototypes": 3,
        "hdbscan_min_cluster_size": 6,
        "hdbscan_min_samples": 10,
        "hdbscan_cluster_selection_method": "eom",
        "hdbscan_cluster_selection_epsilon": 0.10,
        "hdbscan_min_membership_probability": 0.30,
        "cw_threshold": 0.65,
        "cw_k_neighbors": 10,
        "cw_iterations": 20,
        "cw_min_member_similarity": 0.50,
    },
    FaceClusteringPreset.LOOSE: {
        "match_new_faces_on_index": True,
        "face_recognition_min_confidence": 0.4,
        "existing_person_cosine_threshold": 0.56,
        "existing_person_min_cosine_margin": 0.03,
        "rerun_face_iou_dedupe_threshold": 0.70,
        "face_recognition_min_face_pixels": 1200,
        "person_min_face_count": 2,
        "person_min_media_count": 2,
        "person_merge_search_k": 50,
        "person_cluster_max_l2_radius": 1.04,
        "person_merge_percent_similarity": 68,
        "cluster_batch_size": 15000,
        "cluster_seed_min_det_score": 0.55,
        "cluster_seed_min_frontality": 0.25,
        "person_matching_max_prototypes": 3,
        "hdbscan_min_cluster_size": 4,
        "hdbscan_min_samples": 4,
        "hdbscan_cluster_selection_method": "eom",
        "hdbscan_cluster_selection_epsilon": 0.11,
        "hdbscan_min_membership_probability": 0.20,
        "cw_threshold": 0.62,
        "cw_k_neighbors": 10,
        "cw_iterations": 20,
        "cw_min_member_similarity": 0.45,
    },
}


class FaceRecognitionSettings(BaseModel):
    # Attach newly indexed faces to existing people when no clustering follows.
    match_new_faces_on_index: bool = True
    embedding_backend: FaceEmbeddingBackend = FaceEmbeddingBackend.BUFFALO_L
    inference_socket_path: Path = Path("/app/data/run/omoide-ml.sock")
    inference_timeout_seconds: float = Field(default=180.0, gt=0.0, le=600.0)
    preset: FaceClusteringPreset = FaceClusteringPreset.LOOSE
    # minimum confidence needed to extract a face
    face_recognition_min_confidence: float = 0.5
    # cosine threshold for attaching a face to an existing person's prototypes.
    # Must stay consistent with person_cluster_max_l2_radius: clusters accept
    # members down to ~0.5 cosine from the centroid, so demanding 0.8 here
    # meant trimmed/leftover faces of the same person could never re-attach.
    existing_person_cosine_threshold: float = 0.62
    # requires a margin between best and second-best match (cosine space)
    existing_person_min_cosine_margin: float = 0.05
    # on processor re-run, treat detections with IoU above this threshold as duplicates
    rerun_face_iou_dedupe_threshold: float = Field(
        default=0.70, ge=0.0, le=1.0
    )
    # minimum size of a face in pixels to be detected. Base size for detection
    # is the original image, not a thumbnail!
    face_recognition_min_face_pixels: int = 1600
    # skip blurry/occluded face crops before embedding (Laplacian variance check)
    face_sharpness_filter_enabled: bool = False
    face_sharpness_min_variance: float = 30.0
    # cap how many frames per video are scanned for faces (sampled evenly
    # across the video's scenes). Faces recur across scenes, so scanning
    # every scene frame adds little. 0 = scan all scene frames.
    face_detection_max_video_frames: int = 15
    # number of faces needed to automatically create a person
    person_min_face_count: int = 2
    # require a person to span at least this many distinct media assets
    person_min_media_count: int = 2
    # enforce intra-cluster compactness when forming a new person
    # maximum allowed L2 radius around centroid (normalized vectors)
    person_cluster_max_l2_radius: float = 1.04
    # merge previously created persons when their embeddings are extremely similar
    person_merge_percent_similarity: int = 80
    person_merge_search_k: int = 20
    # reduce if ram is an issue, the higher the more accurate the clustering.
    cluster_batch_size: int = 15000
    # Quality gate for SEEDING new person clusters. Low-confidence and hard
    # profile faces produce unreliable embeddings that pile up into junk
    # "people" (side profiles, statues). Such faces are excluded from cluster
    # formation but can still be ATTACHED to existing persons during the
    # leftover matching pass. Faces extracted before these columns existed
    # (NULL values) are not filtered.
    cluster_seed_min_det_score: float = 0.55
    cluster_seed_min_frontality: float = 0.25
    # number of embedding prototypes kept per person when matching leftover
    # faces (captures pose modes instead of a single frontal-dominated centroid)
    person_matching_max_prototypes: int = 3
    # Supplement the quality-filtered matching prototypes with centroids from
    # each person's frontal, three-quarter, and profile faces.
    person_pose_prototypes_enabled: bool = True
    person_pose_prototype_min_faces: int = Field(default=4, ge=1)
    person_pose_prototype_sample_cap: int = Field(default=256, ge=8)
    # Deterministic per-person sample used to build the k-means prototypes.
    person_prototype_sample_cap: int = Field(default=64, ge=8)
    # clustering algorithm to use for person discovery
    # graph k-NN clustering settings (cosine similarity)
    cw_threshold: float = 0.62
    cw_iterations: int = 20
    cw_k_neighbors: int = 10
    # after Chinese Whispers converges, drop cluster members whose cosine
    # similarity to the cluster centroid falls below this (kills transitive
    # chain merges; dropped faces stay unassigned for the matching pass)
    cw_min_member_similarity: float = 0.45
    # Cap per-HDBSCAN batch size to keep large datasets from stalling.
    # HDBSCAN tuning to reduce over-merged clusters (e.g., side profiles)
    hdbscan_min_cluster_size: int = 6
    hdbscan_min_samples: int = 10
    hdbscan_cluster_selection_method: str = "eom"  # "leaf" for finer clusters
    hdbscan_cluster_selection_epsilon: float = 0.10
    # demote HDBSCAN members with membership probability below this to noise
    hdbscan_min_membership_probability: float = 0.20

    def model_post_init(self, __context: Any) -> None:
        fields_set = getattr(self, "model_fields_set", set())
        if "preset" not in fields_set:
            matched = self._infer_preset()
            object.__setattr__(self, "preset", matched)
            return

        if self.preset == FaceClusteringPreset.CUSTOM:
            return

        preset_values = FACE_RECOGNITION_PRESETS.get(self.preset)
        if not preset_values:
            object.__setattr__(self, "preset", FaceClusteringPreset.CUSTOM)
            return

        for field_name, value in preset_values.items():
            object.__setattr__(self, field_name, value)

    def _infer_preset(self) -> FaceClusteringPreset:
        for preset, values in FACE_RECOGNITION_PRESETS.items():
            if self._matches_values(values):
                return preset
        return FaceClusteringPreset.CUSTOM

    def _matches_values(self, expected: dict[str, float | int | str]) -> bool:
        for field_name, value in expected.items():
            current = getattr(self, field_name)
            if isinstance(value, float):
                if not math.isclose(
                    float(current), float(value), rel_tol=1e-4, abs_tol=1e-4
                ):
                    return False
            else:
                if current != value:
                    return False
        return True

    face_detection_model: FaceDetectionModel = FaceDetectionModel.BUFFALO_L



class DuplicateSettings(BaseModel):
    """Defines how to handle detected duplicate files"""

    # what to do with duplicates. Default = Keep = do nothing automatically
    duplicate_auto_handling: DuplicateHandlingRule = DuplicateHandlingRule.KEEP
    # Which image/video to keep in case auto_handling != keep
    duplicate_auto_keep_rule: DuplicateKeepRule = DuplicateKeepRule.HIGHEST_RES


class EventSettings(BaseModel):
    """Controls the Events tab and the "Rebuild events" clustering task."""

    # show/hide the Events tab and its endpoints in the UI
    events_enabled: bool = True
    # a new event starts when consecutive media are further apart than this
    event_gap_hours: float = 6.0
    # clusters smaller than this are noise, not an "event"
    event_min_media: int = 3
    # when rebuilding, try to match renamed events to their new cluster by
    # media overlap and keep the custom title instead of the auto-generated one
    preserve_renamed_on_rebuild: bool = True


VideoConversionMode = Literal["off", "keep_original", "replace_original"]


class VideoSettings(BaseModel):
    """These settings control the video processing. By default we automatically
    detect scenes in a video and retrieve thumbnails for the scene overlay
    and the content searchability. If scene detection fails we split videos every
    n seconds into a maximum of :max_frames_per_video scenes."""

    # automatically detect scenes to split video into images
    auto_scene_detection: bool = True
    # Max number of frames detected per video if automatic scene detection fails
    max_frames_per_video: int = 30
    # Frames to skip between analyzed frames during scene detection. Higher
    # values speed up detection roughly linearly; with the coarse minimum
    # scene length used here the precision loss is negligible. 0 = analyze
    # every frame.
    scene_detection_frame_skip: int = 2

    convert_on_the_fly: bool = False

    auto_convert: VideoConversionMode = "off"



class ContentProcessorSettings(BaseModel):
    # extract exif data from images/videos
    exif_processor_active: bool = True
    # extract faces from content. Only active when enable_people=true.
    face_processor_active: bool = True
    # image embedding creator active. Needed for all AI related features
    # if you want to deactivate all smart features, set this to False
    image_embedding_processor_active: bool = True
    # compute Laplacian variance for blur detection during media processing
    blur_processor_active: bool = True
    # number of media rows processed per batch when running the heavy pipeline
    media_batch_size: int = 125
    # use GPU acceleration when available (CUDA/MPS for CLIP, CUDA/DML for InsightFace)
    # falls back to CPU automatically when no GPU is detected
    prefer_gpu: bool = True
    # number of video frames batched together for a single CLIP forward pass
    embedding_batch_size: int = 16


class AppSettings(BaseModel):
    general: GeneralSettings = Field(default_factory=GeneralSettings)
    scan: ScanSettings = Field(default_factory=ScanSettings)
    ai: AISettings = Field(default_factory=AISettings)
    tagging: TaggingSettings = Field(default_factory=TaggingSettings)
    annotations: AnnotationSettings = Field(default_factory=AnnotationSettings)
    training: TrainingSettings = Field(default_factory=TrainingSettings)
    repairs: RepairSettings = Field(default_factory=RepairSettings)
    face_recognition: FaceRecognitionSettings = Field(
        default_factory=FaceRecognitionSettings
    )
    duplicates: DuplicateSettings = Field(default_factory=DuplicateSettings)
    events: EventSettings = Field(default_factory=EventSettings)
    video: VideoSettings = Field(default_factory=VideoSettings)
    processors: ContentProcessorSettings = Field(
        default_factory=ContentProcessorSettings
    )
