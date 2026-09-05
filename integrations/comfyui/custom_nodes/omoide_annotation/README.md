# Omoide annotation node

This package is Omoide's narrow ComfyUI execution adapter for the WD
EVA02-Large tagger v3. It is deliberately not a generic model loader.

## Fixed contract

- model: `SmilingWolf/wd-eva02-large-tagger-v3`
- revision: `c5303bb7139430db980e4c680a778fe79d72b541`
- artifacts: `model.onnx`, `selected_tags.csv`
- artifact SHA-256 values are hard-coded and verified before session creation
- execution provider: ONNXRuntime `CPUExecutionProvider` only
- general threshold: score strictly greater than the exact float32 value
  `0.5296000242233276` (the model-card `0.5296` P=R operating point)
- character-candidate threshold: score strictly greater than the exact float32
  value `0.8500000238418579` (the official SmilingWolf ONNX inference-space
  `0.85` default)

The node accepts an in-memory ComfyUI `IMAGE` batch. It has no path, repository,
revision, provider, or threshold inputs. Inference is network-free: the two
reviewed artifacts must already exist under
`ComfyUI/models/annotators/omoide/wd-eva02-large-tagger-v3-v1.0/`, and both
SHA-256 values are checked before the process-lifetime session is created. No
Omoide media path is sent to the node.

The `annotation_json` output is a Comfy STRING list with one canonical document
per image in the input batch. Every document has exactly this top-level shape:

```json
{
  "schema": "omoide.annotation/v1",
  "kind": "tags",
  "profile_id": "omoide-tags-v1",
  "model": {},
  "tags": {"rating": [], "general": [], "character": []}
}
```

The three tag arrays preserve every raw model score and are deterministically
sorted by descending score and then name. Thresholds are provenance, not a
destructive filter. Omoide's renderer may select general tags from these raw
observations. Character tags are never mixed into a training-tag field and this
node never creates identity labels.

## Installation

Use an exact symlink so ComfyUI executes the reviewed Omoide-owned source:

```bash
ln -s \
  /home/elpresidank/YeeBois/workstation-apps/Omoide/integrations/comfyui/custom_nodes/omoide_annotation \
  /home/elpresidank/ai/ComfyUI/custom_nodes/omoide_annotation
```

The validated workstation ComfyUI venv contains the exact dependency versions
listed here, including ONNX Runtime 1.29.0. Do not downgrade that ambient venv.
For a different environment, install the lock-like requirements:

```bash
python -m pip install -r requirements.txt
```

ComfyUI itself supplies Torch and `comfy_api`; they intentionally remain tied to
the hosting ComfyUI build rather than being replaced by this node package.

## Verification

The core tests do not import ComfyUI and do not download the model:

```bash
/home/elpresidank/ai/ComfyUI/.venv/bin/python \
  -m unittest discover -s tests -v
```

Stage the model outside inference with the AI-hub installer, restart ComfyUI,
and verify `OmoideWDEva02LargeTaggerV3` in `/object_info`. Structural workflow
validation never downloads the model.

## Provenance

- model card: <https://huggingface.co/SmilingWolf/wd-eva02-large-tagger-v3>
- official ONNX reference: <https://huggingface.co/spaces/SmilingWolf/wd-tagger>
- model license: Apache-2.0
