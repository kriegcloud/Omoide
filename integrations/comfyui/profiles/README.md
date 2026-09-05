# Omoide image-repair workflows

Repair workflow JSON is authored and validated on the ComfyUI host, not stored
in this repository. Export each graph in API format to
`/home/elpresidank/ai/workflows/comfy/<profile-id>/workflow.api.json`, calculate
its SHA-256, and declare the exact input/output node IDs in bridge config. Every
graph has a `LoadImage` input and a selected `SaveImage` output.

## `omoide-remove-text-v1`

Detect visible text with OCR, convert the detected regions to a mask, and use
LaMa inpainting to reconstruct those pixels. Save one repaired PNG or JPEG.

## `omoide-upscale-v1`

Perform 2x photographic restoration with SUPIR, or CodeFormer followed by
ESRGAN. Preserve aspect ratio and save one restored PNG or JPEG.

## `omoide-remove-people-v1`

Accept a JSON string input shaped as
`{"subject_box":{"x":0,"y":0,"width":100,"height":100}}`. Coordinates are
source-image pixels after EXIF orientation. Segment people outside that subject
face box, inpaint them, and save the repaired image through the configured
SaveImage node. Save the segmentation mask through a second SaveImage node for
dataset use; the bridge intentionally ignores that second node.
