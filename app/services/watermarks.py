"""Watermark proposal generation and reversible, guarded file replacement."""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import os
import re
import tempfile
from threading import RLock

import cv2
import numpy as np
from PIL import Image

FILE_LOCK = RLock()
DETECTOR_VERSION = 'florence-community/Florence-2-large:ocr-region-v1'
MODEL_VERSION = 'big-lama:hard-composite-v1'


def normalize_handle(text):
    return ''.join(char for char in text.lower() if char.isalnum())


def edit_similarity(left, right):
    """Normalized Levenshtein similarity, with substitutions counted as edits."""
    if not left or not right:
        return float(left == right)
    previous = list(range(len(right) + 1))
    for i, a in enumerate(left, 1):
        current = [i]
        for j, b in enumerate(right, 1):
            current.append(min(current[-1] + 1, previous[j] + 1,
                               previous[j - 1] + (a != b)))
        previous = current
    return 1 - previous[-1] / max(len(left), len(right))


def select_regions(regions, size, handles):
    width, height = size
    learned = [normalize_handle(getattr(h, 'text_normalized', h)) for h in handles]
    selected = []
    for region in regions:
        text = str(region.get('text', ''))
        normalized = normalize_handle(text)
        quad = region.get('quad', [])
        if len(normalized) < 3 or len(quad) != 8 or not all(np.isfinite(v) for v in quad):
            continue
        match = next((h for h in learned if h and edit_similarity(normalized, h) >= .8), None)
        xs, ys = quad[::2], quad[1::2]
        edge = min(xs) <= width*.2 or max(xs) >= width*.8 or min(ys) <= height*.2 or max(ys) >= height*.8
        looks_like = any(token in text.lower() for token in ('.com','.vip','/','@','onlyfans','fansly','patreon','instagram','tiktok','reddit','twitter','x.com'))
        if match or (edge and looks_like):
            selected.append({**region, 'matched_handle': match, 'kind': 'learned' if match else 'edge_handle'})
    return selected


def validate_mask(mask, size):
    if mask.size != size:
        raise ValueError('Mask dimensions must match the source image')
    values = np.asarray(mask.convert('L'))
    binary = (values > 0).astype(np.uint8)*255
    coverage = np.count_nonzero(binary)/binary.size
    if not 0 < coverage <= .08:
        raise ValueError('Mask must cover more than zero and at most 8% of the image')
    return Image.fromarray(binary)


def build_mask(image, regions):
    gray = np.asarray(image.convert('L'))
    h,w = gray.shape
    result = np.zeros((h,w), np.uint8)
    for region in regions:
        quad = region['quad']
        x0=max(0,int(min(quad[::2]))-6); x1=min(w,int(max(quad[::2]))+7)
        y0=max(0,int(min(quad[1::2]))-6); y1=min(h,int(max(quad[1::2]))+7)
        if x1<=x0 or y1<=y0: continue
        box=gray[y0:y1,x0:x1]
        sigma=max(1., (max(quad[1::2])-min(quad[1::2]))/6.)
        high=np.abs(box.astype(np.float32)-cv2.GaussianBlur(box.astype(np.float32),(0,0),sigma))
        high=cv2.normalize(high,None,0,255,cv2.NORM_MINMAX).astype(np.uint8)
        _, strokes=cv2.threshold(high,0,255,cv2.THRESH_BINARY+cv2.THRESH_OTSU)
        strokes=cv2.dilate(strokes,np.ones((9,9),np.uint8))
        if np.count_nonzero(strokes) < box.size*.05: strokes[:]=255
        result[y0:y1,x0:x1] |= strokes
    return validate_mask(Image.fromarray(result),image.size)


def hard_composite(source, repaired, mask):
    if repaired.size != source.size or mask.size != source.size:
        raise ValueError('Inpaint dimensions differ from source')
    src=np.asarray(source.convert('RGB')); out=src.copy(); selected=np.asarray(mask.convert('L'))>0
    out[selected]=np.asarray(repaired.convert('RGB'))[selected]
    assert np.array_equal(out[~selected],src[~selected])
    return Image.fromarray(out)


def digest(path):
    h=sha256()
    with open(path,'rb') as stream:
        for chunk in iter(lambda: stream.read(1024*1024),b''): h.update(chunk)
    return h.hexdigest()


def apply_image(path, cleaned, expected_sha256=None, before_replace=None):
    """Encode first, checkpoint backup destination, then rename on one filesystem.

    The caller persists before_replace BEFORE the first rename, leaving a recovery
    route even if execution stops between the two atomic filesystem operations.
    """
    path=Path(path)
    with FILE_LOCK:
        if path.is_symlink() or (path.parent / '.beep-originals').is_symlink():
            raise ValueError('Original and backup directory must not be symlinks')
        original_hash=digest(path)
        if expected_sha256 and original_hash != expected_sha256:
            raise ValueError('Original changed since preview; detect it again')
        with Image.open(path) as source:
            fmt=source.format
            if fmt not in ('JPEG','PNG','WEBP'): raise ValueError('Only JPEG, PNG and WebP replacement is supported')
            if getattr(source, 'n_frames', 1) != 1: raise ValueError('Animated images are not supported')
            if source.size != cleaned.size: raise ValueError('Cleaned image dimensions differ')
            options={k:source.info[k] for k in ('exif','icc_profile') if k in source.info}
            if fmt=='JPEG': options['quality']=95
            if fmt=='WEBP': options['lossless']=True
            output=cleaned.convert(source.mode) if source.mode in ('RGB','RGBA','L') else cleaned.convert('RGB')
            if 'A' in source.getbands() or 'transparency' in source.info:
                output=cleaned.convert('RGBA'); output.putalpha(source.convert('RGBA').getchannel('A'))
        backup_dir=path.parent/'.beep-originals'; backup_dir.mkdir(exist_ok=True)
        backup=backup_dir/path.name
        if backup.exists(): backup=backup_dir/(path.name+'.'+datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f'))
        fd,tmp=tempfile.mkstemp(prefix='.watermark-',dir=path.parent)
        try:
            with os.fdopen(fd,'wb') as stream:
                output.save(stream,format=fmt,**options); stream.flush(); os.fsync(stream.fileno())
            cleaned_hash=digest(tmp)
            if before_replace: before_replace(backup,original_hash,cleaned_hash)
            os.rename(path,backup)
            try: os.replace(tmp,path)
            except BaseException:
                os.rename(backup,path)
                raise
            return backup,original_hash,cleaned_hash
        finally:
            if os.path.exists(tmp): os.unlink(tmp)


def restore_image(path, backup, expected_cleaned_sha256=None, expected_original_sha256=None):
    path,backup=Path(path),Path(backup)
    with FILE_LOCK:
        if path.is_symlink() or backup.is_symlink() or backup.parent.is_symlink():
            raise ValueError('Original and backup must not be symlinks')
        if backup.parent != path.parent/'.beep-originals': raise ValueError('Invalid backup location')
        if expected_cleaned_sha256 and path.exists() and digest(path)!=expected_cleaned_sha256:
            raise ValueError('Cleaned file changed; restore refused')
        if expected_original_sha256 and digest(backup)!=expected_original_sha256:
            raise ValueError('Backup changed; restore refused')
        os.replace(backup,path)


def preview_candidate(session, media, candidate, mask=None):
    from app.config import settings
    from app.services.vision_client import get_vision_client
    # Raw raster coordinates preserve original EXIF, including orientation.
    original_hash=digest(media.path)
    with Image.open(media.path) as source:
        source.load(); image=source.convert('RGB'); image.info.clear()
    mask=validate_mask(mask,image.size) if mask is not None else build_mask(image,candidate.regions)
    repaired=get_vision_client().inpaint(image,mask)
    cleaned=hard_composite(image,repaired,mask)
    directory=settings.general.thumb_dir/'watermarks'; directory.mkdir(parents=True,exist_ok=True)
    token=os.urandom(8).hex()
    mask_path=directory/f'{candidate.id}-{token}-mask.png'
    preview_path=directory/f'{candidate.id}-{token}-preview.jpg'
    mask.save(mask_path); cleaned.save(preview_path,quality=95)
    candidate.mask_path=str(mask_path.relative_to(settings.general.thumb_dir))
    candidate.preview_path=str(preview_path.relative_to(settings.general.thumb_dir))
    if digest(media.path)!=original_hash:
        mask_path.unlink(missing_ok=True); preview_path.unlink(missing_ok=True)
        raise ValueError('Source changed while generating preview')
    candidate.original_sha256=original_hash
    session.add(candidate); session.commit()
    return candidate


def refresh_media(session, media, mask):
    from app.phash import generate_perceptual_hash
    from app.sharpness import score_image_path
    from app.thumbnails import generate_thumbnail
    from app.processors.embedding_extractor import EmbeddingExtractor
    from app.processors.quality import QualityProcessor
    media.size=Path(media.path).stat().st_size
    media.phash=generate_perceptual_hash(media,'image')
    thumb,error=generate_thumbnail(media)
    if error: raise ValueError(error)
    media.thumbnail_path=thumb
    media.laplacian_score=score_image_path(media.path)
    media.quality_score=None
    media.person_score=None; media.person_siglip_margin=None
    media.persons_detected=False; media.has_persons=None
    from app.frames import load_photo_scene
    photo=load_photo_scene(Path(media.path))
    try:
        embedding=EmbeddingExtractor(); embedding.load_model(); embedding.reset_for_media(media,session)
        if not embedding.process(media,session,[photo]): raise ValueError('Embedding refresh failed')
        quality=QualityProcessor(); quality.load_model(); quality.process(media,session,[])
        if media.quality_score is None: raise ValueError('Quality refresh remains pending')
        from app.models import Face
        from sqlmodel import select
        selected=np.asarray(mask.convert('L'))>0
        from PIL import ImageOps
        with Image.open(media.path) as source:
            oriented_mask=mask.copy(); oriented_mask.info['exif']=source.getexif().tobytes()
            oriented_mask=ImageOps.exif_transpose(oriented_mask)
        selected=np.asarray(oriented_mask.resize((photo.rgb.shape[1],photo.rgb.shape[0]),Image.Resampling.NEAREST))>0
        overlapping=False
        for face in session.exec(select(Face).where(Face.media_id==media.id)).all():
            x0,y0,width,height=map(int,face.bbox)
            x1,y1=x0+width,y0+height
            if selected[max(0,y0):max(0,y1),max(0,x0):max(0,x1)].any(): overlapping=True
        if overlapping:
            from app.processors.faces import FaceProcessor
            faces=FaceProcessor(); faces.load_model(); faces.reset_for_media(media,session)
            if not faces.process(media,session,[photo]): raise ValueError('Face refresh failed')
        session.add(media); session.commit()
    finally:
        photo.image.close()
