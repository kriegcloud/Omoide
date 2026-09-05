from __future__ import annotations

import numpy as np
from sqlalchemy.exc import OperationalError
from sqlmodel import Session

from app.utils import get_person_embedding


def decode_embedding(value: object) -> np.ndarray | None:
    if value is None:
        return None
    try:
        vector = np.frombuffer(bytes(value), dtype=np.float32).copy()
    except (TypeError, ValueError):
        return None
    return vector if vector.size else None


def normalize_embedding(value: object) -> np.ndarray | None:
    vector = decode_embedding(value) if not isinstance(value, np.ndarray) else value
    if vector is None:
        return None
    vector = np.asarray(vector, dtype=np.float32).reshape(-1)
    norm = float(np.linalg.norm(vector))
    if not np.isfinite(norm) or norm <= 0.0:
        return None
    return vector / norm


def person_centroid(session: Session, person_id: int | None) -> np.ndarray | None:
    if person_id is None:
        return None
    try:
        return normalize_embedding(get_person_embedding(session, person_id))
    except OperationalError:
        return None


def cosine_distance(a: np.ndarray, b: np.ndarray) -> float:
    left = normalize_embedding(np.asarray(a, dtype=np.float32))
    right = normalize_embedding(np.asarray(b, dtype=np.float32))
    if left is None or right is None:
        return 1.0
    return float(1.0 - np.clip(np.dot(left, right), -1.0, 1.0))
