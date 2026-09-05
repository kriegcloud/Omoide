"""ComfyUI V3 node registration surface for Omoide annotations."""

from __future__ import annotations

from pathlib import Path

import folder_paths
from comfy_api.latest import io, ui

from .core import MODEL_DIRECTORY_NAME, annotate_numpy_batch


class OmoideWDEva02LargeTaggerV3(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="OmoideWDEva02LargeTaggerV3",
            display_name="Omoide WD EVA02-Large Tagger v3",
            category="Omoide/annotation",
            description=(
                "Runs the revision-pinned WD EVA02-Large v3 ONNX model on CPU. "
                "Emits canonical JSON with all rating/general/character scores; "
                "character candidates are never added to training_tags."
            ),
            inputs=[io.Image.Input("image")],
            outputs=[
                io.String.Output(
                    "annotation_json",
                    display_name="Canonical annotation JSON",
                    is_output_list=True,
                )
            ],
            has_intermediate_output=True,
        )

    @classmethod
    def execute(cls, image) -> io.NodeOutput:
        image_batch = image.detach().cpu().numpy()
        model_directory = (
            Path(folder_paths.models_dir)
            / "annotators"
            / "omoide"
            / MODEL_DIRECTORY_NAME
        )
        annotation_json_documents, preview = annotate_numpy_batch(
            image_batch,
            model_directory,
        )
        return io.NodeOutput(annotation_json_documents, ui=ui.PreviewText(preview))
