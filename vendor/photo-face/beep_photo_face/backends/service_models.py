"""Session adapters retaining InsightFace preprocessing and postprocessing."""
from __future__ import annotations

from typing import Any
import numpy as np


class TorchDetectorSession:
    """ORT-compatible outputs from a fixed-shape torch SCRFD graph."""

    def __init__(self, session: Any, module: Any, torch: Any, device: Any,
                 fp16: bool = False):
        self.session, self.module, self.torch = session, module, torch
        self.device = device
        self.dtype = torch.float16 if fp16 else torch.float32
        self.output_names = [output.name for output in session.get_outputs()]

    def __getattr__(self, name):
        return getattr(self.session, name)

    def run(self, output_names, inputs):
        blob = next(iter(inputs.values()))
        if blob.shape != (1, 3, 640, 640):
            raise ValueError('GPU SCRFD requires a fixed 1x3x640x640 input')
        with self.torch.inference_mode():
            outputs = self.module(self.torch.from_numpy(blob).to(device=self.device, dtype=self.dtype))
        if not isinstance(outputs, (list, tuple)):
            outputs = [outputs]
        if len(outputs) != len(self.output_names):
            raise ValueError('SCRFD torch graph returned an unexpected output count')
        values = {name: value.detach().float().cpu().numpy()
                  for name, value in zip(self.output_names, outputs)}
        return [values[name] for name in (output_names or self.output_names)]


def enable_torch_detector(detector, path, torch, device, fp16=False):
    from onnx2torch import convert
    module = convert(str(path)).eval().to(
        device=device, dtype=torch.float16 if fp16 else torch.float32)
    detector.session = TorchDetectorSession(detector.session, module, torch, device, fp16)
    # InsightFace 1.0.1 supports multiple resolutions; force exactly one.
    detector.input_size = (640, 640)
    detector.input_sizes = [(640, 640)]
    return detector


class _CaptureSession:
    def __init__(self, session):
        self.session = session
        self.outputs = None

    def __getattr__(self, name):
        return getattr(self.session, name)

    def run(self, *args, **kwargs):
        self.outputs = self.session.run(*args, **kwargs)
        return self.outputs


class GenderAge:
    def __init__(self, attribute):
        self.attribute = attribute
        self.session = _CaptureSession(attribute.session)
        attribute.session = self.session

    @classmethod
    def load(cls, path):
        import onnxruntime as ort
        from insightface.model_zoo.attribute import Attribute
        options = ort.SessionOptions()
        options.intra_op_num_threads = 8
        session = ort.InferenceSession(str(path), sess_options=options,
                                       providers=['CPUExecutionProvider'])
        attribute = Attribute(str(path), session=session)
        if attribute.taskname != 'genderage':
            raise ValueError('--genderage must be a genderage model')
        return cls(attribute)

    def apply(self, image, faces):
        from insightface.app.common import Face
        for face in faces:
            detected = Face(bbox=face.bbox, kps=face.kps)
            self.session.outputs = None
            self.attribute.get(image, detected)
            logits = np.asarray(self.session.outputs[0][0][:2], dtype=np.float64)
            probabilities = np.exp(logits - logits.max())
            probabilities /= probabilities.sum()
            face.sex = 'M' if detected.gender == 1 else 'F'
            face.age = int(detected.age)
            face.sex_score = float(probabilities[int(detected.gender)])
