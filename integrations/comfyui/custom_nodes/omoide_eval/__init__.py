"""Omoide evaluation helpers for ComfyUI.

Dependency-free nodes that let an allowlisted evaluation workflow take its
prompt, sampler settings and LoRA from one bounded JSON parameters string.
"""

from .nodes import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
