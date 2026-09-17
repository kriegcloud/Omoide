"""Descriptor-anchored, bounded still reads and deterministic fixture transforms."""
import io
import json
import os
import stat
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from uuid import uuid4

import PIL
from PIL import Image, ImageOps, UnidentifiedImageError, features

from app.services.curation_policy import digest, fail

MAX_BYTES = 16 * 1024 * 1024
MAX_PIXELS = 16_000_000
MAX_ARTIFACT_BYTES = 2 * 1024 * 1024
TRANSFORM = {'version': 'rgb-png-v1', 'formats': ['JPEG', 'PNG', 'WEBP'],
             'orientation': 'exif-transpose-once', 'color': 'untagged-rgb-assumed-srgb',
             'alpha': 'reject', 'icc': 'reject', 'resize': 'none',
             'encoder': 'PNG', 'compress_level': 9, 'optimize': False,
             'pillow': PIL.__version__,
             'code_sha256': digest(Path(__file__).read_bytes()),
             'codec_versions': {name: features.version(name) for name in ('jpg', 'zlib', 'webp')}}


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()


def safe_parts(path: str) -> tuple[str, ...]:
    parts = PurePosixPath(path).parts
    if not parts or PurePosixPath(path).is_absolute() or any(p in {'.', '..', ''} for p in path.split('/')):
        fail('unsafe_path')
    return parts


@contextmanager
def directory(path: str, identity: tuple[int, int] | None = None):
    """Walk from / with O_NOFOLLOW on every component, then verify root identity."""
    absolute = Path(path)
    if not absolute.is_absolute():
        fail('unsafe_root')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in absolute.parts[1:]:
            if component in {'.', '..'}:
                fail('unsafe_root')
            nxt = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = nxt
        info = os.fstat(fd)
        if identity and (info.st_dev, info.st_ino) != identity:
            fail('root_identity_changed')
        yield fd
    except OSError:
        fail('storage_unavailable')
    finally:
        os.close(fd)


@contextmanager
def parent_directory(root_fd: int, relative: str):
    parts = safe_parts(relative)
    fd = os.dup(root_fd)
    try:
        for part in parts[:-1]:
            nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = nxt
        yield fd, parts[-1]
    except OSError:
        fail('unsafe_path')
    finally:
        os.close(fd)


def stamp(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def read_at(root_fd: int, relative: str, expected: str | None = None,
            limit: int = MAX_BYTES, hook=None, descriptor_guard=None) -> bytes:
    with parent_directory(root_fd, relative) as (parent, name):
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        except OSError:
            fail('source_unavailable')
        try:
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode):
                fail('nonregular_file')
            if before.st_size > limit:
                fail('file_too_large')
            if descriptor_guard:
                descriptor_guard(fd)
            chunks = []
            total = 0
            if hook:
                hook()
            while True:
                chunk = os.read(fd, min(1024 * 1024, limit + 1 - total))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total > limit:
                    fail('file_too_large')
            after = os.fstat(fd)
            if descriptor_guard:
                descriptor_guard(fd)
            entry = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if stamp(before) != stamp(after) or stamp(entry) != stamp(after):
                fail('source_changed')
            data = b''.join(chunks)
            if expected and digest(data) != expected:
                fail('hash_mismatch')
            return data
        except OSError:
            fail('source_changed')
        finally:
            os.close(fd)


def source_bytes(dataset, source, hook=None):
    from app.services.source_locations import verify_source_descriptor, verify_source_volume
    verify_source_volume(dataset, source.relative_path)
    def guard(fd):
        verify_source_descriptor(dataset, fd)
    with directory(dataset.source_root, (dataset.source_device, dataset.source_inode)) as fd:
        data = read_at(fd, source.relative_path, source.sha256, hook=hook, descriptor_guard=guard)
    # Rewalk after read: reject ancestor/root substitution during the operation.
    with directory(dataset.source_root, (dataset.source_device, dataset.source_inode)) as fd:
        if digest(read_at(fd, source.relative_path, source.sha256, descriptor_guard=guard)) != digest(data):
            fail('source_changed')
    verify_source_volume(dataset, source.relative_path)
    return data


def normalized(data: bytes):
    try:
        with Image.open(io.BytesIO(data)) as opened:
            if opened.format not in TRANSFORM['formats']:
                fail('unsupported_format')
            if opened.width * opened.height > MAX_PIXELS:
                fail('decoded_image_too_large')
            if getattr(opened, 'n_frames', 1) != 1:
                fail('unsupported_animation')
            if opened.info.get('icc_profile'):
                fail('unsupported_icc')
            if opened.mode not in {'RGB', 'L'} or 'transparency' in opened.info:
                fail('unsupported_color_or_alpha')
            original_size = list(opened.size)
            orientation = opened.getexif().get(274, 1)
            output = ImageOps.exif_transpose(opened).convert('RGB')
            output.info.clear()
            stream = io.BytesIO()
            output.save(stream, format='PNG', compress_level=9, optimize=False)
            encoded = stream.getvalue()
            if len(encoded) > MAX_ARTIFACT_BYTES:
                fail('artifact_too_large')
            pixels = canonical({'width': output.width, 'height': output.height, 'mode': 'RGB'}) + output.tobytes()
            return encoded, digest(pixels), output.width, output.height, {
                'transform': TRANSFORM, 'source_dimensions': original_size,
                'output_dimensions': list(output.size), 'source_exif_orientation': orientation,
                'uncertainties': ['Untagged RGB is assumed sRGB; no color accuracy claim.'],
            }
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        fail('corrupt_or_unsupported_image')


def ensure_directory(parent_fd: int, name: str) -> int:
    safe_parts(name)
    if '/' in name:
        fail('unsafe_path')
    try:
        os.mkdir(name, mode=0o700, dir_fd=parent_fd)
        os.fsync(parent_fd)
    except FileExistsError:
        pass
    try:
        return os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
    except OSError:
        fail('unsafe_output_directory')


def probe_store_capabilities(root_fd: int) -> None:
    """Refuse store roots whose filesystem cannot honor publication semantics.

    Publication relies on exclusive creation, an enforced 0o400 mode and a
    same-directory hard link. Filesystems such as exFAT synthesize modes and
    reject links, which would otherwise surface as opaque failures after
    registration. The probe writes only private temporary names in the store.
    """
    name = '.capability-probe-' + uuid4().hex
    linked = name + '.link'
    try:
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=root_fd)
        try:
            os.fchmod(fd, 0o400)
            if stat.S_IMODE(os.fstat(fd).st_mode) != 0o400:
                fail('store_root_unsupported_filesystem')
        finally:
            os.close(fd)
        os.link(name, linked, src_dir_fd=root_fd, dst_dir_fd=root_fd, follow_symlinks=False)
    except OSError:
        fail('store_root_unsupported_filesystem')
    finally:
        for probe in (linked, name):
            try:
                os.unlink(probe, dir_fd=root_fd)
            except OSError:
                pass


def write_once(root_fd: int, name: str, data: bytes):
    safe_parts(name)
    if '/' in name:
        fail('unsafe_path')
    try:
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=root_fd)
    except FileExistsError:
        # An interrupted partial write is not silently trusted or overwritten.
        if read_at(root_fd, name, limit=max(MAX_BYTES, len(data))) != data:
            fail('publication_collision')
        return
    try:
        view = memoryview(data)
        while view:
            size = os.write(fd, view)
            view = view[size:]
        os.fsync(fd)
        os.fchmod(fd, 0o400)
    finally:
        os.close(fd)
    os.fsync(root_fd)


def artifact_bytes(dataset, artifact):
    with directory(dataset.store_root, (dataset.store_device, dataset.store_inode)) as fd:
        return read_at(fd, f'artifacts/{artifact.sha256}.png', artifact.sha256, MAX_ARTIFACT_BYTES)


def publish_artifact(dataset, data: bytes):
    with directory(dataset.store_root, (dataset.store_device, dataset.store_inode)) as root:
        fd = ensure_directory(root, 'artifacts')
        try:
            # Never expose a half-written content-addressed artifact. A crash
            # leaves only an owned temporary file; retry can publish a new one.
            temporary = '.pending-' + uuid4().hex
            write_once(fd, temporary, data)
            final = digest(data) + '.png'
            try:
                # Atomic no-clobber promotion from this private temporary inode.
                # This is not a hardlink to an original or exported artifact.
                os.link(temporary, final, src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)
            except FileExistsError:
                read_at(fd, final, digest(data), MAX_ARTIFACT_BYTES)
            os.unlink(temporary, dir_fd=fd)
            os.fsync(fd)
            verify_child_directory(root, 'artifacts', fd)
        finally:
            os.close(fd)
    with directory(dataset.store_root, (dataset.store_device, dataset.store_inode)) as root:
        read_at(root, 'artifacts/' + digest(data) + '.png', digest(data), MAX_ARTIFACT_BYTES)


def verify_child_directory(parent_fd: int, name: str, opened_fd: int):
    """Fence a child directory replaced after its descriptor was opened."""
    try:
        entry = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except OSError:
        fail('output_directory_changed')
    opened = os.fstat(opened_fd)
    if not stat.S_ISDIR(entry.st_mode) or (entry.st_dev, entry.st_ino) != (opened.st_dev, opened.st_ino):
        fail('output_directory_changed')
