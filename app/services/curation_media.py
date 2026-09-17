"""Deterministic, fully disclosed still and single-frame media normalization.

Every accepted input produces byte-identical output for identical input bytes and
records, in artifact provenance, exactly what was done to it: the decoded input
mode, any ICC transform (with the embedded profile digest and rendering intent),
any bit-depth reduction, palette expansion, opaque-alpha flattening, CMYK
conversion, the selected frame, and — for video — the decoder lineage.

Nothing here is generative. Repair/mask/comparison evidence has a schema stub in
``app.schemas.curation`` only; materializing such a kind is refused. Unsupported
inputs keep an explicit refusal code instead of a silent assumption, and optional
runtimes (ImageCms, pillow-heif, ffmpeg) refuse by name when they are absent.
"""
import io
import json
import os
import shutil
import subprocess
import tempfile
from fractions import Fraction
from pathlib import Path, PurePosixPath

import numpy as np
import PIL
from PIL import Image, ImageOps, UnidentifiedImageError, features

from app.services.curation_artifacts import (MAX_ARTIFACT_BYTES, MAX_PIXELS, TRANSFORM,
    canonical)
from app.services.curation_policy import digest, fail

try:  # Optional colour-management runtime; never assumed to exist.
    from PIL import ImageCms
except ImportError:  # pragma: no cover - exercised by patching IMAGECMS
    ImageCms = None

try:  # Optional HEIC/HEIF decoder; never assumed to exist.
    import pillow_heif
except ImportError:  # pragma: no cover - exercised by patching PILLOW_HEIF
    pillow_heif = None

IMAGECMS = ImageCms
PILLOW_HEIF = pillow_heif

# Formats accepted without an explicitly requested frame. Everything else is
# refused by name; widening this set is a transform-version change.
BASE_FORMATS = ('JPEG', 'PNG', 'WEBP', 'HEIF')
# Accepted only when the operator explicitly requests one frame/page index.
FRAME_INDEXED_FORMATS = ('GIF', 'TIFF')
VIDEO_EXTENSIONS = ('.avi', '.m4v', '.mkv', '.mov', '.mp4', '.webm')
HEIF_BRANDS = (b'heic', b'heix', b'heim', b'heis', b'hevc', b'hevx', b'mif1', b'msf1')

MAX_VIDEO_FRAMES = 5000
FFMPEG_TIMEOUT_SECONDS = 20
FFMPEG_ENVIRONMENT = {'PATH': '/usr/bin:/bin', 'LC_ALL': 'C'}

# Exact, disclosed 16-bit reduction: round-half-up of value * 255 / 65535.
BIT_DEPTH_METHOD = 'uint16-linear-255-div-65535-round-half-up'
ALPHA_METHOD = 'reject-unless-fully-opaque-then-drop-opaque-alpha'
RENDERING_INTENT = 0  # perceptual


def runtime_versions() -> dict:
    """Optional-runtime identity recorded with every artifact of this transform."""
    return {'pillow': PIL.__version__,
            'littlecms2': features.version('littlecms2') if IMAGECMS else None,
            'imagecms': bool(IMAGECMS),
            'pillow_heif': getattr(PILLOW_HEIF, '__version__', None),
            'libheif': PILLOW_HEIF.libheif_version() if PILLOW_HEIF else None}


def _selector(frame):
    """Accept a validated selector model or its JSON snapshot; never a raw dict."""
    if frame is None:
        return None
    from app.schemas.curation import FRAME_SELECTOR
    from pydantic import ValidationError
    try:
        return FRAME_SELECTOR.validate_python(
            frame if isinstance(frame, dict) else frame.model_dump())
    except ValidationError:
        fail('invalid_frame_selector', 422)


def _extension(relative_path: str) -> str:
    return PurePosixPath(relative_path).suffix.lower()


def _is_heif(data: bytes) -> bool:
    return len(data) >= 12 and data[4:8] == b'ftyp' and data[8:12] in HEIF_BRANDS


def _open(data: bytes):
    if _is_heif(data):
        if PILLOW_HEIF is None:
            fail('heif_runtime_unavailable')
        PILLOW_HEIF.register_heif_opener()  # Idempotent; decoder only, no policy.
    return Image.open(io.BytesIO(data))


def _cms_profile(icc: bytes):
    try:
        return IMAGECMS.getOpenProfile(io.BytesIO(icc))
    except Exception:
        fail('unsupported_icc')


def _icc_to_srgb(image, icc: bytes) -> tuple:
    """Convert through the embedded profile to sRGB; refuse rather than guess."""
    if IMAGECMS is None:
        fail('icc_runtime_unavailable')
    source = _cms_profile(icc)
    try:
        description = IMAGECMS.getProfileDescription(source).strip()
    except Exception:
        description = None
    flags = getattr(getattr(IMAGECMS, 'Flags', None), 'NONE', 0)
    try:
        converted = IMAGECMS.profileToProfile(image, source, IMAGECMS.createProfile('sRGB'),
                                              renderingIntent=RENDERING_INTENT,
                                              outputMode='RGB', flags=flags)
    except Exception:
        fail('unsupported_icc')
    if converted is None:
        fail('unsupported_icc')
    return converted, {'embedded_profile_sha256': digest(icc), 'embedded_profile_bytes': len(icc),
                       'profile_description': description, 'rendering_intent': 'perceptual',
                       'rendering_intent_code': RENDERING_INTENT,
                       'black_point_compensation': False,
                       'littlecms2': features.version('littlecms2')}


def _flatten_opaque(image) -> tuple:
    """Alpha stays refused unless every pixel is fully opaque."""
    with_alpha = image if image.mode == 'RGBA' else image.convert('RGBA')
    if with_alpha.getextrema()[3][0] != 255:
        fail('unsupported_color_or_alpha')
    return with_alpha.convert('RGB'), {'input_had_alpha': True, 'fully_opaque': True,
                                       'method': ALPHA_METHOD, 'composited_background': None}


def _to_eight_bit(image) -> tuple:
    """Deterministic 16-bit integer reduction; byte order normalized by dtype."""
    array = np.asarray(image).astype(np.uint16)
    reduced = ((array.astype(np.uint32) * 255 + 32767) // 65535).astype(np.uint8)
    return Image.fromarray(reduced, mode='L'), {'input_mode': image.mode, 'input_bits': 16,
                                                'output_bits': 8, 'method': BIT_DEPTH_METHOD}


def _to_srgb(image, icc, uncertainties) -> tuple:
    """Return an RGB image plus the exact colour record for its provenance."""
    record = {'input_mode': image.mode, 'output_mode': 'RGB', 'icc': None, 'bit_depth': None,
              'alpha': None, 'palette_expanded': False, 'cmyk_converted': False}
    if image.mode in {'I', 'F'} or image.mode == '1':
        fail('unsupported_bit_depth')
    if image.mode.startswith('I;16'):
        image, record['bit_depth'] = _to_eight_bit(image)
        uncertainties.append('16-bit integer input was reduced to 8 bits per channel by '
                             f'{BIT_DEPTH_METHOD}; tonal precision was lost.')
    if image.mode in {'RGBA', 'LA', 'PA'} or 'transparency' in image.info:
        was_palette = image.mode in {'P', 'PA'}
        image, record['alpha'] = _flatten_opaque(image)
        record['palette_expanded'] = record['palette_expanded'] or was_palette
        uncertainties.append('Input carried a fully opaque alpha or transparency channel; '
                             'it was dropped without compositing any background.')
    if image.mode == 'P':
        image = image.convert('RGB')
        record['palette_expanded'] = True
        uncertainties.append('Palette input was expanded to RGB; palette entries are assumed sRGB.')
    if image.mode not in {'RGB', 'L', 'CMYK'}:
        fail('unsupported_color_or_alpha')
    record['working_mode'] = image.mode
    if icc:
        image, record['icc'] = _icc_to_srgb(image, icc)
        record['method'] = 'embedded-icc-to-srgb-perceptual'
        uncertainties.append('Colour was converted from the embedded ICC profile to sRGB with the '
                             'perceptual intent; the rendering is decoder-version dependent and no '
                             'exact colour accuracy is claimed.')
    else:
        if image.mode == 'CMYK':
            record['cmyk_converted'] = True
            record['method'] = 'untagged-cmyk-naive-rgb'
            uncertainties.append('Untagged CMYK was converted by the naive decoder conversion, not '
                                 'a colour-managed transform; colour accuracy is not claimed.')
        elif record['bit_depth'] or record['palette_expanded'] or image.mode == 'L':
            record['method'] = 'untagged-assumed-srgb'
            uncertainties.append('Untagged input is assumed sRGB; no colour accuracy claim.')
        else:
            record['method'] = 'untagged-assumed-srgb'
            uncertainties.append('Untagged RGB is assumed sRGB; no color accuracy claim.')
        image = image.convert('RGB')
    return image, record


def _encode(image, original_size, orientation, color, frame, uncertainties, decoder=None) -> tuple:
    image.info.clear()
    stream = io.BytesIO()
    image.save(stream, format='PNG', compress_level=9, optimize=False)
    encoded = stream.getvalue()
    if len(encoded) > MAX_ARTIFACT_BYTES:
        fail('artifact_too_large')
    pixels = canonical({'width': image.width, 'height': image.height, 'mode': 'RGB'}) + image.tobytes()
    provenance = {'transform': TRANSFORM, 'source_dimensions': original_size,
                  'output_dimensions': list(image.size), 'source_exif_orientation': orientation,
                  'color': color, 'frame': frame, 'runtimes': runtime_versions(),
                  'uncertainties': uncertainties}
    if decoder:
        provenance['decoder'] = decoder
        provenance['decoder_identity'] = {'tool': 'ffmpeg', 'version': decoder['ffmpeg_version']}
    return encoded, digest(pixels), image.width, image.height, provenance


def normalize_still(data: bytes, frame=None, decoder=None):
    """Decode one still (or one explicitly requested frame of a multi-frame still)."""
    selector = _selector(frame)
    index = selector.index if selector is not None and selector.kind == 'image_frame' else None
    if selector is not None and selector.kind == 'video_frame' and decoder is None:
        fail('video_selector_not_supported_for_still')
    if selector is not None and selector.kind == 'repair_evidence':
        fail('generative_disabled', 403)
    try:
        with _open(data) as opened:
            accepted = BASE_FORMATS + (FRAME_INDEXED_FORMATS if index is not None else ())
            if opened.format not in accepted:
                fail('unsupported_format')
            if opened.width * opened.height > MAX_PIXELS:
                fail('decoded_image_too_large')
            frames = getattr(opened, 'n_frames', 1)
            if index is None:
                if frames != 1:
                    fail('unsupported_animation')
            else:
                if index >= frames:
                    fail('frame_index_out_of_range')
                opened.seek(index)
            if opened.width * opened.height > MAX_PIXELS:
                fail('decoded_image_too_large')
            icc = opened.info.get('icc_profile')
            original_size = list(opened.size)
            orientation = opened.getexif().get(274, 1)
            uncertainties = []
            output, color = _to_srgb(ImageOps.exif_transpose(opened), icc, uncertainties)
            record = {'source_format': opened.format, 'frame_count': frames,
                      'selected_index': 0 if index is None else index,
                      'explicitly_requested': index is not None}
            return _encode(output, original_size, orientation, color, record, uncertainties, decoder)
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        fail('corrupt_or_unsupported_image')


def _tool(name: str) -> str:
    path = shutil.which(name, path=FFMPEG_ENVIRONMENT['PATH'])
    if not path:
        fail('ffmpeg_runtime_unavailable')
    return path


def _run(arguments: list[str], code: str) -> str:
    try:
        finished = subprocess.run(arguments, stdin=subprocess.DEVNULL, capture_output=True,
                                  timeout=FFMPEG_TIMEOUT_SECONDS, env=FFMPEG_ENVIRONMENT,
                                  close_fds=True, shell=False)
    except subprocess.TimeoutExpired:
        fail('video_decode_timeout')
    except OSError:
        fail('ffmpeg_runtime_unavailable')
    if finished.returncode != 0:
        fail(code)
    return finished.stdout.decode('utf-8', 'replace')


def _version(tool: str) -> str:
    line = _run([_tool(tool), '-hide_banner', '-version'], 'ffmpeg_runtime_unavailable').splitlines()
    return line[0].strip() if line else ''


def _probe_stream(path: str, stream_index: int) -> dict:
    payload = _run([_tool('ffprobe'), '-v', 'error', '-print_format', 'json',
                    '-show_format', '-show_streams', '-select_streams', f'v:{stream_index}', path],
                   'video_probe_failed')
    try:
        parsed = json.loads(payload)
    except ValueError:
        fail('video_probe_failed')
    streams = parsed.get('streams') or []
    if not streams:
        fail('video_stream_not_found')
    return {'stream': streams[0], 'format': parsed.get('format') or {}}


def _probe_frames(path: str, stream_index: int) -> list[dict]:
    payload = _run([_tool('ffprobe'), '-v', 'error', '-print_format', 'json',
                    '-select_streams', f'v:{stream_index}',
                    '-show_entries', 'frame=pts,pts_time,key_frame,duration', path],
                   'video_probe_failed')
    try:
        frames = json.loads(payload).get('frames') or []
    except ValueError:
        fail('video_probe_failed')
    if len(frames) > MAX_VIDEO_FRAMES:
        fail('video_frame_count_exceeded')
    if not frames:
        fail('video_stream_not_found')
    return frames


def _rotation(stream: dict):
    for entry in stream.get('side_data_list') or []:
        if 'rotation' in entry:
            return entry['rotation']
    return (stream.get('tags') or {}).get('rotate')


def _timebase(stream: dict) -> Fraction:
    try:
        return Fraction(stream['time_base'])
    except (KeyError, ValueError, ZeroDivisionError):
        fail('video_probe_failed')


def video_frame(data: bytes, relative_path: str, selector):
    """Decode exactly one requested presentation timestamp from a bounded copy.

    The registered source is never handed to ffmpeg: its already hash-verified
    bytes are copied into a private temporary directory, decoded there, and the
    directory is removed. Nothing is written beside the original.
    """
    extension = _extension(relative_path)
    if extension not in VIDEO_EXTENSIONS:
        fail('unsupported_video_extension')
    requested = Fraction(selector.pts_seconds)
    versions = {'ffmpeg_version': _version('ffmpeg'), 'ffprobe_version': _version('ffprobe')}
    workspace = tempfile.mkdtemp(prefix='curation-video-')
    try:
        os.chmod(workspace, 0o700)
        path = os.path.join(workspace, 'source' + extension)
        with open(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as handle:
            handle.write(data)
        probed = _probe_stream(path, selector.stream_index)
        stream, container = probed['stream'], probed['format']
        timebase = _timebase(stream)
        frames = _probe_frames(path, selector.stream_index)
        chosen = None
        for position, entry in enumerate(frames):
            pts = entry.get('pts')
            if pts is None:
                continue
            if Fraction(int(pts)) * timebase >= requested:
                chosen = (position, int(pts), entry)
                break
        if chosen is None:
            fail('video_timestamp_out_of_range')
        position, pts, entry = chosen
        actual = Fraction(pts) * timebase
        output = os.path.join(workspace, 'frame.png')
        _run([_tool('ffmpeg'), '-nostdin', '-hide_banner', '-loglevel', 'error', '-i', path,
              '-map', f'0:v:{selector.stream_index}', '-vf', 'select=eq(n\\,%d)' % position,
              '-fps_mode', 'passthrough', '-frames:v', '1', '-an', '-sn', '-dn',
              '-pix_fmt', 'rgb24', '-f', 'image2', '-c:v', 'png', '-y', output],
             'video_decode_failed')
        try:
            decoded = Path(output).read_bytes()
        except OSError:
            fail('video_decode_failed')
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
    lineage = {**versions, 'tool': 'ffmpeg',
               'container_format': container.get('format_name'),
               'container_duration_seconds': container.get('duration'),
               'codec_name': stream.get('codec_name'), 'codec_long_name': stream.get('codec_long_name'),
               'stream_index': selector.stream_index, 'time_base': stream.get('time_base'),
               'avg_frame_rate': stream.get('avg_frame_rate'), 'r_frame_rate': stream.get('r_frame_rate'),
               'decoded_frame_count': len(frames), 'frame_index': position,
               'requested_pts_seconds': selector.pts_seconds,
               'actual_pts': pts, 'actual_pts_seconds': f'{actual.numerator}/{actual.denominator}',
               'actual_pts_time': entry.get('pts_time'), 'key_frame': bool(entry.get('key_frame')),
               'coded_width': stream.get('width'), 'coded_height': stream.get('height'),
               'pix_fmt': stream.get('pix_fmt'), 'color_range': stream.get('color_range'),
               'color_primaries': stream.get('color_primaries'),
               'color_transfer': stream.get('color_transfer'), 'color_space': stream.get('color_space'),
               'rotation': _rotation(stream), 'selection_rule': 'first-presentation-timestamp-at-or-after-request',
               'source_sha256': digest(data)}
    encoded, pixel_hash, width, height, provenance = normalize_still(decoded, None, lineage)
    provenance['uncertainties'].append(
        'One video frame was decoded by ffmpeg and re-encoded losslessly; the reported container '
        'colour metadata was not colour-managed and the decoded RGB is assumed sRGB.')
    if not stream.get('color_primaries') or not stream.get('color_transfer'):
        provenance['uncertainties'].append(
            'The container did not report colour primaries or transfer characteristics.')
    return encoded, pixel_hash, width, height, provenance


def video_policy_enabled(dataset) -> bool:
    media = (dataset.policy or {}).get('media') or {}
    return media.get('video_frame_materialization') is True


def materialize_media(dataset, source, data: bytes, frame=None):
    """Single entry point used by materialization: still bytes or one video frame."""
    selector = _selector(frame)
    if selector is not None and selector.kind == 'repair_evidence':
        # Generative repair/mask evidence has a schema, never a materialization.
        fail('generative_disabled', 403)
    if _extension(source.relative_path) in VIDEO_EXTENSIONS:
        if selector is None or selector.kind != 'video_frame':
            fail('video_frame_selector_required')
        if not video_policy_enabled(dataset):
            fail('video_materialization_disabled', 403)
        return video_frame(data, source.relative_path, selector)
    if selector is not None and selector.kind == 'video_frame':
        fail('video_selector_not_supported_for_still')
    return normalize_still(data, selector)
