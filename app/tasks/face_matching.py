"""Pose-aware batch matching with conditional ownership and provenance.

The upstream module split shares the fork scorer/threshold policy rather than
introducing the upstream video's separate lower acceptance threshold.
"""
from __future__ import annotations
import numpy as np
from sqlmodel import Session
import app.database as db
from app.config import settings
from app.database import safe_commit
from app.logger import logger
from app.models import ProcessingTask
from app.services.face_matching import (PrototypeIndex, _bulk_assign_faces_to_persons,
    _load_person_prototype_matrix, matching_thresholds, score_faces)
from app.utils import refresh_persons
from .clustering_queries import (is_task_cancelled as _is_task_cancelled,
    fetch_faces_and_embeddings as _fetch_faces_and_embeddings,
    get_face_total as _get_face_total)
from .state import set_task_progress


def _match_unassigned_to_existing(
    session: Session,
    face_ids: list[int],
    embeddings: np.ndarray,
    task_id: str,
    prototypes: tuple[np.ndarray, np.ndarray] | None = None,
    _hooks=None,
) -> list[int]:
    """
    Attempts to assign faces to existing persons by comparing against
    per-person pose prototypes. Returns a list of face_ids that were NOT
    assigned and still need clustering. ``prototypes`` allows callers that
    loop over batches to load the (person_ids, matrix) pair once.
    """
    _hooks = _hooks or {}
    _load_person_prototype_matrix = _hooks.get("_load_person_prototype_matrix", globals()["_load_person_prototype_matrix"])
    _is_task_cancelled = _hooks.get("_is_task_cancelled", globals()["_is_task_cancelled"])
    _bulk_assign_faces_to_persons = _hooks.get("_bulk_assign_faces_to_persons", globals()["_bulk_assign_faces_to_persons"])
    refresh_persons = _hooks.get("refresh_persons", globals()["refresh_persons"])
    safe_commit = _hooks.get("safe_commit", globals()["safe_commit"])
    set_task_progress = _hooks.get("set_task_progress", globals()["set_task_progress"])
    if not face_ids:
        return []

    if prototypes is None:
        prototypes = _load_person_prototype_matrix(session)
    person_ids, person_matrix = prototypes
    if person_matrix.size == 0:
        logger.info("No existing persons found, skipping to clustering!")
        return face_ids
    if (
        embeddings.ndim != 2
        or person_matrix.ndim != 2
        or embeddings.shape[1] != person_matrix.shape[1]
    ):
        logger.warning(
            "Skipping single-face match due to embedding dim mismatch"
            " (faces=%s persons=%s)",
            tuple(embeddings.shape),
            tuple(person_matrix.shape),
        )
        return face_ids

    threshold_cosine, min_margin = matching_thresholds()
    index = PrototypeIndex(person_ids, person_matrix, {})

    unassigned_after_match = []
    assignments: dict[int, int] = {}
    chunk_size = 512
    cancel_check_every = max(
        1,
        int(getattr(settings.face_recognition, "matching_cancel_check_every", 8)),
    )
    cancelled = False

    logger.info("Attempting to match %d faces to existing persons...", len(face_ids))
    total_faces = len(face_ids)
    for start in range(0, total_faces, chunk_size):
        end = min(start + chunk_size, total_faces)
        chunk_number = (start // chunk_size) + 1
        if (
            start == 0 or chunk_number % cancel_check_every == 0
        ) and _is_task_cancelled(session, task_id):
            logger.info(
                "Task %s cancelled during matching; stopping at %d/%d faces.",
                task_id,
                start,
                total_faces,
            )
            unassigned_after_match.extend(int(fid) for fid in face_ids[start:])
            cancelled = True
            break

        chunk_face_ids = face_ids[start:end]
        matches = score_faces(
            index, chunk_face_ids, embeddings[start:end], [None] * len(chunk_face_ids)
        )
        accepted = {
            match.face_id: match.person_id
            for match in matches
            if match.score >= threshold_cosine and match.margin >= min_margin
        }
        assignments.update(accepted)
        unassigned_after_match.extend(
            int(face_id) for face_id in chunk_face_ids if face_id not in accepted
        )

        if start == 0 or (start // chunk_size) % 8 == 0:
            set_task_progress(
                task_id,
                current_step="matching_known_persons",
                current_item=f"Matching face {end}/{total_faces} in current batch",
            )

    if assignments:
        proposed = assignments
        assignments = _bulk_assign_faces_to_persons(session, proposed)
        # The caller derives this worker's matched count from the remaining
        # ids; a manual assignment that won the race is not an auto match.
        unassigned_after_match.extend(fid for fid in proposed if fid not in assignments)
        refresh_persons(session, set(assignments.values()))
        safe_commit(session)
        logger.info("Matched %d faces to existing persons.", len(assignments))
    if cancelled:
        logger.info("Matching was cancelled; leaving remaining faces unassigned.")

    return unassigned_after_match


def _match_remaining_single_faces(
    task_id: str, *, batch_size: int,
    _hooks=None,
) -> tuple[int, int, bool]:
    _hooks = _hooks or {}
    _load_person_prototype_matrix = _hooks.get("_load_person_prototype_matrix", globals()["_load_person_prototype_matrix"])
    _is_task_cancelled = _hooks.get("_is_task_cancelled", globals()["_is_task_cancelled"])
    safe_commit = _hooks.get("safe_commit", globals()["safe_commit"])
    set_task_progress = _hooks.get("set_task_progress", globals()["set_task_progress"])
    _get_face_total = _hooks.get("_get_face_total", globals()["_get_face_total"])
    _fetch_faces_and_embeddings = _hooks.get("_fetch_faces_and_embeddings", globals()["_fetch_faces_and_embeddings"])
    _match_unassigned_to_existing = _hooks.get("_match_unassigned_to_existing", globals()["_match_unassigned_to_existing"])
    total_scanned = 0
    total_matched = 0
    last_id = 0
    cancelled = False

    set_task_progress(
        task_id,
        current_step="matching_known_persons",
        current_item="starting leftover single-face pass",
    )
    # Load per-person prototypes once for the whole pass instead of once per
    # batch (persons do not change during matching). Also repoint the task's
    # processed/total at this phase — the merge phase left them at its own
    # counts, which made the progress bar meaningless for the rest of the run.
    with Session(db.engine) as session:
        prototypes = _load_person_prototype_matrix(session)
        remaining_total = _get_face_total(session)
        task = session.get(ProcessingTask, task_id)
        if task and task.status == "running":
            task.processed = 0
            task.total = remaining_total
            session.add(task)
            safe_commit(session)
    while True:
        with Session(db.engine) as session:
            face_ids, embeddings = _fetch_faces_and_embeddings(
                session, limit=batch_size, last_id=last_id
            )
            if not face_ids:
                break
            last_id = face_ids[-1]

            still_unassigned_ids = _match_unassigned_to_existing(
                session,
                face_ids,
                embeddings,
                task_id,
                prototypes=prototypes,
            )
            if _is_task_cancelled(session, task_id):
                cancelled = True

        scanned = len(face_ids)
        matched = scanned - len(still_unassigned_ids)
        total_scanned += scanned
        total_matched += matched

        set_task_progress(
            task_id,
            current_step="matching_known_persons",
            current_item=(
                f"leftover pass scanned={total_scanned} matched={total_matched}"
            ),
        )
        with Session(db.engine) as progress_session:
            task = progress_session.get(ProcessingTask, task_id)
            if task and task.status == "running":
                task.processed = total_scanned
                progress_session.add(task)
                safe_commit(progress_session)

        if cancelled:
            logger.info(
                "Task %s cancelled during leftover matching; stopping pass.", task_id
            )
            break
        if scanned < batch_size:
            break

    return total_matched, total_scanned, cancelled


match_remaining_single_faces = _match_remaining_single_faces
