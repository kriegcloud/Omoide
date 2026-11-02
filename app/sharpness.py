"""Canonical sharpness: oriented full image, RGB, 512px, CLAHE, Laplacian."""
import cv2
import numpy as np
from PIL import Image, ImageOps

# Downscale to this before computing Laplacian — enough for blur detection, much faster.
_MAX_SIZE = 512


def laplacian_score(arr: np.ndarray) -> float:
    """Compute Laplacian variance on a grayscale or RGB uint8 array.

    CLAHE normalises local contrast before the Laplacian so that dark or
    low-key images score based on edge sharpness, not absolute brightness.
    Without this, a dark-but-sharp photo scores as low as a blurry one.
    """
    h, w = arr.shape[:2]
    if max(h, w) > _MAX_SIZE:
        scale = _MAX_SIZE / max(h, w)
        arr = cv2.resize(
            arr, (max(1, int(w * scale)), max(1, int(h * scale))),
            interpolation=cv2.INTER_AREA,
        )
    if arr.ndim == 3:
        gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
    else:
        gray = arr
    gray = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
    return float(cv2.Laplacian(gray, cv2.CV_32F).var())


def score_image_path(path) -> float:
    """Use the original full decode; JPEG draft changes the score scale."""
    with Image.open(path) as image:
        return laplacian_score(np.asarray(ImageOps.exif_transpose(image).convert("RGB")))
