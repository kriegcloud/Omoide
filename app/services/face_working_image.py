"""Pure image preparation shared by face extraction and read-only experiments."""

import cv2
import numpy as np
from PIL import Image, ImageOps

MAX_DET_DIM = 1280


def face_scene_rgb(scene: Image.Image) -> np.ndarray:
    """Apply the face pipeline's EXIF orientation and RGB conversion."""
    return np.array(ImageOps.exif_transpose(scene).convert("RGB"))


def face_working_image(scene: np.ndarray) -> np.ndarray:
    """Use the pipeline's area resampling and integer truncation, without upscale."""
    h_orig, w_orig = scene.shape[:2]
    if max(h_orig, w_orig) > MAX_DET_DIM:
        scale = MAX_DET_DIM / max(h_orig, w_orig)
        return cv2.resize(
            scene,
            (int(w_orig * scale), int(h_orig * scale)),
            interpolation=cv2.INTER_AREA,
        )
    return scene
