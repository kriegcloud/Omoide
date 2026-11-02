"""Hybrid person observations from RT-DETR and the stored SigLIP vector."""
from threading import Lock

import numpy as np
from PIL import Image, ImageOps
from sqlalchemy import text
from sqlmodel import col, select

from app.config import settings
from app.database import safe_commit
from app.logger import logger
from app.models import Media, Scene
from app.processors.base import MediaProcessor
from app.services.vision_client import VisionError, get_vision_client
from app.vectors import vector_from_stored

POS = [
    "a photo of a person", "a close-up photo of part of a human body",
    "a photo of a hand", "a photo of legs and feet", "a photo of a woman's torso",
    "a photo of a person seen from behind", "a cropped photo of skin and a body",
    "a mirror selfie of a person with the face hidden", "a photo of a nude body",
]
NEG = [
    "a photo with no people", "a photo of an object", "a photo of a room with nobody in it",
    "a screenshot of text", "a photo of food", "a photo of a landscape",
    "a photo of an animal", "a meme or graphic", "a photo of clothing laid out",
]
_text_embeddings: dict[tuple[str, int], np.ndarray] = {}
_text_lock = Lock()


def has_persons(score: float, margin: float) -> bool:
    return score >= 0.35 or margin > 0


def _prepare(image):
    image = ImageOps.exif_transpose(image).convert('RGB')
    image.thumbnail((1280, 1280), Image.Resampling.LANCZOS)
    return image


def detector_images(media, scenes):
    """Read the image, or for videos the poster frame plus up to four evenly sampled
    existing keyframes (short clips can switch content between keyframes)."""
    if media.duration is None:
        with Image.open(media.path) as source:
            return [_prepare(source)]
    available = [scene for scene in scenes if isinstance(scene, tuple) or (
        isinstance(scene, Scene) and scene.thumbnail_path
    )]
    images = []
    poster = getattr(media, 'thumbnail_path', None)
    if poster:
        try:
            with Image.open(settings.general.thumb_dir / poster) as source:
                images.append(_prepare(source))
        except OSError:
            pass
    budget = 5 - len(images)
    indices = np.linspace(0, len(available) - 1, min(budget, len(available)), dtype=int) if available else []
    for index in indices:
        scene = available[index]
        if isinstance(scene, tuple):
            images.append(_prepare(Image.fromarray(scene[1])))
        else:
            with Image.open(settings.general.thumb_dir / scene.thumbnail_path) as source:
                images.append(_prepare(source))
    return images


def _prompts(client, model_id, dimension):
    key = (model_id, dimension)
    with _text_lock:
        if key not in _text_embeddings:
            vectors = np.asarray(client.embed_texts(POS + NEG, model_id, dimension), dtype=np.float32)
            if vectors.shape != (len(POS) + len(NEG), dimension) or not np.isfinite(vectors).all():
                raise VisionError('invalid person prompt embeddings')
            _text_embeddings[key] = vectors
        return _text_embeddings[key]


class PersonDetectionProcessor(MediaProcessor):
    name = 'person_detection'
    order = 11  # Stored media embedding must exist before evaluating SigLIP.
    handles_empty_scenes = True
    allows_pending_result = True

    def load_model(self):
        self.active = settings.processors.person_detection_processor_active

    def unload(self):
        pass

    def process(self, media, session, scenes):
        if media.persons_detected:
            return True
        client = get_vision_client()
        if not client.enabled:
            return True
        row = session.exec(text('SELECT embedding FROM media_embeddings WHERE media_id = :id').bindparams(id=media.id)).first()
        if row is None:
            return True
        try:
            model = settings.ai.clip_model
            vector = vector_from_stored(row[0])
            if vector is None or vector.shape != (model.embedding_size,) or not np.isfinite(vector).all():
                return True
            prompts = _prompts(client, model.hf_repo, model.embedding_size)
            similarities = prompts @ vector
            margin = float(similarities[:len(POS)].max() - similarities[len(POS):].max())
            images = detector_images(media, scenes)
            if not images:
                return True
            detections = client.detect_persons(images)
            if len(detections) != len(images):
                raise VisionError('invalid person detection count')
            scores = [float(result['score']) for result in detections]
            if not all(np.isfinite(score) and 0 <= score <= 1 for score in scores):
                raise VisionError('invalid person detection score')
            score = max(scores)
        except (VisionError, OSError, ValueError, TypeError, KeyError) as exc:
            logger.debug('Person detection remains pending: %s', type(exc).__name__)
            return True
        media.person_score = score
        media.person_siglip_margin = margin
        media.has_persons = has_persons(score, margin)
        media.persons_detected = True
        session.add(media)
        safe_commit(session)
        return True

    def get_results(self, media_id, session):
        media = session.exec(select(Media).where(Media.id == media_id)).first()
        return {key: getattr(media, key) for key in (
            'person_score', 'person_siglip_margin', 'persons_detected', 'has_persons'
        )} if media else {}

    def get_pending_condition(self):
        return col(Media.persons_detected).is_(False)

    def reset_for_media(self, media, session):
        media.person_score = None
        media.person_siglip_margin = None
        media.persons_detected = False
        media.has_persons = None
        session.add(media)
