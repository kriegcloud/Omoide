"""On-demand frames in the same pixel space as video face detection."""
import subprocess
from pathlib import Path

import cv2
import numpy as np

from app.ffmpeg import SINGLE_FRAME_THREAD_ARGS, ensure_ffmpeg_available
from app.frames import fit_processing_size
from app.subprocess_helpers import run_silent


def extract_face_frame(source: Path, timestamp: float, target: Path) -> None:
    """Accurately seek, decode and cap a frame using detector integer sizing.

    ffmpeg's input seek decodes forward to the requested time (no keyframe-only
    seek). Leave autorotation enabled, just as the processing frame extractor.
    The caller supplies a private temporary path and publishes it atomically.
    """
    binary = ensure_ffmpeg_available()
    if not binary:
        raise RuntimeError('ffmpeg unavailable')
    result = run_silent(
        [str(binary), '-hide_banner', '-loglevel', 'error',
         *SINGLE_FRAME_THREAD_ARGS, '-ss', str(timestamp), '-i', str(source),
         '-frames:v', '1', *SINGLE_FRAME_THREAD_ARGS,
         '-f', 'image2pipe', '-strict', '-1', '-vcodec', 'mjpeg', '-'],
        check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20,
    )
    if result.returncode or not result.stdout:
        raise RuntimeError('No video frame decoded')
    frame = cv2.imdecode(np.frombuffer(result.stdout, np.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        raise RuntimeError('Invalid decoded frame')
    # fit_processing_size is channel independent; retaining BGR here saves a copy.
    frame = fit_processing_size(frame)
    if not cv2.imwrite(str(target), frame):
        raise RuntimeError('Could not write video frame')
