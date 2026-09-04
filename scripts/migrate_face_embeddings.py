"""Migrate Omoide's existing face thumbnails into the pinned AdaFace space.

This command reads only Omoide-generated face thumbnails under the data
directory. It never opens source media paths.
"""

from __future__ import annotations

import argparse
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps
from sqlalchemy import text
from sqlmodel import Session, select

from app.config import settings
from app.database import engine, safe_commit
from app.models import Face, Person
from app.services.face_inference import (
    FACE_MODEL_FINGERPRINT,
    AdaFaceSocketAnalysis,
)
from app.utils import vector_from_stored, vector_to_blob


def _backup_database() -> Path:
    source = settings.general.database_dir / "omoide.db"
    backup_dir = settings.general.data_dir / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    destination = backup_dir / f"omoide-pre-adaface-{stamp}.db"
    with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as source_db:
        with sqlite3.connect(destination) as destination_db:
            source_db.backup(destination_db)
            result = destination_db.execute("PRAGMA integrity_check").fetchone()
            if result != ("ok",):
                raise RuntimeError(
                    "AdaFace migration backup failed its integrity check"
                )
    destination.chmod(0o600)
    return destination


def _largest_face(faces):
    return max(
        faces,
        key=lambda face: max(0.0, float(face.bbox[2] - face.bbox[0]))
        * max(0.0, float(face.bbox[3] - face.bbox[1])),
    )


def _initialize_state(session: Session) -> None:
    session.exec(
        text(
            """
            CREATE TABLE IF NOT EXISTS model_fingerprints (
                component TEXT PRIMARY KEY,
                fingerprint TEXT NOT NULL,
                status TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
    )
    session.exec(
        text(
            """
            CREATE TABLE IF NOT EXISTS face_embedding_migration (
                face_id INTEGER PRIMARY KEY,
                fingerprint TEXT NOT NULL,
                outcome TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
    )
    session.exec(
        text(
            """
            INSERT OR REPLACE INTO model_fingerprints(
                component, fingerprint, status, updated_at
            ) VALUES (
                'face_embeddings', :fingerprint, 'migrating', CURRENT_TIMESTAMP
            )
            """
        ).bindparams(fingerprint=FACE_MODEL_FINGERPRINT)
    )
    safe_commit(session)


def _rebuild_people(session: Session) -> None:
    session.exec(text("DELETE FROM person_embeddings"))
    people = session.exec(select(Person.id).order_by(Person.id)).all()
    for person_id in people:
        stored = session.exec(
            text(
                """
                SELECT embedding
                FROM face_embeddings
                WHERE person_id = :person_id
                """
            ).bindparams(person_id=person_id)
        ).all()
        vectors = [
            vector
            for (embedding,) in stored
            if (vector := vector_from_stored(embedding)) is not None
        ]
        if not vectors:
            continue
        centroid = np.mean(np.stack(vectors).astype(np.float32), axis=0)
        norm = float(np.linalg.norm(centroid))
        if not np.isfinite(norm) or norm <= 0.0:
            continue
        blob = vector_to_blob(centroid / norm)
        session.exec(
            text(
                """
                INSERT INTO person_embeddings(person_id, embedding)
                VALUES (:person_id, :embedding)
                """
            ).bindparams(person_id=person_id, embedding=blob)
        )
    safe_commit(session)


def migrate(batch_size: int, limit: int | None) -> None:
    backup = _backup_database()
    print(f"Integrity-checked backup: {backup}")
    client = AdaFaceSocketAnalysis(
        settings.face_recognition.inference_socket_path,
        settings.face_recognition.inference_timeout_seconds,
    )
    health = client.health()
    print(f"Face service: {health['model']} on {health['runtime']['actualCompute']}")

    with Session(engine) as session:
        _initialize_state(session)
        completed = (
            select(text("face_id"))
            .select_from(text("face_embedding_migration"))
            .where(text("fingerprint = :fingerprint"))
            .params(fingerprint=FACE_MODEL_FINGERPRINT)
        )
        query = (
            select(Face)
            .where(Face.thumbnail_path.is_not(None), Face.id.not_in(completed))
            .order_by(Face.id)
        )
        if limit is not None:
            query = query.limit(limit)
        faces = session.exec(query).all()
        migrated = 0
        skipped = 0
        for face in faces:
            outcome = "no_embedding"
            embedding = None
            try:
                thumbnail = settings.general.thumb_dir / face.thumbnail_path
                with Image.open(thumbnail) as image:
                    rgb = np.asarray(ImageOps.exif_transpose(image).convert("RGB"))
                detected = client.get(rgb)
                if detected:
                    embedding = _largest_face(detected).embedding
            except (OSError, RuntimeError):
                outcome = "unreadable_thumbnail"

            session.exec(
                text("DELETE FROM face_embeddings WHERE face_id = :face_id").bindparams(
                    face_id=face.id
                )
            )
            if embedding is not None:
                normalized = np.asarray(embedding, dtype=np.float32)
                norm = float(np.linalg.norm(normalized))
                if np.isfinite(norm) and norm > 0.0:
                    normalized /= norm
                    session.exec(
                        text(
                            """
                            INSERT INTO face_embeddings(face_id, person_id, embedding)
                            VALUES (:face_id, :person_id, :embedding)
                            """
                        ).bindparams(
                            face_id=face.id,
                            person_id=face.person_id
                            if face.person_id is not None
                            else -1,
                            embedding=vector_to_blob(normalized),
                        )
                    )
                    outcome = "migrated"
                    migrated += 1
            if outcome != "migrated":
                skipped += 1
            session.exec(
                text(
                    """
                    INSERT OR REPLACE INTO face_embedding_migration(
                        face_id, fingerprint, outcome, updated_at
                    ) VALUES (:face_id, :fingerprint, :outcome, CURRENT_TIMESTAMP)
                    """
                ).bindparams(
                    face_id=face.id,
                    fingerprint=FACE_MODEL_FINGERPRINT,
                    outcome=outcome,
                )
            )
            if (migrated + skipped) % batch_size == 0:
                safe_commit(session)
                print(
                    f"Processed {migrated + skipped}: {migrated} migrated, {skipped} skipped"
                )
        safe_commit(session)

        remaining = session.exec(
            select(Face.id)
            .where(Face.thumbnail_path.is_not(None), Face.id.not_in(completed))
            .limit(1)
        ).first()
        if remaining is None:
            _rebuild_people(session)
            session.exec(
                text(
                    """
                    UPDATE model_fingerprints
                    SET status = 'ready', updated_at = CURRENT_TIMESTAMP
                    WHERE component = 'face_embeddings'
                      AND fingerprint = :fingerprint
                    """
                ).bindparams(fingerprint=FACE_MODEL_FINGERPRINT)
            )
            safe_commit(session)
            print("AdaFace migration complete; person centroids rebuilt.")
        else:
            print("Partial migration committed; rerun without --limit to finish.")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--limit", type=int)
    arguments = parser.parse_args()
    if not arguments.apply:
        raise SystemExit("Refusing to mutate the catalog without --apply")
    if arguments.batch_size < 1 or arguments.batch_size > 1000:
        raise SystemExit("--batch-size must be between 1 and 1000")
    migrate(arguments.batch_size, arguments.limit)


if __name__ == "__main__":
    main()
