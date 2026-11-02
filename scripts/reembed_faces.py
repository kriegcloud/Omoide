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


def reembed(client, *, keep_unresolved=False, dry_run=None, min_agreement=None):
    """Re-embed faces. keep_unresolved retains the old vector (same model, near-identical
    space) for faces whose thumbnail cannot be re-detected; dry_run saves staged vectors
    to an .npz and writes nothing."""
    health = client.health()
    if health.get('mirrorTta') is not True or health.get('model') != ADA_FACE_MODEL_NAME:
        raise RuntimeError('Re-embedding requires the AdaFace service with mirror TTA enabled')
    with Session(engine) as session:
        snapshots = [(f.id, f.person_id, f.thumbnail_path, f.assignment_source, f.assigned_at)
                     for f in session.exec(select(Face).order_by(Face.id)).all()]
    old_vectors = {}
    if min_agreement is not None:
        with Session(engine) as session:
            for face_id, blob in session.exec(text('SELECT face_id, embedding FROM face_embeddings')).all():
                vector = np.frombuffer(blob, dtype=np.float32)
                length = float(np.linalg.norm(vector))
                if vector.shape == (512,) and length > 0:
                    old_vectors[face_id] = vector / length
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
            if min_agreement is not None:
                previous = old_vectors.get(face_id)
                if previous is not None and float(previous @ (vector / length)) < min_agreement:
                    raise ValueError(f'new vector disagrees with the stored one (cosine < {min_agreement})')
            staged.append((face_id, person_id, vector_to_blob(vector / length), float(norm)))
        except (OSError, ValueError, RuntimeError) as error:
            unresolved.append(face_id)
            print(f'Face {face_id}: unresolved ({error})')
    print(f'Staged {len(staged)} faces; unresolved {len(unresolved)}')
    if dry_run is not None:
        np.savez(dry_run, face_ids=np.array([row[0] for row in staged], dtype=np.int64),
                 vectors=np.stack([np.frombuffer(row[2], dtype=np.float32) for row in staged]) if staged else np.empty((0, 512), np.float32),
                 norms=np.array([row[3] for row in staged], dtype=np.float32),
                 unresolved=np.array(unresolved, dtype=np.int64))
        return {'rewritten': 0, 'kept': len(unresolved), 'staged': len(staged)}
    if unresolved and not keep_unresolved:
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
    print(f'Re-embedded {len(staged)} faces; kept {len(unresolved)} old vectors; assignments retained; person centroids rebuilt.')
    return {'rewritten': len(staged), 'kept': len(unresolved), 'staged': len(staged)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--dry-run', type=Path, metavar='NPZ', help='stage only; save vectors to NPZ')
    parser.add_argument('--min-agreement', type=float, default=None,
                        help='treat a face as unresolved when cosine(old, new) is below this')
    parser.add_argument('--keep-unresolved', action='store_true',
                        help='keep the old vector for faces whose thumbnail cannot be re-detected')
    args = parser.parse_args()
    if not args.apply and args.dry_run is None:
        parser.error('--apply (quiesce matching/extraction tasks first) or --dry-run is required')
    reembed(AdaFaceSocketAnalysis(settings.face_recognition.inference_socket_path,
                                 settings.face_recognition.inference_timeout_seconds),
            keep_unresolved=args.keep_unresolved, dry_run=args.dry_run, min_agreement=args.min_agreement)


if __name__ == '__main__':
    main()
