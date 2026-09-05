"""ComfyUI nodes for Omoide repair workflows."""

from __future__ import annotations

import json


def _parse_subject_box(params_json: str) -> tuple[float, float, float, float] | None:
    """Return (x1, y1, x2, y2) from ``{"subject_box": {x, y, width, height}}``."""
    try:
        payload = json.loads(params_json or "{}")
    except json.JSONDecodeError:
        return None
    box = payload.get("subject_box") if isinstance(payload, dict) else None
    if not isinstance(box, dict):
        return None
    try:
        x = float(box["x"])
        y = float(box["y"])
        width = float(box["width"])
        height = float(box["height"])
    except (KeyError, TypeError, ValueError):
        return None
    if width <= 0 or height <= 0:
        return None
    return (x, y, x + width, y + height)


def _intersects(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


class OmoideSegsOutsideBox:
    """Keep only the SEGS whose bounding box does not touch the subject box.

    The subject box is the face of the person the dataset belongs to, given in
    source pixels as ``{"subject_box": {"x", "y", "width", "height"}}``. The
    box is expanded by ``margin`` so a person segment that merely grazes the
    face is still treated as the subject.
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "segs": ("SEGS",),
                "params_json": ("STRING", {"default": "{}", "multiline": True}),
                "margin": ("INT", {"default": 32, "min": 0, "max": 4096, "step": 1}),
            }
        }

    RETURN_TYPES = ("SEGS", "INT", "INT")
    RETURN_NAMES = ("segs", "kept", "dropped")
    FUNCTION = "filter"
    CATEGORY = "Omoide/repair"

    def filter(self, segs, params_json: str, margin: int):
        shape, items = segs[0], list(segs[1])
        subject = _parse_subject_box(params_json)
        if subject is None:
            # Without a subject every person is a candidate for removal.
            return (segs, len(items), 0)
        expanded = (
            subject[0] - margin,
            subject[1] - margin,
            subject[2] + margin,
            subject[3] + margin,
        )
        kept = []
        dropped = 0
        for seg in items:
            bbox = getattr(seg, "bbox", None)
            if bbox is None or len(bbox) < 4:
                kept.append(seg)
                continue
            seg_box = (float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3]))
            if _intersects(seg_box, expanded):
                dropped += 1
                continue
            kept.append(seg)
        return ((shape, kept), len(kept), dropped)


NODE_CLASS_MAPPINGS = {"OmoideSegsOutsideBox": OmoideSegsOutsideBox}
NODE_DISPLAY_NAME_MAPPINGS = {"OmoideSegsOutsideBox": "Omoide: SEGS outside subject box"}
