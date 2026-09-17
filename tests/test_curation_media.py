"""Format, colour, frame and decoder-lineage cases for still materialization.

Every fixture is synthetic: Pillow-drawn images and a generated MJPG video. No
private media, no network, no model and no generative processing is involved.
Each accepted case asserts deterministic bytes (decoded twice), a `color`
provenance record, and an `uncertainties` entry wherever colour accuracy is not
guaranteed. Each refused case asserts its exact refusal code.
"""
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from PIL import Image, ImageCms
from sqlmodel import Session, SQLModel, create_engine, select

from app.api.curation import router
from app.config import settings
from app.curation_models import CurationArtifact, CurationDataset, CurationOperation, CurationSource
from app.database import get_session
from app.schemas.curation import (CaptionInput, ExportInput, ImageFrameSelector, MaterializeInput,
    RepairSelector, ReviewInput, VideoFrameSelector)
from app.services import curation_media as media
from app.services.curation_artifacts import TRANSFORM, normalized
from app.services.curation_fixtures import create_fixture_dataset
from app.services.curation_plans import add_caption, materialize, review
from app.services.curation_policy import digest
from app.services.frozen_exports import admit_export, execute_export

try:  # Optional in principle; present and exercised on this workstation.
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None


def encoded(mode, format_, size=(6, 4), colour=None, **kwargs) -> bytes:
    image = Image.new(mode, size) if colour is None else Image.new(mode, size, colour)
    if mode == 'P':
        image.putpalette([0, 0, 0] + [200, 40, 90] * 255)
        image.paste(1, (0, 0, size[0], size[1]))
    stream = io.BytesIO()
    image.save(stream, format=format_, **kwargs)
    return stream.getvalue()


def animation(format_, count=3, **kwargs) -> bytes:
    frames = [Image.new('RGB', (8, 6), shade) for shade in ('#ff0000', '#00ff00', '#0000ff')][:count]
    stream = io.BytesIO()
    frames[0].save(stream, format=format_, save_all=True, append_images=frames[1:], **kwargs)
    return stream.getvalue()


def srgb_profile() -> bytes:
    return ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB')).tobytes()


def write_video(path: Path, frames: int = 8, fps: float = 4.0, size=(64, 48)) -> Path:
    """Deterministic MJPG fixture; each frame is one flat, distinguishable shade."""
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*'MJPG'), fps, size)
    if not writer.isOpened():  # pragma: no cover - would be an environment fault
        raise unittest.SkipTest('OpenCV cannot write the MJPG fixture here')
    for index in range(frames):
        writer.write(np.full((size[1], size[0], 3), (index * 30) % 255, dtype=np.uint8))
    writer.release()
    return path


class RefusalMixin:
    def expect_code(self, code, function, *args, **kwargs):
        with self.assertRaises(HTTPException) as raised:
            function(*args, **kwargs)
        self.assertEqual(raised.exception.detail['code'], code)
        return raised.exception

    def expect_code_in(self, codes, function, *args, **kwargs):
        with self.assertRaises(HTTPException) as raised:
            function(*args, **kwargs)
        self.assertIn(raised.exception.detail['code'], codes)
        return raised.exception

    def accepted(self, data, selector=None):
        """Decode twice: identical bytes, a colour record and a frame record."""
        first = normalized(data, selector)
        second = normalized(data, selector)
        self.assertEqual(first[0], second[0], 'artifact bytes are not deterministic')
        self.assertEqual(first[1], second[1], 'pixel digest is not deterministic')
        self.assertEqual(first[4], second[4], 'provenance is not deterministic')
        self.assertIn('color', first[4])
        self.assertIn('frame', first[4])
        self.assertTrue(first[4]['uncertainties'])
        return first


class StillFormatAndColourTests(RefusalMixin, unittest.TestCase):
    def test_transform_version_and_module_digests_participate_in_the_cache_key(self):
        self.assertEqual(TRANSFORM['version'], 'rgb-png-v2')
        self.assertEqual(len(TRANSFORM['code_sha256']), 64)
        self.assertEqual(len(TRANSFORM['media_code_sha256']), 64)
        source = Path(media.__file__).read_bytes()
        self.assertEqual(TRANSFORM['media_code_sha256'], digest(source))

    def test_previously_supported_untagged_still_keeps_its_exact_bytes(self):
        from PIL import ImageOps
        data = encoded('RGB', 'JPEG', (12, 8), '#3366aa')
        with Image.open(io.BytesIO(data)) as opened:
            legacy = ImageOps.exif_transpose(opened).convert('RGB')
            legacy.info.clear()
            stream = io.BytesIO()
            legacy.save(stream, format='PNG', compress_level=9, optimize=False)
        result = self.accepted(data)
        self.assertEqual(result[0], stream.getvalue())
        self.assertEqual(result[4]['color']['method'], 'untagged-assumed-srgb')
        self.assertIn('Untagged RGB is assumed sRGB; no color accuracy claim.', result[4]['uncertainties'])

    def test_grayscale_and_palette_declare_their_expansion(self):
        grayscale = self.accepted(encoded('L', 'PNG'))
        self.assertEqual(grayscale[4]['color']['input_mode'], 'L')
        self.assertFalse(grayscale[4]['color']['palette_expanded'])
        palette = self.accepted(encoded('P', 'PNG'))
        self.assertTrue(palette[4]['color']['palette_expanded'])
        self.assertTrue(any('Palette input was expanded' in note
                            for note in palette[4]['uncertainties']))
        with Image.open(io.BytesIO(palette[0])) as image:
            self.assertEqual(image.mode, 'RGB')
            self.assertEqual(np.asarray(image).reshape(-1, 3)[0].tolist(), [200, 40, 90])

    def test_sixteen_bit_reduction_is_exact_and_disclosed(self):
        image = Image.new('I;16', (4, 1))
        image.putdata([0, 257, 32896, 65535])
        stream = io.BytesIO()
        image.save(stream, format='PNG')
        result = self.accepted(stream.getvalue())
        record = result[4]['color']['bit_depth']
        self.assertEqual(record, {'input_mode': 'I;16', 'input_bits': 16, 'output_bits': 8,
                                  'method': media.BIT_DEPTH_METHOD})
        with Image.open(io.BytesIO(result[0])) as decoded:
            self.assertEqual(np.asarray(decoded).reshape(-1, 3)[:, 0].tolist(), [0, 1, 128, 255])
        self.assertTrue(any('16-bit integer input was reduced' in note
                            for note in result[4]['uncertainties']))

    def test_unsupported_bit_depths_are_refused_by_name(self):
        bilevel = io.BytesIO()
        Image.new('1', (4, 4)).save(bilevel, format='PNG')
        self.expect_code('unsupported_bit_depth', normalized, bilevel.getvalue())
        # 32-bit integer and float pages are reachable only with an explicit index.
        for mode in ('I', 'F'):
            stream = io.BytesIO()
            Image.new(mode, (4, 4)).save(stream, format='TIFF')
            self.expect_code('unsupported_bit_depth', normalized, stream.getvalue(),
                             ImageFrameSelector(kind='image_frame', index=0))

    def test_alpha_is_refused_unless_fully_opaque_and_flattening_is_disclosed(self):
        self.expect_code('unsupported_color_or_alpha', normalized, encoded('RGBA', 'PNG'))
        self.expect_code('unsupported_color_or_alpha', normalized,
                         encoded('RGBA', 'PNG', colour=(10, 20, 30, 128)))
        self.expect_code('unsupported_color_or_alpha', normalized,
                         encoded('LA', 'PNG', colour=(40, 0)))
        for data in (encoded('RGBA', 'PNG', colour=(10, 20, 30, 255)),
                     encoded('LA', 'PNG', colour=(40, 255))):
            result = self.accepted(data)
            self.assertEqual(result[4]['color']['alpha'],
                             {'input_had_alpha': True, 'fully_opaque': True,
                              'method': media.ALPHA_METHOD, 'composited_background': None})
            self.assertTrue(any('fully opaque alpha' in note for note in result[4]['uncertainties']))

    def test_colour_key_transparency_is_treated_as_alpha(self):
        image = Image.new('P', (4, 4))
        image.putpalette([0, 0, 0] + [1, 2, 3] * 255)
        stream = io.BytesIO()
        image.save(stream, format='PNG', transparency=0)
        self.expect_code('unsupported_color_or_alpha', normalized, stream.getvalue())

    def test_cmyk_conversion_is_disclosed_as_not_colour_managed(self):
        result = self.accepted(encoded('CMYK', 'JPEG', colour=(10, 20, 30, 40)))
        self.assertTrue(result[4]['color']['cmyk_converted'])
        self.assertEqual(result[4]['color']['method'], 'untagged-cmyk-naive-rgb')
        self.assertTrue(any('Untagged CMYK' in note for note in result[4]['uncertainties']))

    def test_embedded_icc_is_converted_to_srgb_with_a_recorded_profile_digest(self):
        profile = srgb_profile()
        result = self.accepted(encoded('RGB', 'PNG', colour='#22aa55', icc_profile=profile))
        record = result[4]['color']['icc']
        self.assertEqual(record['embedded_profile_sha256'], digest(profile))
        self.assertEqual(record['rendering_intent'], 'perceptual')
        self.assertEqual(record['rendering_intent_code'], 0)
        self.assertFalse(record['black_point_compensation'])
        self.assertEqual(record['littlecms2'], media.features.version('littlecms2'))
        self.assertEqual(result[4]['color']['method'], 'embedded-icc-to-srgb-perceptual')
        self.assertTrue(any('embedded ICC profile to sRGB' in note
                            for note in result[4]['uncertainties']))
        with Image.open(io.BytesIO(result[0])) as decoded:
            self.assertIsNone(decoded.info.get('icc_profile'))

    def test_invalid_icc_and_missing_imagecms_refuse_instead_of_assuming(self):
        self.expect_code('unsupported_icc', normalized, encoded('RGB', 'PNG', icc_profile=b'fake-profile'))
        tagged = encoded('RGB', 'PNG', icc_profile=srgb_profile())
        with patch.object(media, 'IMAGECMS', None):
            self.expect_code('icc_runtime_unavailable', normalized, tagged)
            # An untagged still is unaffected by the absent colour runtime.
            self.assertTrue(normalized(encoded('RGB', 'PNG'))[0])

    def test_heif_is_accepted_when_importable_and_named_when_not(self):
        if media.PILLOW_HEIF is None:  # pragma: no cover - present on this workstation
            self.skipTest('pillow-heif is not importable in this environment')
        media.PILLOW_HEIF.register_heif_opener()
        data = encoded('RGB', 'HEIF', (10, 10), '#123456')
        self.assertTrue(media._is_heif(data))
        result = self.accepted(data)
        self.assertEqual(result[4]['frame']['source_format'], 'HEIF')
        self.assertEqual(result[4]['runtimes']['pillow_heif'], media.PILLOW_HEIF.__version__)
        with patch.object(media, 'PILLOW_HEIF', None):
            self.expect_code('heif_runtime_unavailable', normalized, data)

    def test_multi_frame_inputs_refuse_until_one_frame_is_requested(self):
        gif = animation('GIF')
        webp = animation('WEBP', lossless=True, quality=100)
        tiff = animation('TIFF')
        self.expect_code('unsupported_format', normalized, gif)
        self.expect_code('unsupported_animation', normalized, webp)
        self.expect_code('unsupported_format', normalized, tiff)
        for data, index, shade in ((gif, 2, [0, 0, 255]), (webp, 1, [0, 255, 0]), (tiff, 0, [255, 0, 0])):
            selector = ImageFrameSelector(kind='image_frame', index=index)
            result = self.accepted(data, selector)
            self.assertEqual(result[4]['frame']['selected_index'], index)
            self.assertTrue(result[4]['frame']['explicitly_requested'])
            self.assertEqual(result[4]['frame']['frame_count'], 3)
            with Image.open(io.BytesIO(result[0])) as decoded:
                self.assertEqual(np.asarray(decoded).reshape(-1, 3)[0].tolist(), shade)
        self.expect_code('frame_index_out_of_range', normalized, gif,
                         ImageFrameSelector(kind='image_frame', index=3))

    def test_unsupported_and_corrupt_inputs_keep_their_codes(self):
        self.expect_code('unsupported_format', normalized, encoded('RGB', 'TIFF'))
        self.expect_code('unsupported_format', normalized, encoded('RGB', 'BMP'))
        self.expect_code('corrupt_or_unsupported_image', normalized, b'corrupt')
        self.expect_code('invalid_frame_selector', normalized, encoded('RGB', 'PNG'),
                         {'kind': 'unknown_frame'})

    def test_repair_and_mask_evidence_validates_but_never_materializes(self):
        selector = RepairSelector(kind='repair_evidence', operation='inpaint',
            mask={'mask_artifact_sha256': 'a' * 64, 'mask_pixel_sha256': 'b' * 64,
                  'coordinate_space': 'post_transform_artifact_pixels',
                  'space_width': 64, 'space_height': 48,
                  'regions': [{'x': 1, 'y': 2, 'width': 3, 'height': 4}]},
            workflow={'runtime_identifier': 'fixture-workflow', 'workflow_sha256': 'c' * 64,
                      'weights_sha256': 'd' * 64, 'prompt_sha256': 'e' * 64,
                      'negative_prompt_sha256': 'f' * 64, 'seed': 7, 'strength': '0.35'},
            comparison={'before_artifact_sha256': '0' * 64, 'after_artifact_sha256': '1' * 64,
                        'unmasked_comparison_sha256': '2' * 64,
                        'protected_detail_regions': [{'x': 0, 'y': 0, 'width': 8, 'height': 8}]})
        self.assertIsNone(selector.human_acceptance_of_exact_output_sha256)
        refusal = self.expect_code('generative_disabled', normalized, encoded('RGB', 'PNG'), selector)
        self.assertEqual(refusal.status_code, 403)
        source = SimpleNamespace(relative_path='still.png')
        dataset = SimpleNamespace(policy={'media': {'video_frame_materialization': True}})
        self.expect_code('generative_disabled', media.materialize_media, dataset, source,
                         encoded('RGB', 'PNG'), selector)

    def test_human_acceptance_of_generative_output_cannot_be_asserted(self):
        from pydantic import ValidationError
        payload = {'kind': 'repair_evidence', 'operation': 'inpaint',
                   'mask': {'mask_artifact_sha256': 'a' * 64, 'mask_pixel_sha256': 'b' * 64,
                            'coordinate_space': 'post_transform_artifact_pixels',
                            'space_width': 4, 'space_height': 4,
                            'regions': [{'x': 0, 'y': 0, 'width': 1, 'height': 1}]},
                   'workflow': {'runtime_identifier': 'x', 'workflow_sha256': 'c' * 64,
                                'weights_sha256': 'd' * 64, 'prompt_sha256': 'e' * 64,
                                'negative_prompt_sha256': 'f' * 64, 'seed': 1, 'strength': '1'},
                   'comparison': {'before_artifact_sha256': '0' * 64, 'after_artifact_sha256': '1' * 64,
                                  'unmasked_comparison_sha256': '2' * 64},
                   'human_acceptance_of_exact_output_sha256': '3' * 64}
        with self.assertRaises(ValidationError):
            RepairSelector.model_validate(payload)


@unittest.skipIf(cv2 is None, 'OpenCV is required to generate the video fixture')
class VideoFrameTests(RefusalMixin, unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='curation-video-tests-')
        self.root = Path(self.temp.name)
        self.path = write_video(self.root / 'clip.avi')
        self.data = self.path.read_bytes()
        self.source = SimpleNamespace(relative_path='clip.avi')
        self.enabled = SimpleNamespace(policy={'media': {'video_frame_materialization': True}})
        self.disabled = SimpleNamespace(policy={'media': {'video_frame_materialization': False}})

    def tearDown(self):
        self.temp.cleanup()

    def selector(self, pts='0.75', stream=0):
        return VideoFrameSelector(kind='video_frame', pts_seconds=pts, stream_index=stream)

    def test_policy_flag_default_off_and_selector_required(self):
        refusal = self.expect_code('video_materialization_disabled', media.materialize_media,
                                   self.disabled, self.source, self.data, self.selector())
        self.assertEqual(refusal.status_code, 403)
        self.expect_code('video_materialization_disabled', media.materialize_media,
                         SimpleNamespace(policy={}), self.source, self.data, self.selector())
        self.expect_code('video_frame_selector_required', media.materialize_media,
                         self.enabled, self.source, self.data, None)
        self.expect_code('video_frame_selector_required', media.materialize_media,
                         self.enabled, self.source, self.data,
                         ImageFrameSelector(kind='image_frame', index=0))
        self.expect_code('video_selector_not_supported_for_still', media.materialize_media,
                         self.enabled, SimpleNamespace(relative_path='still.png'),
                         encoded('RGB', 'PNG'), self.selector())

    def test_requested_timestamp_yields_that_exact_frame_deterministically(self):
        first = media.materialize_media(self.enabled, self.source, self.data, self.selector())
        second = media.materialize_media(self.enabled, self.source, self.data, self.selector())
        self.assertEqual(first[0], second[0])
        self.assertEqual(first[1], second[1])
        self.assertEqual((first[2], first[3]), (64, 48))
        with Image.open(io.BytesIO(first[0])) as decoded:
            self.assertEqual(np.asarray(decoded).reshape(-1, 3)[0].tolist(), [90, 90, 90])
        lineage = first[4]['decoder']
        self.assertEqual(lineage['tool'], 'ffmpeg')
        self.assertEqual(lineage['container_format'], 'avi')
        self.assertEqual(lineage['codec_name'], 'mjpeg')
        self.assertEqual(lineage['time_base'], '1/4')
        self.assertEqual(lineage['requested_pts_seconds'], '0.75')
        self.assertEqual(lineage['actual_pts'], 3)
        self.assertEqual(lineage['actual_pts_seconds'], '3/4')
        self.assertEqual(lineage['frame_index'], 3)
        self.assertEqual(lineage['stream_index'], 0)
        self.assertEqual(lineage['decoded_frame_count'], 8)
        self.assertEqual(lineage['source_sha256'], digest(self.data))
        self.assertEqual(lineage['selection_rule'], 'first-presentation-timestamp-at-or-after-request')
        self.assertTrue(lineage['ffmpeg_version'].startswith('ffmpeg version'))
        self.assertTrue(lineage['ffprobe_version'].startswith('ffprobe version'))
        self.assertIn('color_range', lineage)
        self.assertIn('color_primaries', lineage)
        self.assertEqual(first[4]['decoder_identity'],
                         {'tool': 'ffmpeg', 'version': lineage['ffmpeg_version']})
        self.assertTrue(any('One video frame was decoded by ffmpeg' in note
                            for note in first[4]['uncertainties']))

    def test_a_timestamp_between_frames_selects_the_next_presentation_timestamp(self):
        exact = media.materialize_media(self.enabled, self.source, self.data, self.selector('0.5'))
        between = media.materialize_media(self.enabled, self.source, self.data, self.selector('0.3'))
        self.assertEqual(exact[4]['decoder']['actual_pts'], 2)
        self.assertEqual(between[4]['decoder']['actual_pts'], 2)
        self.assertEqual(between[4]['decoder']['requested_pts_seconds'], '0.3')
        self.assertEqual(exact[0], between[0])
        self.assertNotEqual(exact[4]['decoder']['requested_pts_seconds'],
                            between[4]['decoder']['requested_pts_seconds'])

    def test_out_of_range_missing_stream_broken_container_and_extension(self):
        self.expect_code('video_timestamp_out_of_range', media.materialize_media,
                         self.enabled, self.source, self.data, self.selector('99'))
        self.expect_code('video_stream_not_found', media.materialize_media,
                         self.enabled, self.source, self.data, self.selector('0', 7))
        self.expect_code('video_probe_failed', media.materialize_media,
                         self.enabled, self.source, b'not a container at all', self.selector())
        self.expect_code('unsupported_video_extension', media.video_frame,
                         self.data, 'clip.mpg', self.selector())

    def test_absent_ffmpeg_refuses_by_name_and_a_slow_decode_is_bounded(self):
        with patch.object(media, 'FFMPEG_ENVIRONMENT', {'PATH': '/nonexistent', 'LC_ALL': 'C'}):
            self.expect_code('ffmpeg_runtime_unavailable', media.materialize_media,
                             self.enabled, self.source, self.data, self.selector())
        with patch.object(media.subprocess, 'run',
                          side_effect=subprocess.TimeoutExpired('ffprobe', 20)):
            self.expect_code('video_decode_timeout', media.materialize_media,
                             self.enabled, self.source, self.data, self.selector())

    def test_the_source_directory_is_never_written_and_temporaries_are_removed(self):
        before = sorted(os.listdir(self.root))
        existing = sorted(os.listdir(tempfile.gettempdir()))
        media.materialize_media(self.enabled, self.source, self.data, self.selector())
        self.assertEqual(sorted(os.listdir(self.root)), before)
        self.assertEqual(self.path.read_bytes(), self.data)
        leaked = [name for name in os.listdir(tempfile.gettempdir())
                  if name.startswith('curation-video-') and name not in existing]
        self.assertEqual(leaked, [])


class MaterializationIntegrationTests(RefusalMixin, unittest.TestCase):
    """The full admitted path: provenance, cache identity and export manifest."""

    def setUp(self):
        if cv2 is None:  # pragma: no cover
            self.skipTest('OpenCV is required to generate the video fixture')
        self.temp = tempfile.TemporaryDirectory(prefix='curation-media-integration-')
        self.root = Path(self.temp.name)
        self.sources = self.root / 'sources'
        self.store = self.root / 'store'
        self.sources.mkdir()
        self.store.mkdir()
        (self.sources / 'animation.gif').write_bytes(animation('GIF'))
        self.video_bytes = write_video(self.sources / 'clip.avi').read_bytes()
        self.engine = create_engine('sqlite:///' + str(self.root / 'media.sqlite'),
                                    connect_args={'timeout': 20, 'check_same_thread': False})
        SQLModel.metadata.create_all(self.engine)
        self.env = patch.dict(os.environ, {'OMOIDE_CURATION_FIXTURES': '1'})
        self.env.start()
        self.presentation = patch.object(settings.general, 'presentation_mode', False)
        self.presentation.start()
        with Session(self.engine) as session:
            seeded = create_fixture_dataset(session, source_root=self.sources, store_root=self.store,
                files=[{'relative_path': 'animation.gif', 'label': 'Animated fixture', 'group_id': 'still-group'},
                       {'relative_path': 'clip.avi', 'label': 'Video fixture', 'group_id': 'video-group'}],
                video_frame_materialization=True)
            self.dataset_id = seeded['dataset_id']
            self.agent = seeded['credentials']['agent']
            self.human = seeded['credentials']['fixture_human']
            rows = session.exec(select(CurationSource)).all()
            self.still_id = next(row.id for row in rows if row.relative_path == 'animation.gif')
            self.video_id = next(row.id for row in rows if row.relative_path == 'clip.avi')
        app = FastAPI()
        app.include_router(router, prefix='/api/curation')

        def session_override():
            with Session(self.engine) as session:
                yield session
        app.dependency_overrides[get_session] = session_override
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.presentation.stop()
        self.env.stop()
        self.engine.dispose()
        self.temp.cleanup()

    def request(self, source_id, key, frame=None, revision=0):
        return MaterializeInput(source_id=source_id, expected_revision=revision,
                                idempotency_key=key, frame=frame)

    def test_multi_frame_still_requires_the_explicit_index_through_the_service(self):
        with Session(self.engine) as session:
            self.expect_code('unsupported_format', materialize, session, self.agent, self.dataset_id,
                             self.request(self.still_id, 'no-frame-index'))
            operation = session.exec(select(CurationOperation)).one()
            self.assertEqual(operation.status, 'blocked')
            self.assertEqual(operation.error_code, 'unsupported_format')
            self.assertIsNone(operation.snapshot['frame'])

    def test_two_frames_of_one_source_are_two_artifacts_with_distinct_cache_keys(self):
        with Session(self.engine) as session:
            first = materialize(session, self.agent, self.dataset_id, self.request(
                self.still_id, 'frame-zero', ImageFrameSelector(kind='image_frame', index=0)))
            second = materialize(session, self.agent, self.dataset_id, self.request(
                self.still_id, 'frame-two', ImageFrameSelector(kind='image_frame', index=2),
                revision=first['revision']))
            self.assertEqual(len(second['items']), 2)
            artifacts = session.exec(select(CurationArtifact)).all()
            self.assertEqual(len({artifact.cache_key for artifact in artifacts}), 2)
            self.assertEqual(len({artifact.sha256 for artifact in artifacts}), 2)
            indexes = sorted(artifact.provenance['frame']['selected_index'] for artifact in artifacts)
            self.assertEqual(indexes, [0, 2])

    def test_video_frame_flows_through_admission_with_decoder_lineage_and_export(self):
        with Session(self.engine) as session:
            state = materialize(session, self.agent, self.dataset_id, self.request(
                self.video_id, 'video-frame-one',
                VideoFrameSelector(kind='video_frame', pts_seconds='0.75')))
            item = next(entry for entry in state['items'] if entry['source_id'] == self.video_id)
            artifact = session.get(CurationArtifact, item['artifact_id'])
            self.assertEqual(artifact.provenance['decoder']['actual_pts'], 3)
            self.assertEqual(artifact.provenance['decoder']['frame_index'], 3)
            self.assertTrue(artifact.provenance['decoder']['ffmpeg_version'])
            self.assertEqual(artifact.provenance['color']['method'], 'untagged-assumed-srgb')
            self.assertTrue(any('One video frame was decoded by ffmpeg' in note
                                for note in item['uncertainties']))
            # The still source stays unmaterialized and uncaptioned, so the freeze
            # is blocked whichever member the snapshot reaches first.
            blocked = self.expect_code_in({'unmaterialized_sources', 'caption_required'},
                admit_export, session, self.agent, self.dataset_id,
                ExportInput(expected_revision=state['revision'], idempotency_key='blocked-export'))
            self.assertEqual(blocked.status_code, 409)
            still = session.get(CurationSource, self.still_id)
            still.split = 'excluded'
            session.add(still)
            session.commit()
            state = add_caption(session, self.agent, self.dataset_id, CaptionInput(
                artifact_id=artifact.id, text='Flat grey video frame.\n',
                expected_revision=state['revision']))
            item = state['items'][0]
            state = review(session, self.human, self.dataset_id, ReviewInput(
                artifact_id=item['artifact_id'], caption_id=item['caption']['id'],
                asset_sha256=item['sha256'], caption_sha256=item['caption']['sha256'],
                decision='accept', expected_revision=state['revision']))
            operation_id = admit_export(session, self.agent, self.dataset_id, ExportInput(
                expected_revision=state['revision'], idempotency_key='video-export'))
            receipt = execute_export(session, self.agent, operation_id)
            self.assertEqual(receipt['status'], 'succeeded')
        manifest = json.loads((self.store / 'exports' / operation_id / 'manifest.json').read_bytes())
        member = manifest['members'][0]
        self.assertEqual(member['provenance']['decoder']['requested_pts_seconds'], '0.75')
        self.assertEqual(member['provenance']['decoder']['actual_pts_seconds'], '3/4')
        self.assertEqual(member['provenance']['decoder']['time_base'], '1/4')
        self.assertEqual(member['provenance']['decoder']['codec_name'], 'mjpeg')
        self.assertEqual(member['provenance']['color']['output_mode'], 'RGB')
        self.assertEqual(member['provenance']['transform']['version'], 'rgb-png-v2')
        self.assertTrue(any('video frame was decoded by ffmpeg' in note
                            for note in member['provenance']['uncertainties']))
        self.assertEqual((self.sources / 'clip.avi').read_bytes(), self.video_bytes)

    def test_repair_evidence_is_refused_before_any_operation_is_admitted(self):
        payload = {'source_id': self.still_id, 'expected_revision': 0,
                   'idempotency_key': 'repair-attempt',
                   'frame': {'kind': 'repair_evidence', 'operation': 'restoration',
                             'mask': {'mask_artifact_sha256': 'a' * 64, 'mask_pixel_sha256': 'b' * 64,
                                      'coordinate_space': 'post_transform_artifact_pixels',
                                      'space_width': 8, 'space_height': 6,
                                      'regions': [{'x': 0, 'y': 0, 'width': 2, 'height': 2}]},
                             'workflow': {'runtime_identifier': 'fixture', 'workflow_sha256': 'c' * 64,
                                          'weights_sha256': 'd' * 64, 'prompt_sha256': 'e' * 64,
                                          'negative_prompt_sha256': 'f' * 64, 'seed': 3,
                                          'strength': '0.2'},
                             'comparison': {'before_artifact_sha256': '0' * 64,
                                            'after_artifact_sha256': '1' * 64,
                                            'unmasked_comparison_sha256': '2' * 64}}}
        response = self.client.post(f'/api/curation/datasets/{self.dataset_id}/materialize',
                                    headers={'Authorization': 'Bearer ' + self.agent}, json=payload)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['detail']['code'], 'generative_disabled')
        with Session(self.engine) as session:
            self.assertEqual(session.exec(select(CurationOperation)).all(), [])
            self.assertEqual(session.exec(select(CurationArtifact)).all(), [])


class RegistrationMediaPolicyTests(RefusalMixin, unittest.TestCase):
    def setUp(self):
        if cv2 is None:  # pragma: no cover
            self.skipTest('OpenCV is required to generate the video fixture')
        self.temp = tempfile.TemporaryDirectory(prefix='curation-media-registration-')
        self.root = Path(self.temp.name)
        self.sources = self.root / 'sources'
        self.store = self.root / 'store'
        self.sources.mkdir()
        self.store.mkdir()
        write_video(self.sources / 'clip.avi')
        self.engine = create_engine('sqlite:///' + str(self.root / 'registration.sqlite'))
        SQLModel.metadata.create_all(self.engine)

    def tearDown(self):
        self.engine.dispose()
        self.temp.cleanup()

    def manifest(self, video_enabled=None):
        from app.services.source_locations import observe_source_volume
        observed = observe_source_volume(str(self.sources))
        data = (self.sources / 'clip.avi').read_bytes()
        payload = {'schema_version': 'omoide.source-registration/v1', 'name': 'Video fixture',
            'subject_id': 'synthetic-video-fixture', 'source_root': str(self.sources),
            'store_root': str(self.store), 'expected_filesystem_uuid': observed['uuid'],
            'expected_mountpoint': observed['mountpoint'],
            'attestation': {'operator_id': 'operator', 'attestation_id': 'attestation-1',
                            'statement': 'Synthetic fixture generated for this test.',
                            'evidence_reference': 'tests/test_curation_media.py',
                            'rights_to_curate': True, 'subject_identity_attested': True},
            'files': [{'relative_path': 'clip.avi', 'sha256': digest(data), 'label': 'Video fixture',
                       'subject_id': 'synthetic-video-fixture', 'group_id': 'video-group',
                       'split': 'train',
                       'ancestry': {'kind': 'original_capture', 'evidence_reference': 'generated',
                                    'statement': 'Generated by the test fixture.',
                                    'ancestry_complete': True, 'parent_sha256': []}}]}
        if video_enabled is not None:
            payload['media_policy'] = {'video_frame_materialization': video_enabled}
        return payload

    def test_video_registration_requires_the_explicit_media_policy_flag(self):
        from app.services.curation_registration import register_source_manifest
        with Session(self.engine) as session:
            refusal = self.expect_code('video_materialization_disabled', register_source_manifest,
                                       session, self.manifest())
            self.assertEqual(refusal.status_code, 403)
        with Session(self.engine) as session:
            self.expect_code('video_materialization_disabled', register_source_manifest,
                             session, self.manifest(False))
        with Session(self.engine) as session:
            self.assertEqual(session.exec(select(CurationDataset)).all(), [])

    def test_enabled_flag_is_pinned_into_dataset_policy(self):
        from app.services.curation_registration import register_source_manifest
        with Session(self.engine) as session:
            result = register_source_manifest(session, self.manifest(True))
        with Session(self.engine) as session:
            dataset = session.get(CurationDataset, result['dataset_id'])
            self.assertEqual(dataset.policy['media']['video_frame_materialization'], True)
            self.assertEqual(dataset.policy['media']['generative_derivatives'], False)
            self.assertEqual(dataset.policy['media']['repair_and_mask_materialization'], False)
            self.assertEqual(dataset.policy['media']['allowed_video_extensions'],
                             list(media.VIDEO_EXTENSIONS))
            self.assertTrue(media.video_policy_enabled(dataset))


if __name__ == '__main__':  # pragma: no cover
    unittest.main()
