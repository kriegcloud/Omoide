"""Host-only fixed-shape SCRFD CPU vs torch GPU benchmark (no recognition).

Example: venv-rocm72/bin/python scripts/bench_face_detector.py /path/to/images -n 50
Weights must already exist locally. Includes InsightFace resize/decode/NMS time,
excludes image decoding and startup; GPU outputs synchronize on transfer to CPU.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from time import perf_counter

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'vendor/photo-face'))


def deviation(cpu, gpu):
    """Pair detections by closest box so score-order ties do not distort error."""
    mismatch = abs(len(cpu) - len(gpu))
    remaining = list(range(len(gpu)))
    box_error = score_error = 0.0
    for expected in cpu:
        if not remaining:
            break
        index = min(remaining, key=lambda i: float(np.max(np.abs(expected[:4]-gpu[i,:4]))))
        remaining.remove(index)
        box_error = max(box_error, float(np.max(np.abs(expected[:4]-gpu[index,:4]))))
        score_error = max(score_error, float(abs(expected[4]-gpu[index,4])))
    return box_error, score_error, mismatch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('folder', type=Path)
    parser.add_argument('-n', type=int, default=50)
    parser.add_argument('--detector', type=Path, default=Path('/home/elpresidank/.local/share/omoide-portal/models/adaface-kprpe/detector/det_10g.onnx'))
    parser.add_argument('--device', type=int, default=0)
    parser.add_argument('--fp16', action='store_true')
    parser.add_argument('--warmup', type=int, default=3)
    args = parser.parse_args()
    if args.n < 1 or args.warmup < 0:
        parser.error('n must be positive and warmup nonnegative')
    os.environ.setdefault('MIOPEN_FIND_MODE', 'FAST')
    import torch
    import onnxruntime as ort
    from insightface.model_zoo.scrfd import SCRFD
    from beep_photo_face.backends.service_models import enable_torch_detector

    torch.set_num_threads(8)
    device = torch.device(f'cuda:{args.device}')
    if not torch.cuda.is_available():
        parser.error('GPU benchmark requires CUDA/ROCm')
    options = ort.SessionOptions()
    options.intra_op_num_threads = 8
    def detector():
        session = ort.InferenceSession(str(args.detector), sess_options=options, providers=['CPUExecutionProvider'])
        model = SCRFD(str(args.detector), session=session)
        model.prepare(-1, input_size=(640,640), det_thresh=.5)
        return model
    cpu, gpu = detector(), detector()
    enable_torch_detector(gpu, args.detector, torch, device, args.fp16)
    files = [p for p in sorted(args.folder.rglob('*')) if p.suffix.lower() in {'.jpg','.jpeg','.png','.webp','.bmp'}][:args.n]
    if not files:
        parser.error('folder contains no supported images')
    first = cv2.imread(str(files[0]))
    if first is None:
        parser.error('first image could not be decoded')
    for _ in range(args.warmup):
        cpu.detect(first, max_num=32)
        gpu.detect(first, max_num=32)
    timings = [[],[]]
    box_error = score_error = 0.0
    mismatches = unreadable = 0
    for path in files:
        image = cv2.imread(str(path))
        if image is None:
            unreadable += 1
            continue
        results = []
        for index, model in enumerate((cpu,gpu)):
            started = perf_counter()
            boxes, _ = model.detect(image, max_num=32)
            timings[index].append((perf_counter()-started)*1000)
            results.append(boxes)
        box, score, mismatch = deviation(*results)
        box_error, score_error = max(box_error,box), max(score_error,score)
        mismatches += mismatch
    if not timings[0]:
        parser.error('no readable images')
    print(json.dumps(dict(images=len(timings[0]), unreadable=unreadable,
                          cpuMsPerImage=float(np.mean(timings[0])),
                          gpuMsPerImage=float(np.mean(timings[1])),
                          maxBoxDeviation=box_error, maxScoreDeviation=score_error,
                          unmatchedDetections=mismatches, precision='fp16' if args.fp16 else 'fp32',
                          detSize=640), indent=2))
    if mismatches:
        raise SystemExit(2)


if __name__ == '__main__':
    main()
