"""TOPIQ-NR observations; an unavailable worker leaves the item pending."""
from PIL import Image, ImageOps
from sqlmodel import col, select

from app.config import settings
from app.database import safe_commit
from app.logger import logger
from app.models import Media, Scene
from app.processors.base import MediaProcessor
from app.services.vision_client import VisionError, get_vision_client


def _prepare(image):
    image = ImageOps.exif_transpose(image).convert('RGB')
    image.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
    return image


class QualityProcessor(MediaProcessor):
    name = 'quality'
    order = 7
    handles_empty_scenes = True
    # This optional observation may remain pending while downstream processors run.
    allows_pending_result = True

    def load_model(self):
        self.active = settings.processors.quality_processor_active

    def unload(self):
        pass

    def process(self, media, session, scenes):
        if media.quality_score is not None:
            return True
        client = get_vision_client()
        if not client.enabled:
            return True
        try:
            images = []
            if media.duration is None:
                with Image.open(media.path) as source:
                    images.append(_prepare(source))
            else:
                for scene in scenes:
                    if isinstance(scene, tuple):
                        images.append(_prepare(Image.fromarray(scene[1])))
                    elif isinstance(scene, Scene) and scene.thumbnail_path:
                        with Image.open(settings.general.thumb_dir / scene.thumbnail_path) as source:
                            images.append(_prepare(source))
            if not images:
                return True
            scores = client.quality(images)
        except (VisionError, OSError, ValueError) as exc:
            logger.debug('Quality scoring remains pending: %s', type(exc).__name__)
            return True
        if scores:
            media.quality_score = sum(sorted(scores, reverse=True)[:3]) / min(3, len(scores))
            session.add(media)
            safe_commit(session)
        return True

    def get_results(self, media_id, session):
        media = session.exec(select(Media).where(Media.id == media_id)).first()
        return {'quality_score': media.quality_score} if media and media.quality_score is not None else {}

    def get_pending_condition(self):
        return col(Media.quality_score).is_(None)

    def reset_for_media(self, media, session):
        media.quality_score = None
        session.add(media)
