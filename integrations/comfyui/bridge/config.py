"""Strict immutable configuration for allowlisted annotation profiles."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping
from urllib.parse import urlsplit

from .errors import BridgeError

CONFIG_SCHEMA = "omoide-comfy-bridge-config/v1"
STAGING_SUBFOLDER = "omoide"
PROFILE_ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9-]{0,63}\Z")
NODE_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
INPUT_NAME_PATTERN = re.compile(r"[A-Za-z0-9_.-]{1,128}\Z")
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
CONFIG_KEYS = frozenset(
    {"schema", "comfy_base_url", "staging_directory", "output_directory", "profiles"}
)
PROFILE_KEYS = frozenset(
    {
        "workflow_path",
        "workflow_sha256",
        "image_node_id",
        "image_node_class",
        "image_input",
        "output_node_id",
        "output_node_class",
        "output_key",
        "result_kind",
        "input_json_node_id",
        "input_json_input",
        "timeout_seconds",
        "reconcile_seconds",
        "poll_interval_seconds",
    }
)
REPAIR_PROFILE_IDS = frozenset(
    {
        "omoide-remove-text-v1",
        "omoide-upscale-v1",
        "omoide-remove-people-v1",
    }
)


@dataclass(frozen=True)
class ArtifactBinding:
    path: Path
    bytes_expected: int
    sha256: str
    name: str


@dataclass(frozen=True)
class ProfileBinding:
    workflow_path: Path
    workflow_sha256: str
    image_node_id: str
    image_node_class: str
    image_input: str
    output_node_id: str
    output_node_class: str
    output_key: str
    result_kind: str
    required_node_classes: tuple[str, ...]
    required_workflow_nodes: tuple[tuple[str, str], ...]
    required_combo_values: tuple[tuple[str, str, str], ...]
    artifacts: tuple[ArtifactBinding, ...]
    executor_artifacts: tuple[ArtifactBinding, ...]
    model_repo_id: str
    model_revision: str
    model_license: str
    comfy_version: str
    comfy_core_commit: str
    general_threshold: float | None = None
    character_threshold: float | None = None
    input_json_node_id: str | None = None
    input_json_input: str | None = None


_COMFY_NODES = ArtifactBinding(
    path=Path("/home/elpresidank/ai/ComfyUI/nodes.py"),
    bytes_expected=108_277,
    sha256="e56fe3ce5e08c168ed9e2c34fb06131814e6b7bee2e70c290fce11f9aa7d0009",
    name="ComfyUI/nodes.py",
)
_COMFY_PREVIEW_ANY = ArtifactBinding(
    path=Path("/home/elpresidank/ai/ComfyUI/comfy_extras/nodes_preview_any.py"),
    bytes_expected=1_495,
    sha256="48cffa3b2ddd08c311af609642da8703abc9bac9e624c400c3dc41f6edcea930",
    name="ComfyUI/comfy_extras/nodes_preview_any.py",
)
_COMFY_TEXTGEN = ArtifactBinding(
    path=Path("/home/elpresidank/ai/ComfyUI/comfy_extras/nodes_textgen.py"),
    bytes_expected=23_350,
    sha256="1813182f0b50545adfb51a4aace23d3cd86de312a287edf3aebc8ec84df1fde7",
    name="ComfyUI/comfy_extras/nodes_textgen.py",
)
_COMFY_QWEN35 = ArtifactBinding(
    path=Path("/home/elpresidank/ai/ComfyUI/comfy/text_encoders/qwen35.py"),
    bytes_expected=41_530,
    sha256="570fbf8b80a61e3238f8485a79487f4df066d13e14fba0a7493ae75b436f9dc5",
    name="ComfyUI/comfy/text_encoders/qwen35.py with PR 15685 offload fix",
)
_COMFY_GIT_HEAD = ArtifactBinding(
    path=Path("/home/elpresidank/ai/ComfyUI/.git/refs/heads/master"),
    bytes_expected=41,
    sha256="4d95a5693bc5af5b551f074dab327c88f2f33fb7ade3578e37453d7ebf7d4f1e",
    name="ComfyUI git revision e80c1570",
)
_OMOIDE_NODE_INIT = ArtifactBinding(
    path=Path(
        "/home/elpresidank/YeeBois/workstation-apps/Omoide/"
        "integrations/comfyui/custom_nodes/omoide_annotation/__init__.py"
    ),
    bytes_expected=549,
    sha256="93a8a0f3834329b1a035a2b282ebb503f19ee4b32c44b8014842827e983f72c7",
    name="omoide_annotation/__init__.py",
)
_OMOIDE_NODE_CORE = ArtifactBinding(
    path=Path(
        "/home/elpresidank/YeeBois/workstation-apps/Omoide/"
        "integrations/comfyui/custom_nodes/omoide_annotation/core.py"
    ),
    bytes_expected=12_659,
    sha256="925ddfa51926b1ac97efb063b8e62d69dcdbd96065b5d9a7b5e35104f71e1000",
    name="omoide_annotation/core.py",
)
_OMOIDE_NODE_WRAPPER = ArtifactBinding(
    path=Path(
        "/home/elpresidank/YeeBois/workstation-apps/Omoide/"
        "integrations/comfyui/custom_nodes/omoide_annotation/nodes.py"
    ),
    bytes_expected=1_635,
    sha256="7f595cbecc812babe491976616fe6000e1a25e8f26f117213cb3e010e0d6f819",
    name="omoide_annotation/nodes.py",
)


PROFILE_BINDINGS: Mapping[str, ProfileBinding] = MappingProxyType(
    {
        "omoide-caption-v1": ProfileBinding(
            workflow_path=Path(
                "/home/elpresidank/ai/workflows/comfy/omoide-caption-v1/"
                "workflow.api.json"
            ),
            workflow_sha256=(
                "a120a3a58837248eded8429632d45bbd0"
                "6d547711d44d93892fdc69e39929a03"
            ),
            image_node_id="1111775332128324",
            image_node_class="LoadImage",
            image_input="image",
            output_node_id="3287111284508309",
            output_node_class="PreviewAny",
            output_key="text",
            result_kind="text",
            required_node_classes=(
                "LoadImage",
                "CLIPLoader",
                "TextGenerate",
                "PreviewAny",
            ),
            required_workflow_nodes=(
                ("1111775332128324", "LoadImage"),
                ("1164633755326097", "CLIPLoader"),
                ("4078642408984279", "TextGenerate"),
                ("3287111284508309", "PreviewAny"),
            ),
            required_combo_values=(
                ("CLIPLoader", "clip_name", "qwen3.5_9b_bf16.safetensors"),
            ),
            artifacts=(
                ArtifactBinding(
                    path=Path(
                        "/home/elpresidank/ai/ComfyUI/models/text_encoders/"
                        "qwen3.5_9b_bf16.safetensors"
                    ),
                    bytes_expected=19_306_312_328,
                    sha256=(
                        "7e6e9f08d598f829cb940e60ac0c698e"
                        "1f1c27a47daffd7e598cd78c78b4cc53"
                    ),
                    name="qwen3.5_9b_bf16.safetensors",
                ),
            ),
            executor_artifacts=(
                _COMFY_GIT_HEAD,
                _COMFY_NODES,
                _COMFY_PREVIEW_ANY,
                _COMFY_TEXTGEN,
                _COMFY_QWEN35,
            ),
            model_repo_id="Comfy-Org/Qwen3.5",
            model_revision="5d50a2252bf1bcd49e5fee9b5f296986d442682b",
            model_license="Apache-2.0",
            comfy_version="0.34.0",
            comfy_core_commit="e80c1570b6b44a2557d5d8e341e05782d18c9bbb",
        ),
        "omoide-tags-v1": ProfileBinding(
            workflow_path=Path(
                "/home/elpresidank/ai/workflows/comfy/omoide-tags-v1/"
                "workflow.api.json"
            ),
            workflow_sha256=(
                "76fdd0daca41ad3bf6e8409a1e98e72b"
                "c08a1fdf5f32475e26e7e2296c00a1fc"
            ),
            image_node_id="3128925228060849",
            image_node_class="LoadImage",
            image_input="image",
            output_node_id="3436206424550325",
            output_node_class="PreviewAny",
            output_key="text",
            result_kind="json",
            required_node_classes=(
                "LoadImage",
                "OmoideWDEva02LargeTaggerV3",
                "PreviewAny",
            ),
            required_workflow_nodes=(
                ("3128925228060849", "LoadImage"),
                ("496084697938234", "OmoideWDEva02LargeTaggerV3"),
                ("3436206424550325", "PreviewAny"),
            ),
            required_combo_values=(),
            artifacts=(
                ArtifactBinding(
                    path=Path(
                        "/home/elpresidank/ai/ComfyUI/models/annotators/omoide/"
                        "wd-eva02-large-tagger-v3-v1.0/model.onnx"
                    ),
                    bytes_expected=1_260_435_999,
                    sha256=(
                        "9e768793060c7939b277ccb382783e867"
                        "0e8a042d29d77aa736be0c8cc898bfc"
                    ),
                    name="model.onnx",
                ),
                ArtifactBinding(
                    path=Path(
                        "/home/elpresidank/ai/ComfyUI/models/annotators/omoide/"
                        "wd-eva02-large-tagger-v3-v1.0/selected_tags.csv"
                    ),
                    bytes_expected=308_468,
                    sha256=(
                        "298633d94d0031d2081c0893f29c82ea"
                        "b7f0df00b08483ba8f29d1e979441217"
                    ),
                    name="selected_tags.csv",
                ),
            ),
            executor_artifacts=(
                _COMFY_GIT_HEAD,
                _COMFY_NODES,
                _COMFY_PREVIEW_ANY,
                _OMOIDE_NODE_INIT,
                _OMOIDE_NODE_CORE,
                _OMOIDE_NODE_WRAPPER,
            ),
            model_repo_id="SmilingWolf/wd-eva02-large-tagger-v3",
            model_revision="c5303bb7139430db980e4c680a778fe79d72b541",
            model_license="Apache-2.0",
            comfy_version="0.34.0",
            comfy_core_commit="e80c1570b6b44a2557d5d8e341e05782d18c9bbb",
            general_threshold=0.5296000242233276,
            character_threshold=0.8500000238418579,
        ),
    }
)


@dataclass(frozen=True)
class Profile:
    profile_id: str
    workflow_path: Path
    workflow_sha256: str
    image_node_id: str
    image_node_class: str
    image_input: str
    output_node_id: str
    output_node_class: str
    output_key: str
    result_kind: str
    timeout_seconds: float
    reconcile_seconds: float
    poll_interval_seconds: float
    required_node_classes: tuple[str, ...]
    required_combo_values: tuple[tuple[str, str, str], ...]
    artifacts: tuple[ArtifactBinding, ...]
    executor_artifacts: tuple[ArtifactBinding, ...]
    model_repo_id: str
    model_revision: str
    model_license: str
    comfy_version: str
    comfy_core_commit: str
    general_threshold: float | None
    character_threshold: float | None
    _workflow: dict[str, Any]
    input_json_node_id: str | None = None
    input_json_input: str | None = None

    def provenance(self) -> dict[str, Any]:
        provenance: dict[str, Any] = {
            "profile_id": self.profile_id,
            "workflow_sha256": self.workflow_sha256,
            "runtime": {
                "comfy_version": self.comfy_version,
                "comfy_core_commit": self.comfy_core_commit,
            },
            "executor": {
                "artifacts": [
                    {
                        "name": artifact.name,
                        "bytes": artifact.bytes_expected,
                        "sha256": artifact.sha256,
                    }
                    for artifact in self.executor_artifacts
                ],
            },
            "model": {
                "repo_id": self.model_repo_id,
                "revision": self.model_revision,
                "license": self.model_license,
                "artifacts": [
                    {
                        "name": artifact.name,
                        "bytes": artifact.bytes_expected,
                        "sha256": artifact.sha256,
                    }
                    for artifact in self.artifacts
                ],
            },
        }
        if self.general_threshold is not None:
            provenance["selection"] = {
                "comparison": "strictly-greater-than",
                "general_threshold": self.general_threshold,
                "character_threshold": self.character_threshold,
            }
        return provenance

    def provenance_sha256(self) -> str:
        encoded = json.dumps(
            self.provenance(),
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def make_prompt(
        self,
        staged_name: str,
        params: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if Path(staged_name).name != staged_name or not staged_name:
            raise BridgeError("invalid-staging-name", "staged filename is invalid")
        prompt = copy.deepcopy(self._workflow)
        prompt[self.image_node_id]["inputs"][self.image_input] = (
            f"{STAGING_SUBFOLDER}/{staged_name}"
        )
        if params is not None:
            if self.input_json_node_id is None or self.input_json_input is None:
                raise BridgeError(
                    "invalid-params",
                    "profile does not accept repair parameters",
                )
            prompt[self.input_json_node_id]["inputs"][self.input_json_input] = json.dumps(
                params,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        return prompt


@dataclass(frozen=True)
class BridgeConfig:
    comfy_base_url: str
    staging_directory: Path
    profiles: Mapping[str, Profile]
    output_directory: Path | None = None


def _require_mapping(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise BridgeError("configuration-error", f"{name} must be an object")
    return value


def _require_string(
    mapping: Mapping[str, Any],
    key: str,
    *,
    pattern: re.Pattern[str] | None = None,
) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value:
        raise BridgeError("configuration-error", f"{key} must be a string")
    if pattern is not None and pattern.fullmatch(value) is None:
        raise BridgeError("configuration-error", f"{key} has an invalid value")
    return value


def _bounded_number(
    mapping: Mapping[str, Any],
    key: str,
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    value = mapping.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BridgeError("configuration-error", f"{key} must be a number")
    number = float(value)
    if number < minimum or number > maximum:
        raise BridgeError("configuration-error", f"{key} is outside its limit")
    return number


def _validate_base_url(value: str) -> str:
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError as error:
        raise BridgeError("configuration-error", "Comfy URL port is invalid") from error
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
        or port is None
    ):
        raise BridgeError(
            "configuration-error",
            "Comfy URL must be an explicit http://127.0.0.1:<port> origin",
        )
    return f"http://127.0.0.1:{port}"


def _load_workflow(
    profile_id: str,
    profile_data: Mapping[str, Any],
) -> tuple[Path, str, dict[str, Any]]:
    workflow_path = Path(_require_string(profile_data, "workflow_path"))
    if not workflow_path.is_absolute():
        raise BridgeError(
            "configuration-error",
            f"profile {profile_id} workflow_path must be absolute",
        )
    expected_digest = _require_string(
        profile_data,
        "workflow_sha256",
        pattern=SHA256_PATTERN,
    )
    try:
        workflow_bytes = workflow_path.read_bytes()
    except OSError as error:
        raise BridgeError(
            "configuration-error",
            f"profile {profile_id} workflow could not be read",
        ) from error
    actual_digest = hashlib.sha256(workflow_bytes).hexdigest()
    if actual_digest != expected_digest:
        raise BridgeError(
            "configuration-error",
            f"profile {profile_id} workflow digest does not match",
        )
    try:
        workflow = json.loads(workflow_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BridgeError(
            "configuration-error",
            f"profile {profile_id} workflow is not valid JSON",
        ) from error
    workflow = _require_mapping(workflow, f"profile {profile_id} workflow")
    if not workflow:
        raise BridgeError(
            "configuration-error",
            f"profile {profile_id} workflow is empty",
        )
    return workflow_path.resolve(), actual_digest, workflow


def _load_profile(profile_id: str, value: object) -> Profile:
    if PROFILE_ID_PATTERN.fullmatch(profile_id) is None:
        raise BridgeError("configuration-error", "profile ID is invalid")
    data = _require_mapping(value, f"profile {profile_id}")
    if set(data) - PROFILE_KEYS:
        raise BridgeError(
            "configuration-error",
            f"profile {profile_id} contains unsupported fields",
        )
    workflow_path, digest, workflow = _load_workflow(profile_id, data)
    binding = PROFILE_BINDINGS.get(profile_id)
    if binding is None:
        if profile_id not in REPAIR_PROFILE_IDS:
            raise BridgeError(
                "configuration-error",
                f"profile {profile_id} is not in the built-in v1 allowlist",
            )
        expected_path = Path(
            f"/home/elpresidank/ai/workflows/comfy/{profile_id}/workflow.api.json"
        ).resolve()
        if workflow_path != expected_path:
            raise BridgeError(
                "configuration-error",
                f"profile {profile_id} workflow path is not the built-in trust root",
            )
        image_node_id = _require_string(data, "image_node_id", pattern=NODE_ID_PATTERN)
        image_input = _require_string(data, "image_input", pattern=INPUT_NAME_PATTERN)
        output_node_id = _require_string(data, "output_node_id", pattern=NODE_ID_PATTERN)
        input_json_node_id = data.get("input_json_node_id")
        input_json_input = data.get("input_json_input")
        if input_json_node_id is not None and (
            not isinstance(input_json_node_id, str)
            or NODE_ID_PATTERN.fullmatch(input_json_node_id) is None
        ):
            raise BridgeError("configuration-error", "input_json_node_id is invalid")
        if input_json_input is not None and (
            not isinstance(input_json_input, str)
            or INPUT_NAME_PATTERN.fullmatch(input_json_input) is None
        ):
            raise BridgeError("configuration-error", "input_json_input is invalid")
        if profile_id == "omoide-remove-people-v1" and (
            input_json_node_id is None or input_json_input is None
        ):
            raise BridgeError(
                "configuration-error",
                "remove-people profile requires a JSON input node",
            )
        binding = ProfileBinding(
            workflow_path=expected_path,
            workflow_sha256=digest,
            image_node_id=image_node_id,
            image_node_class="LoadImage",
            image_input=image_input,
            output_node_id=output_node_id,
            output_node_class="SaveImage",
            output_key="images",
            result_kind="image",
            required_node_classes=("LoadImage", "SaveImage"),
            required_workflow_nodes=(
                (image_node_id, "LoadImage"),
                (output_node_id, "SaveImage"),
            ),
            required_combo_values=(),
            artifacts=(),
            executor_artifacts=(_COMFY_GIT_HEAD, _COMFY_NODES),
            model_repo_id="host-managed-repair-workflow",
            model_revision=digest,
            model_license="host-managed",
            comfy_version="0.34.0",
            comfy_core_commit="e80c1570b6b44a2557d5d8e341e05782d18c9bbb",
            input_json_node_id=input_json_node_id,
            input_json_input=input_json_input,
        )
    if workflow_path != binding.workflow_path.resolve():
        raise BridgeError(
            "configuration-error",
            f"profile {profile_id} workflow path is not the built-in trust root",
        )
    if digest != binding.workflow_sha256:
        raise BridgeError(
            "configuration-error",
            f"profile {profile_id} workflow digest is not the built-in trust root",
        )
    for key, expected in (
        ("image_node_id", binding.image_node_id),
        ("image_node_class", binding.image_node_class),
        ("image_input", binding.image_input),
        ("output_node_id", binding.output_node_id),
        ("output_node_class", binding.output_node_class),
        ("output_key", binding.output_key),
        ("result_kind", binding.result_kind),
        ("input_json_node_id", binding.input_json_node_id),
        ("input_json_input", binding.input_json_input),
    ):
        if key in data and data[key] != expected:
            raise BridgeError(
                "configuration-error",
                f"profile {profile_id} cannot override its built-in {key}",
            )
    image_node_id = binding.image_node_id
    image_node_class = binding.image_node_class
    image_input = binding.image_input
    output_node_id = binding.output_node_id
    output_node_class = binding.output_node_class
    output_key = binding.output_key
    result_kind = binding.result_kind
    if result_kind not in {"text", "json", "image"}:
        raise BridgeError("configuration-error", "profile result_kind is unsupported")
    if result_kind == "image" and (
        output_node_class != "SaveImage" or output_key != "images"
    ):
        raise BridgeError(
            "configuration-error",
            f"profile {profile_id} image output must use SaveImage.images",
        )
    input_json_node_id = binding.input_json_node_id
    input_json_input = binding.input_json_input
    if (input_json_node_id is None) != (input_json_input is None):
        raise BridgeError(
            "configuration-error",
            f"profile {profile_id} JSON node fields must be supplied together",
        )
    image_node = _require_mapping(
        workflow.get(image_node_id),
        f"profile {profile_id} image node",
    )
    if image_node.get("class_type") != image_node_class:
        raise BridgeError(
            "configuration-error",
            f"profile {profile_id} image node class does not match",
        )
    image_inputs = _require_mapping(
        image_node.get("inputs"),
        f"profile {profile_id} image node inputs",
    )
    if image_input not in image_inputs:
        raise BridgeError(
            "configuration-error",
            f"profile {profile_id} image input does not exist",
        )
    output_node = _require_mapping(
        workflow.get(output_node_id),
        f"profile {profile_id} output node",
    )
    if output_node.get("class_type") != output_node_class:
        raise BridgeError(
            "configuration-error",
            f"profile {profile_id} output node class does not match",
        )
    if input_json_node_id is not None and input_json_input is not None:
        json_node = _require_mapping(
            workflow.get(input_json_node_id),
            f"profile {profile_id} JSON input node",
        )
        json_inputs = _require_mapping(
            json_node.get("inputs"),
            f"profile {profile_id} JSON input node inputs",
        )
        if input_json_input not in json_inputs:
            raise BridgeError(
                "configuration-error",
                f"profile {profile_id} JSON input does not exist",
            )
    for node_id, node_class in binding.required_workflow_nodes:
        node = _require_mapping(
            workflow.get(node_id),
            f"profile {profile_id} required node {node_id}",
        )
        if node.get("class_type") != node_class:
            raise BridgeError(
                "configuration-error",
                f"profile {profile_id} required node {node_id} class does not match",
            )
    return Profile(
        profile_id=profile_id,
        workflow_path=workflow_path,
        workflow_sha256=digest,
        image_node_id=image_node_id,
        image_node_class=image_node_class,
        image_input=image_input,
        output_node_id=output_node_id,
        output_node_class=output_node_class,
        output_key=output_key,
        result_kind=result_kind,
        timeout_seconds=_bounded_number(data, "timeout_seconds", 900, 1, 3600),
        reconcile_seconds=_bounded_number(data, "reconcile_seconds", 15, 1, 60),
        poll_interval_seconds=_bounded_number(
            data,
            "poll_interval_seconds",
            0.5,
            0.05,
            10,
        ),
        required_node_classes=binding.required_node_classes,
        required_combo_values=binding.required_combo_values,
        artifacts=binding.artifacts,
        executor_artifacts=binding.executor_artifacts,
        model_repo_id=binding.model_repo_id,
        model_revision=binding.model_revision,
        model_license=binding.model_license,
        comfy_version=binding.comfy_version,
        comfy_core_commit=binding.comfy_core_commit,
        general_threshold=binding.general_threshold,
        character_threshold=binding.character_threshold,
        input_json_node_id=input_json_node_id,
        input_json_input=input_json_input,
        _workflow=workflow,
    )


def load_config(path: Path) -> BridgeConfig:
    try:
        raw = json.loads(path.read_bytes())
    except OSError as error:
        raise BridgeError("configuration-error", "bridge config could not be read") from error
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BridgeError("configuration-error", "bridge config is not valid JSON") from error
    data = _require_mapping(raw, "bridge config")
    if set(data) - CONFIG_KEYS:
        raise BridgeError(
            "configuration-error",
            "bridge config contains unsupported fields",
        )
    if data.get("schema") != CONFIG_SCHEMA:
        raise BridgeError("configuration-error", "bridge config schema is unsupported")
    comfy_base_url = _validate_base_url(
        _require_string(data, "comfy_base_url")
    )
    staging_directory = Path(_require_string(data, "staging_directory"))
    if not staging_directory.is_absolute() or staging_directory.name != STAGING_SUBFOLDER:
        raise BridgeError(
            "configuration-error",
            "staging_directory must be an absolute directory named omoide",
        )
    output_directory_value = data.get("output_directory")
    output_directory: Path | None = None
    if output_directory_value is not None:
        if not isinstance(output_directory_value, str) or not output_directory_value:
            raise BridgeError(
                "configuration-error", "output_directory must be a string"
            )
        output_directory = Path(output_directory_value)
        if not output_directory.is_absolute():
            raise BridgeError(
                "configuration-error", "output_directory must be absolute"
            )
    profiles_data = _require_mapping(data.get("profiles"), "profiles")
    if not profiles_data:
        raise BridgeError("configuration-error", "at least one profile is required")
    profiles = {
        profile_id: _load_profile(profile_id, profile_data)
        for profile_id, profile_data in profiles_data.items()
    }
    if any(profile.result_kind == "image" for profile in profiles.values()) and (
        output_directory is None
    ):
        raise BridgeError(
            "configuration-error",
            "output_directory is required for image result profiles",
        )
    return BridgeConfig(
        comfy_base_url=comfy_base_url,
        staging_directory=staging_directory.resolve(),
        output_directory=(output_directory.resolve() if output_directory else None),
        profiles=MappingProxyType(profiles),
    )
