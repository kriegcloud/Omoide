import gc

import numpy as np
from PIL import Image
from cv2.typing import MatLike
from sqlmodel import col, select

from app.config import settings
from app.database import safe_commit
from app.models import Media, Scene
from app.processors.base import MediaProcessor
from app.frames import PhotoScene

from app.sharpness import laplacian_score as _laplacian_score, score_image_path

_MAX_SCENES = 5

class BlurProcessor(MediaProcessor):
    name = "blur"
    order = 5  # before embedding/faces, no heavy models needed

    def process(
        self,
        media: Media,
        session,
        scenes: list[tuple[Scene, MatLike]] | list[PhotoScene] | list[Scene],
    ) -> bool | None:
        if session.exec(
            select(Media).where(
                Media.id == media.id,
                Media.laplacian_score.isnot(None),
            )
        ).first():
            return True

        if media.duration is None:
            try:
                media.laplacian_score = score_image_path(media.path)
            except (OSError, ValueError):
                media.laplacian_score = -1.0
            session.add(media)
            safe_commit(session)
            return True

        scores: list[float] = []
        # Sample at most _MAX_SCENES frames for long videos.
        sampled = list(scenes)
        if len(sampled) > _MAX_SCENES:
            step = len(sampled) // _MAX_SCENES
            sampled = sampled[::step][:_MAX_SCENES]

        for scene in sampled:
            try:
                if isinstance(scene, tuple):
                    # video frame — already an RGB ndarray
                    arr: np.ndarray = scene[1]
                elif isinstance(scene, Scene):
                    # stored scene thumbnail on disk — open directly as gray
                    arr = np.array(
                        Image.open(settings.general.thumb_dir / scene.thumbnail_path).convert("L")
                    )
                else:
                    # photo: oriented luma, decoded at >= half resolution
                    arr = scene.gray
            except OSError:
                continue

            if arr is None or arr.size == 0:
                continue

            scores.append(_laplacian_score(arr))

        if not scores:
            media.laplacian_score = -1.0
            session.add(media)
            return True

        media.laplacian_score = float(np.median(scores))
        session.add(media)
        safe_commit(session)
        return True

    def load_model(self):
        if settings.processors.blur_processor_active:
            self.active = True

    def unload(self):
        gc.collect()

    def get_results(self, media_id: int, session):
        result = session.exec(select(Media).where(Media.id == media_id)).first()
        if result and result.laplacian_score is not None:
            return {"laplacian_score": result.laplacian_score}
        return {}

    def get_pending_condition(self):
        return col(Media.laplacian_score).is_(None)

    def reset_for_media(self, media: Media, session) -> None:
        media.laplacian_score = None
        session.add(media)
