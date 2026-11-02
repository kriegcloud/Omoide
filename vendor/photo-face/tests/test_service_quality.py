from types import SimpleNamespace
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
import unittest
import torch

from beep_photo_face.backends.adaface_kprpe import AdaFaceAnalysis
from beep_photo_face import service


def analysis(mirror=True):
    seen = []
    def align(images, priors):
        seen.append(images.clone())
        merged = torch.zeros((len(images), 16))
        merged[:, 5] = 20
        merged[:, 6:] = len(seen)
        return None, None, None, merged, None
    def recognize(images, landmarks):
        # Distinct raw norms catch normalization before summing.
        return torch.tensor([[3., 0.] if x[0, 0] == 1 else [0., 4.] for x in landmarks])
    obj = AdaFaceAnalysis(None, align, recognize, None, torch, 'cpu', 16, mirror_tta=mirror)
    return obj, seen


def test_mirror_raw_sum_and_original_norm():
    obj, seen = analysis()
    crop = np.arange(112*112*3, dtype=np.uint8).reshape(112,112,3)
    result = obj._embed([crop])[0]
    np.testing.assert_allclose(result.embedding, [.6, .8])
    assert result.embedding_norm == 3
    # Width mirror, with existing aligner BGR conversion retained.
    np.testing.assert_allclose(seen[1].numpy(), seen[0].flip(3).numpy(), atol=2e-5)
    rgb = np.flip(crop[:, :, ::-1], axis=1).copy()
    expected = torch.nn.functional.interpolate(torch.from_numpy(rgb).permute(2,0,1)[None].float()/127.5-1,
                                               (160,160), mode='bilinear', align_corners=True).flip(1)
    np.testing.assert_allclose(seen[1], expected, atol=2e-5)


def test_no_mirror():
    obj, seen = analysis(False)
    result = obj._embed([np.zeros((112,112,3), dtype=np.uint8)])[0]
    np.testing.assert_allclose(result.embedding, [1,0])
    assert result.embedding_norm == 3
    assert len(seen) == 1


def test_payload_quality_demographics():
    face = SimpleNamespace(bbox=[0,0,10,10], kps=np.zeros((5,2)), det_score=.9,
                           embedding=np.ones(2), embedding_norm=7., sex='M', age=31, sex_score=.8)
    result = service._face_payload(face)
    assert result['embeddingNorm'] == 7
    assert (result['sex'], result['age'], result['sexScore']) == ('M',31,.8)


def test_cli_defaults_and_options():
    args = ['--socket','/tmp/x','--model-root','/tmp','--detector','/tmp/d','--aligner','/tmp/a','--recognizer','/tmp/r']
    default = service.build_parser().parse_args(args)
    assert default.mirror_tta is True
    assert default.detector_device == 'cpu'
    explicit = service.build_parser().parse_args(args + ['--no-mirror-tta','--detector-device','gpu','--detector-fp16','--genderage','/tmp/g'])
    assert not explicit.mirror_tta
    assert explicit.detector_fp16
    assert explicit.genderage == Path('/tmp/g')


def test_attribute_api_keeps_logits_and_same_detection():
    from beep_photo_face.backends.service_models import GenderAge
    class Attribute:
        def __init__(self, session): self.session = session
        def get(self, image, face):
            assert face.bbox.tolist() == [1,2,11,22]
            assert face.kps.shape == (5,2)
            pred = self.session.run(['out'], {'in': image})[0][0]
            face['gender'], face['age'] = int(np.argmax(pred[:2])), int(round(pred[2]*100))
    session = Mock()
    session.run.return_value = [np.array([[1., 3., .32]])]
    attr = Attribute(session)
    demographics = GenderAge(attr)
    face = SimpleNamespace(bbox=np.array([1,2,11,22]), kps=np.zeros((5,2)))
    demographics.apply(np.zeros((30,30,3)), [face])
    assert (face.sex, face.age) == ('M', 32)
    np.testing.assert_allclose(face.sex_score, 1/(1+np.exp(-2)))
    assert session.run.call_count == 1


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(unittest.FunctionTestCase(value) for name, value in globals().items() if name.startswith("test_"))


def test_torch_session_matches_scrfd_postprocessing():
    from insightface.model_zoo.scrfd import SCRFD
    from beep_photo_face.backends.service_models import TorchDetectorSession
    # Recorded-output fixture: overlapping anchors, two faces, and all 3 FPN
    # levels exercise decoding, thresholding, NMS and max_num selection.
    outputs = []
    for stride in (8,16,32):
        score = np.zeros(((640//stride)**2*2, 1), dtype=np.float32)
        score[100:103] = [[.95],[.91],[.7]]
        outputs.append(score)
    outputs += [np.ones((len(x),4), dtype=np.float32)*2 for x in outputs[:3]]
    outputs += [np.tile(np.arange(10, dtype=np.float32), (len(x),1))/10 for x in outputs[:3]]
    class Session:
        def get_inputs(self): return [SimpleNamespace(name='image',shape=[1,3,'height','width'])]
        def get_outputs(self): return [SimpleNamespace(name=str(i),shape=x.shape) for i,x in enumerate(outputs)]
        def set_providers(self, providers): pass
        def run(self, names, inputs): return outputs
    class Module:
        def __call__(self, blob):
            assert tuple(blob.shape) == (1,3,640,640)
            return [torch.from_numpy(value) for value in outputs]
    reference = SCRFD(session=Session())
    converted = SCRFD(session=TorchDetectorSession(Session(), Module(), torch, 'cpu'))
    for detector in (reference, converted):
        detector.prepare(-1, input_size=(640,640), det_thresh=.5)
    for shape in ((471,813,3), (900,310,3)):
        image = np.zeros(shape, dtype=np.uint8)
        for max_num in (0,2):
            expected = reference.detect(image, max_num=max_num)
            actual = converted.detect(image, max_num=max_num)
            for left,right in zip(expected,actual): np.testing.assert_array_equal(left,right)
    try:
        converted.session.run(['0'], {'image': np.zeros((1,3,320,320),dtype=np.float32)})
    except ValueError:
        pass
    else:
        raise AssertionError('variable shapes must be refused')


def test_benchmark_deviation_matches_reordered_boxes():
    import importlib.util
    spec = importlib.util.spec_from_file_location('bench', Path(__file__).resolve().parents[3] / 'scripts/bench_face_detector.py')
    bench = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bench)
    cpu = np.array([[0,0,10,10,.9],[20,20,40,40,.8]],dtype=np.float32)
    gpu = cpu[::-1].copy()
    gpu[0,0] += .125
    gpu[0,4] += .001
    box, score, mismatch = bench.deviation(cpu,gpu)
    assert box == .125
    np.testing.assert_allclose(score,.001,atol=1e-7)
    assert mismatch == 0
    assert bench.deviation(cpu,gpu[:1])[2] == 1


def test_genderage_loader_caps_cpu_threads():
    from beep_photo_face.backends.service_models import GenderAge
    with patch('onnxruntime.InferenceSession') as session, patch('insightface.model_zoo.attribute.Attribute') as attribute:
        attribute.return_value.taskname = 'genderage'
        GenderAge.load(Path('/models/genderage.onnx'))
        assert session.call_args.kwargs['sess_options'].intra_op_num_threads == 8
        assert session.call_args.kwargs['providers'] == ['CPUExecutionProvider']
        assert attribute.call_args.kwargs['session'] is session.return_value


def test_rejected_original_has_no_norm_and_does_not_embed_mirror():
    obj, seen = analysis()
    def reject(images, priors):
        merged = torch.zeros((len(images),16))
        merged[:,4] = 20
        return None,None,None,merged,None
    obj.aligner = reject
    obj.recognizer = Mock(side_effect=AssertionError('rejected faces must not embed'))
    result = obj._embed([np.zeros((112,112,3),dtype=np.uint8)])[0]
    assert result.embedding is None
    assert result.embedding_norm is None


def test_recorded_onnx_outputs_match_torch_adapter():
    from insightface.model_zoo.scrfd import SCRFD
    from beep_photo_face.backends.service_models import TorchDetectorSession
    with np.load(Path(__file__).parent / 'fixtures/det_10g_synthetic.npz') as fixture:
        outputs = [fixture[str(i)] for i in range(9)]
    class Session:
        def get_inputs(self): return [SimpleNamespace(name='input.1',shape=[1,3,'height','width'])]
        def get_outputs(self): return [SimpleNamespace(name=str(i),shape=x.shape) for i,x in enumerate(outputs)]
        def run(self, names, inputs): return outputs
        def set_providers(self, providers): pass
    class Module:
        def __call__(self, blob): return [torch.from_numpy(output) for output in outputs]
    cpu = SCRFD(session=Session())
    gpu = SCRFD(session=TorchDetectorSession(Session(),Module(),torch,'cpu'))
    for model in (cpu,gpu): model.prepare(-1,input_size=(640,640),det_thresh=.001)
    for shape in ((640,640,3),(450,900,3)):
        expected = cpu.detect(np.zeros(shape,dtype=np.uint8), max_num=32)
        actual = gpu.detect(np.zeros(shape,dtype=np.uint8), max_num=32)
        assert len(expected[0]) > 0
        for left,right in zip(expected,actual): np.testing.assert_array_equal(left,right)
