# Omoide ComfyUI bridge

This service is the narrow host boundary between the Omoide container and the
local ComfyUI server. Omoide sends normalized JPEG or PNG bytes over a private
Unix socket. It never sends a media-library path or a workflow.

The bridge accepts only the built-in annotation IDs, the three repair IDs, and
the evaluation generation ID documented below. At startup it reads each API workflow once,
verifies the configured SHA-256, validates the fixed input/output node binding,
and retains that immutable snapshot. Before admitting each attempt it also
verifies the exact Comfy node files, Omoide custom-node executor files, model
artifacts, and declared Comfy version recorded in profile provenance. A request can replace only the declared
`LoadImage.image` value with an opaque `input/omoide/<attempt UUID>` name. A
repair profile may additionally replace one declared node input with a bounded,
canonical JSON parameters string.

## Workstation setup

ComfyUI remains loopback-only at `http://127.0.0.1:8188`. The bridge does not
start, stop, or restart it.

1. Create the two workflow packets under `/home/elpresidank/ai/workflows/comfy/`
   and validate them in the AI ops hub.
2. Copy `config.example.json` to
   `/home/elpresidank/.config/omoide/comfy-bridge.json`.
3. Create the unit's narrow staging write target before enabling its filesystem
   sandbox:

   ```bash
   install -d -m 0700 /home/elpresidank/ai/ComfyUI/input/omoide
   ```

4. Verify the pinned API workflow digests before installation: caption
   `a120a3a58837248eded8429632d45bbd06d547711d44d93892fdc69e39929a03`
   and tags
   `76fdd0daca41ad3bf6e8409a1e98e72bc08a1fdf5f32475e26e7e2296c00a1fc`.
   Do not loosen or remove the fixed node fields.
5. Link `packaging/systemd/omoide-comfy-bridge.service` into
   `/home/elpresidank/.config/systemd/user/`, run `systemctl --user daemon-reload`,
   and enable/start `omoide-comfy-bridge.service`.

Validate the config without opening a socket:

```bash
packaging/omoide-comfy-bridge-launcher --check
```

Probe the private socket and Comfy readiness:

```bash
packaging/omoide-comfy-bridge-launcher --health
```

The service can be healthy while reporting `ready=false` when ComfyUI is
offline. Omoide must treat that as an unavailable optional backend, not as a
reason to start ComfyUI from a web request.

## Re-pinning after a ComfyUI update

Every profile pins the ComfyUI git revision, a handful of core files, the Omoide
custom-node files and the model artifacts by size and SHA-256. Updating ComfyUI
(or the custom nodes) therefore makes the bridge refuse every attempt with
`configuration-error` until the pins are refreshed. That is intentional: review
the upgrade, then re-pin in one step:

```bash
.venv/bin/python scripts/repin_comfy_bridge.py            # report drift (exit 1 when any)
.venv/bin/python scripts/repin_comfy_bridge.py --write    # rewrite the pins in config.py
packaging/omoide-comfy-bridge-launcher --check
systemctl --user restart omoide-comfy-bridge.service
```

`--skip-models` leaves the multi-gigabyte model artifacts unhashed when only the
executor changed. Missing files are reported and never re-pinned. Commit the
resulting `config.py` change together with the ComfyUI revision it was verified
against.

ComfyUI itself runs from `packaging/systemd/comfyui.service` (loopback only, no
filesystem sandbox because it needs the GPU devices and its model directories).
Enable it once with `systemctl --user enable --now comfyui.service`; comfy-cli's
`comfy launch --background` must not run at the same time or the two instances
fight over port 8188.

## Protocol

The socket uses a four-byte network-order length followed by bounded UTF-8 JSON.
Every request includes `protocol: "omoide-comfy/v1"` and one action:

- `health` reports Comfy readiness, fixed profiles, and the active attempt.
- `annotate` accepts a canonical attempt UUID, allowlisted profile ID, base64
  image bytes, MIME type, and SHA-256. It is synchronous and returns a typed
  result only after the expected history UI output exists.
- `repair` has the same image/provenance request fields as `annotate`, plus an
  optional `params` object whose encoded JSON is at most 8 KiB. It accepts only
  a profile with `result_kind: "image"`.
- `generate` accepts a canonical attempt UUID, the allowlisted
  `omoide-eval-zimage-v1` params profile, and a bounded `params` object. It
  submits no image; the immutable workflow receives canonical params JSON and
  returns the same verified image result shape as `repair`.
- `get_attempt` reads `/history/<UUID>` and `/queue` by exact UUID. It never
  submits or changes a prompt, so Omoide startup can recover a completed result
  or classify an absent prompt as unknown/lost.
- `cancel` calls only `/api/jobs/<UUID>/cancel`; the bridge never uses global
  `/interrupt`.
- `ack_attempt` is the post-commit retention boundary. The bridge first reads
  `/history/<UUID>`, verifies the exact Omoide profile metadata, provenance,
  and terminal Comfy status. Only then does it send
  `POST /history` with `{"delete":["<UUID>"]}` and verify that exact history
  entry is absent. An already-absent UUID is an idempotent success only when
  the exact attempt is neither active nor queued. Foreign, malformed, or
  non-terminal history is never deleted. Verified failures and cancellations
  are deleted without decoding their potentially invalid or verbose outputs.
Exact history reads have a separate 32 MiB response ceiling so bounded verbose
terminal diagnostics can still be verified and removed; the 8 MiB ceiling on
queue, catalog, health, submission, upload, and deletion responses is unchanged.

The caller must persist its attempt UUID before `annotate`. A submit transport
failure is reconciled by the same UUID and is never automatically resubmitted.
Transient history/queue read failures are retried inside the bounded profile
deadline. An outcome that still cannot be proven remains unresolved rather
than becoming a retryable failure. A pre-submission cancellation leaves a
bounded UUID tombstone, preventing a worker that lost the database race from
submitting later.
Only one Omoide annotation can be active, and the bridge refuses admission when
any foreign Comfy prompt is queued or running.

Omoide calls `ack_attempt` only after a definitive terminal attempt is durable;
successful attempts also require the immutable annotation revision to commit in
the same transaction as success. It records `history_acknowledged_at` in a
separate transaction after the bridge confirms deletion. A lost response or
failed timestamp write leaves that field null without changing the committed
success, failure, or cancellation. One process-local supervisor retries such
rows sequentially while Omoide remains up, using capped exponential backoff and
periodic scans so a missed wake-up or a row committed by another process is not
stranded until restart. Its cleanup-only socket deadline and explicit lifespan
stop keep shutdown bounded. Pre-submission cancellations record the receipt
atomically because no Comfy prompt can exist. This ordering keeps successful and
failed Comfy history bounded without making it the system of record.
Media deletion uses the same database write lease as annotation admission and
refuses to cascade any definitive Comfy attempt while this acknowledgement
timestamp is null, preserving the durable cleanup receipt across crashes.

### Image result profiles

An image profile binds one `LoadImage` node and one selected `SaveImage` node.
Its fixed output contract is `output_node_class: "SaveImage"`,
`output_key: "images"`, and `result_kind: "image"`. If it accepts parameters,
both `input_json_node_id` and `input_json_input` identify the string input that
receives canonical JSON. Comfy history must report exactly one descriptor from
the selected SaveImage node: `{filename, subfolder, type: "output"}`.

The top-level `output_directory` is ComfyUI's absolute host output directory.
The bridge resolves the descriptor beneath it, rejects traversal and non-output
descriptors, reads at most 64 MiB, and accepts only a verified PNG or JPEG. It
returns base64 bytes with media type, SHA-256, width, and height; no output path
crosses the socket. `health` includes a `profile_result_kinds` mapping.

The repair allowlist and node contracts are:

- `omoide-remove-text-v1`: LoadImage to OCR/mask/inpaint to SaveImage.
- `omoide-upscale-v1`: LoadImage to 2x restoration to SaveImage.
- `omoide-remove-people-v1`: LoadImage plus a JSON `subject_box` input to
  segmentation/inpainting to SaveImage. A second SaveImage may save the mask;
  the bridge ignores every output node except the configured one.

The generate allowlist contains `omoide-eval-zimage-v1`. It uses
`input_kind: "params"`, requires `input_json_node_id` and
`input_json_input`, and has no image-node fields. `OmoideEvalParams` receives
the canonical JSON while `OmoideEvalLoraLoader` reads the absolute
`lora_path`; the bridge only admits `.safetensors` paths contained by the
top-level `lora_root` (the datasets host root). No LoRA is copied or staged.
Health reports every profile's input mode in `profile_input_kinds`.

The bounded media-state endpoint intentionally remains non-paginated for wire
compatibility. The separate cursor endpoints planned for long-lived history are
specified in `docs/annotation-history-pagination.md`.

## Filesystem and network boundary

The systemd unit denies both media SSD mount roots, permits only AF_UNIX and
IPv4, and limits IPv4 to `127.0.0.1`. Its only writable paths are the shared
Omoide socket directory and `/home/elpresidank/ai/ComfyUI/input/omoide`.

Uploads use the exact name `<attempt UUID>.jpg` or `.png`. That exact file is
removed after the attempt finishes or fails. The bridge never accepts a path
from the Omoide client and never follows a filename returned by Comfy when
performing cleanup.
