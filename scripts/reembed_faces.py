"""Re-embed existing face thumbnails atomically, retaining all assignments.

Run with matching/extraction tasks quiescent. Inference is staged in memory;
any missing, ambiguous, or rejected face aborts before catalog writes. No old
vectors are dropped. The existing migration helper rebuilds person centroids
in the same transaction as the vector replacements.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
from PIL import Image, ImageOps
from sqlalchemy import text
from sqlmodel import Session, select

from app.config import settings
from app.database import engine
from app.models import Face
from app.services.face_inference import AdaFaceSocketAnalysis
from app.services.face_model_state import ADA_FACE_MODEL_NAME
from app.utils import vector_to_blob
from scripts.migrate_face_embeddings import _backup_database, _pad_for_detection, _rebuild_people


def reembed(client):
    health = client.health()
    if health.get('mirrorTta') is not True or health.get('model') != ADA_FACE_MODEL_NAME:
        raise RuntimeError('Re-embedding requires the AdaFace service with mirror TTA enabled')
    with Session(engine) as session:
        snapshots = [(f.id, f.person_id, f.thumbnail_path, f.assignment_source, f.assigned_at)
                     for f in session.exec(select(Face).order_by(Face.id)).all()]
    staged = []
    unresolved = []
    for face_id, person_id, thumbnail, *_ in snapshots:
        try:
            path = (settings.general.thumb_dir / thumbnail).resolve() if thumbnail else None
            if path is None or not path.is_relative_to(settings.general.thumb_dir.resolve()):
                raise ValueError('missing or unsafe thumbnail')
            with Image.open(path) as image:
                rgb = np.asarray(ImageOps.exif_transpose(image).convert('RGB'))
            detected = client.get(rgb)
            if not detected or (len(detected) == 1 and detected[0].embedding is None):
                detected = client.get(_pad_for_detection(rgb))
            # Never choose the largest of multiple identities in a thumbnail.
            if len(detected) != 1 or detected[0].embedding is None:
                raise ValueError('no unambiguous embedding')
            chosen = detected[0]
            vector = np.asarray(chosen.embedding, dtype=np.float32)
            norm = getattr(chosen, 'embedding_norm', None)
            length = float(np.linalg.norm(vector))
            if (vector.shape != (512,) or not np.isfinite(vector).all()
                    or length <= 0 or norm is None or not np.isfinite(norm) or norm <= 0):
                raise ValueError('invalid embedding or raw norm')
            staged.append((face_id, person_id, vector_to_blob(vector / length), float(norm)))
        except (OSError, ValueError, RuntimeError) as error:
            unresolved.append(face_id)
            print(f'Face {face_id}: unresolved ({error})')
    if unresolved:
        raise RuntimeError(f'{len(unresolved)} unresolved faces; no vectors changed. Repair thumbnails/service and rerun.')
    print(f'Integrity-checked backup: {_backup_database()}')
    with Session(engine) as session:
        session.exec(text('BEGIN IMMEDIATE'))
        current = [(f.id, f.person_id, f.thumbnail_path, f.assignment_source, f.assigned_at)
                   for f in session.exec(select(Face).order_by(Face.id)).all()]
        if current != snapshots:
            raise RuntimeError('Face catalog changed during inference; quiesce tasks and rerun')
        for face_id, person_id, blob, norm in staged:
            session.exec(text('DELETE FROM face_embeddings WHERE face_id=:id').bindparams(id=face_id))
            session.exec(text('INSERT INTO face_embeddings(face_id,person_id,embedding) VALUES (:id,:person,:embedding)').bindparams(
                id=face_id, person=person_id if person_id is not None else -1, embedding=blob))
            session.exec(text('UPDATE face SET embedding_norm=:norm WHERE id=:id').bindparams(norm=norm,id=face_id))
        # This existing helper commits only after all centroids are rebuilt.
        _rebuild_people(session)
    print(f'Re-embedded {len(staged)} faces; assignments retained; person centroids rebuilt.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    if not args.apply:
        parser.error('--apply is required; quiesce matching/extraction tasks first')
    reembed(AdaFaceSocketAnalysis(settings.face_recognition.inference_socket_path,
                                 settings.face_recognition.inference_timeout_seconds))


if __name__ == '__main__':
    main()
