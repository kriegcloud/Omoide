"""Read-only, cached exact-frame previews for video faces."""
import hashlib
import math
import os
import subprocess
import tempfile
import threading
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, Response
from PIL import Image
from sqlmodel import Session, select

from app.api._media_filters import exclude_missing
from app.config import settings
from app.database import get_session
from app.face_frames import extract_face_frame
from app.models import Face, Media

router = APIRouter()
# Bound concurrent ffmpeg work and collapse duplicate requests without retaining
# one lock per face forever. Sync handlers run in FastAPI's worker pool.
_extract_slots = threading.BoundedSemaphore(2)
_cache_locks = [threading.Lock() for _ in range(32)]


@router.get('/{face_id}/frame')
def face_frame(face_id: int, request: Request, session: Session = Depends(get_session)):
    row = session.exec(exclude_missing(
        select(Face, Media).join(Media, Face.media_id == Media.id).where(Face.id == face_id)
    )).first()
    if row is None:
        raise HTTPException(404, 'Video face not found')
    face, media = row
    timestamp = face.timestamp
    if media.duration is None or timestamp is None or not math.isfinite(timestamp) or timestamp < 0:
        raise HTTPException(404, 'Video face has no frame timestamp')
    # Processing uses the catalogued media.path directly, including read-only roots.
    source = Path(media.path)
    try:
        source_stat = source.stat()
        if not source.is_file():
            raise OSError('not a file')
    except OSError:
        raise HTTPException(404, 'Video file not found')
    # Include full timestamp and source identity: millisecond rounding alone could
    # alias two frames, or serve a stale frame after source replacement.
    identity = f'{source.resolve()}:{source_stat.st_dev}:{source_stat.st_ino}:{source_stat.st_size}:{source_stat.st_mtime_ns}:{source_stat.st_ctime_ns}:{timestamp!r}:v1'
    version = hashlib.sha256(identity.encode()).hexdigest()[:24]
    cache = settings.general.thumb_dir / 'face-frames'
    target = cache / f'{face_id}-{int(timestamp * 1000)}-{version}.jpg'
    with _cache_locks[face_id % len(_cache_locks)]:
        if not target.is_file():
            cache.mkdir(parents=True, exist_ok=True)
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(dir=cache, suffix='.jpg', delete=False) as file:
                    temporary = Path(file.name)
                with _extract_slots:
                    extract_face_frame(source, timestamp, temporary)
                os.replace(temporary, target)
            except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
                raise HTTPException(404, 'Video frame unavailable')
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
        with Image.open(target) as image:
            width, height = image.size
        stat = target.stat()
    version = f'{stat.st_dev}:{stat.st_ino}:{stat.st_size}:{stat.st_mtime_ns}:{stat.st_ctime_ns}'
    etag = '"' + hashlib.sha256(version.encode()).hexdigest() + '"'
    headers = {
        'Cache-Control': 'private, no-cache', 'ETag': etag,
        'X-Face-Frame-Width': str(width), 'X-Face-Frame-Height': str(height),
        'X-Face-Box-Space': 'detector',
    }
    matches = request.headers.get('if-none-match')
    if matches is not None and any(tag.strip().removeprefix('W/') in {etag, '*'} for tag in matches.split(',')):
        return Response(status_code=304, headers=headers)
    return FileResponse(target, media_type='image/jpeg', stat_result=stat, headers=headers)
