# Omoide image-repair workflows

The bridge executes allowlisted API-format graphs installed at
`/home/elpresidank/ai/workflows/comfy/<profile-id>/workflow.api.json`. Declare
that absolute path, its SHA-256 and the exact input/output node IDs in bridge
config. Every image-repair graph has a `LoadImage` input and a selected
`SaveImage.images` output. The bridge ignores other SaveImage outputs.

Most repair graphs are authored and validated on the ComfyUI host. The SeedVR2
profile below also ships its pinned API graph in this directory so deployment
can install an exact, reviewable copy. Adding the config example does not
install the host graph or enable a running bridge.

## `omoide-remove-text-v1`

Detect visible text with OCR, convert the detected regions to a mask, and use
LaMa inpainting to reconstruct those pixels. Save one repaired PNG or JPEG.

## `omoide-upscale-v1`

The workstation's existing faithful 2x workflow uses 4x-foolhardy-Remacri ESRGAN
followed by a 0.5x Lanczos resize. It remains the default upscale profile.

## `omoide-upscale-seedvr2-v1`

Generative restoration with SeedVR2 7B Sharp FP16. The API graph is copied
byte-for-byte from the validated `seedvr2-restore-upscale` reference packet:
SHA-256 `c42526b1549c4449e1e6988cfdaf543164f54cec84e4a5a580639000a4b62268`.
The packet records validation on 2026-08-25 with ComfyUI v0.33.4, a 1024-square
source producing a 2048-square output on one non-display R9700. This historical
proof does not validate the bridge deployment or a 4x run. The bridge retains
its current v0.34.0 executor revision/artifact readiness checks; integration on
that runtime still needs an authorized live proof.

Install `omoide-upscale-seedvr2-v1/workflow.api.json` at the host path above and
add the matching entry from `bridge/config.example.json` to the host config.
The host config must declare `output_directory` for image outputs. Bindings are
fixed in code and cannot be changed through request params or config:

| Purpose | Node | Class | Input/output |
| --- | --- | --- | --- |
| Staged source | `1` | `LoadImage` | `image` |
| Scale | `3` | `ImageScaleBy` | `scale_by` |
| Final output | `12` | `SaveImage` | `images` |

Repair protocol params accept only `{"scale":2}` or `{"scale":4}`; omitted
params and `{}` use 2. Scale is written as a numeric value directly to node 3,
with Lanczos preserved. Unknown keys, booleans and other scale values fail
before uploading the source. Other nodes remain pinned: source alpha,
SeedVR2Preprocess, tiled 512px VAE with 128px overlap, one Euler/simple sampling
step, and LAB correction. The full graph and both model files are digest-pinned.
Model source revision: `Comfy-Org/SeedVR2` at
`673340c8a66db62b84e4099def7d01d337ae12dc`.

SeedVR2 invents fine detail; review faces, hands, clothing boundaries, jewelry,
logos and text against the original. It is a heavy VRAM workload and the source
packet recommends a fresh ComfyUI process after other model families. Selecting
this profile never restarts ComfyUI automatically. A 4x run produces sixteen
times the source pixel count and four times the 2x pixel count; treat it as a
separate supervised experiment. Use the existing Remacri profile for source
fidelity. No custom nodes are required by this graph.

## `omoide-remove-people-v1`

Accept a JSON string input shaped as
`{"subject_box":{"x":0,"y":0,"width":100,"height":100}}`. Coordinates are
source-image pixels after EXIF orientation. Segment people outside that subject
face box, inpaint them, and save the repaired image through the configured
SaveImage node. Save the segmentation mask through a second SaveImage node for
dataset use; the bridge intentionally ignores that second node.
