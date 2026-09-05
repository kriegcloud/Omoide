"""Omoide annotation nodes for ComfyUI."""

from __future__ import annotations

from typing_extensions import override

from comfy_api.latest import ComfyExtension, io

from .nodes import OmoideWDEva02LargeTaggerV3


class OmoideAnnotationExtension(ComfyExtension):
    @override
    async def get_node_list(self) -> list[type[io.ComfyNode]]:
        return [OmoideWDEva02LargeTaggerV3]


async def comfy_entrypoint() -> OmoideAnnotationExtension:
    return OmoideAnnotationExtension()


__all__ = ["OmoideAnnotationExtension", "comfy_entrypoint"]
