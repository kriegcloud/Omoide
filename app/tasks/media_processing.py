from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image
from sqlalchemy import func, or_
from sqlmodel import Session, col, select

import app.database as db
from app.concurrency import heavy_writer
from app.config import settings
from app.image_limits import apply_pillow_limits
from app.database import safe_commit
from app.logger import logger
from app.models import Face, Media, ProcessingTask, Status
from app.processor_registry import load_processors, processors
from app.services.face_matching import match_faces_to_persons, matching_thresholds
from app.utils import split_video
from .state import clear_task_progress, set_task_progress
from .common import _start_task, _finish_task

__all__ = [
    "run_media_processing",
    "run_media_processing_and_chain",
    "run_single_processor",
    "run_processors_for_media",
]


def _is_task_cancelled(task_id: str) -> bool:
    """Check cancellation using a fresh session.

    The long-lived processing session holds an open transaction whose snapshot
    predates any cancel committed by the API endpoint.  A throw-away session
    always starts a new transaction and therefore reads the latest committed
    status, making cancel responsive within a single item rather than a full
    batch.
    """
    try:
        with Session(db.engine) as s:
            task = s.get(ProcessingTask, task_id)
            return task is not None and task.status in ("cancelled", "interrupted", "failed")
    except Exception:
        return False


def _media_processing_conditions() -> list:
    """Return filter clauses for media rows needing processing."""
    conditions: list = []
    active_processors = {
        proc.name for proc in processors if getattr(proc, "active", False)
    }
    if active_processors & {"faces", "embedding_extractor", "auto_tagger"}:
        conditions.append(Media.extracted_scenes.is_(False))
    flag_columns = {
        "faces": Media.faces_extracted,
        "auto_tagger": Media.ran_auto_tagging,
        "embedding_extractor": Media.embeddings_created,
    }
    for name, column in flag_columns.items():
        if name in active_processors:
            conditions.append(column.is_(False))
    if "blur" in active_processors:
        conditions.append(col(Media.laplacian_score).is_(None))
    return conditions


def _count_media_to_process(session: Session) -> int:
    conditions = _media_processing_conditions()
    if not conditions:
        return 0
    return (
        session.exec(
            select(func.count(Media.id)).where(
                or_(*conditions),
                col(Media.missing_since).is_(None),
                col(Media.processing_error).is_(None),
            )
        ).first()
        or 0
    )


def _fetch_media_batch_to_process(session: Session, limit: int, after_id: int = 0, retry_ids: set[int] | None = None) -> list[Media]:
    conditions = _media_processing_conditions()
    if not conditions:
        return []
    return session.exec(
        select(Media)
        .where(
            or_(*conditions),
            col(Media.missing_since).is_(None),
            col(Media.processing_error).is_(None),
        )
        .where(or_(Media.id > after_id, col(Media.id).in_(retry_ids or [])))
        .order_by(Media.id.asc())
        .limit(limit)
    ).all()


def _get_or_extract_scenes(
    media: Media, session: Session
) -> list[Image.Image | tuple]:
    media_path_obj = Path(media.path)
    suffix = media_path_obj.suffix.lower()

    if media.extracted_scenes and suffix not in settings.scan.IMAGE_SUFFIXES:
        return media.scenes

    try:
        if suffix in settings.scan.IMAGE_SUFFIXES:
            scenes = [Image.open(media_path_obj)]
        else:
            scenes = split_video(media, media_path_obj)
    except FileNotFoundError:
        logger.warning("File not found: %s. Marking missing.", media.path)
        media.missing_since = datetime.now(timezone.utc)
        session.add(media)
        return []
    except Exception as exc:
        logger.warning("Failed to extract frames for %s: %s", media.path, exc)
        media.processing_error = (
            f"Failed to extract frames: {type(exc).__name__}: {exc}"
        )[:500]
        session.add(media)
        return []

    if not scenes:
        media.processing_error = "Scene extraction produced no frames."
        session.add(media)
        return []

    media.extracted_scenes = True
    session.add(media)

    for scene in scenes:
        if isinstance(scene, tuple) and hasattr(scene[0], "id"):
            session.add(scene[0])

    return scenes


def _processor_failure(media: Media, session: Session, name: str, detail: str) -> bool:
    # Only the failed processor loses its completion marker; previous successes
    # remain usable. The review/retry flow clears processing_error explicitly.
    flag = {"faces": "faces_extracted", "embedding_extractor": "embeddings_created",
            "auto_tagger": "ran_auto_tagging"}.get(name)
    if flag:
        setattr(media, flag, False)
    media.processing_error = f"Processor '{name}' failed: {detail}"[:500]
    session.add(media)
    return False


def _process_one(proc, media: Media, scenes: list, session: Session, task_id: str | None = None) -> bool:
    if task_id:
        set_task_progress(task_id, current_item=os.fspath(media.path), current_step=proc.name)
    if not scenes and not getattr(proc, "handles_empty_scenes", False):
        return _processor_failure(media, session, proc.name, media.processing_error or "no scenes available")
    try:
        if media.processing_error and media.processing_error.startswith(f"Processor '{proc.name}' failed:"):
            media.processing_error = None
        if not proc.process(media, session, scenes=scenes):
            return _processor_failure(media, session, proc.name, media.processing_error or "processor returned failure")
        condition = getattr(proc, "get_pending_condition", lambda: None)()
        if condition is not None:
            session.flush()
            pending = session.exec(select(Media.id).where(Media.id == media.id, condition)).first()
            if pending is not None:
                return _processor_failure(media, session, proc.name, "no progress: completion condition is still pending")
        return True
    except Exception as exc:
        logger.exception("Processor '%s' raised on media %s", proc.name, media.path)
        return _processor_failure(media, session, proc.name, f"{type(exc).__name__}: {exc}")


def _apply_processors(
    media: Media, scenes: list, session: Session, task_id: str | None = None
) -> bool:
    if not scenes:
        media.processing_error = media.processing_error or "Scene extraction produced no frames."
        session.add(media)
        return False
    for proc in processors:
        if not proc.active:
            continue
        if task_id:
            set_task_progress(task_id, current_item=os.fspath(media.path), current_step=proc.name)
        if not _process_one(proc, media, scenes, session):
            return False
    return True


def _checkpoint_faces(session: Session, task: ProcessingTask, media: Media) -> int:
    params = task.params or {}
    if params.get("matching_media_id") is not None:
        _save_pending_faces(session, task, set(_created_face_ids(
            session, params["matching_media_id"], params.get("matching_after_face_id", 0))))
    before_id = _latest_face_id(session)
    task.params = {**(task.params or {}), "matching_media_id": media.id,
                   "matching_after_face_id": before_id}
    session.add(task)
    safe_commit(session)
    return before_id


def _save_pending_faces(session: Session, task: ProcessingTask, face_ids: set[int]) -> None:
    params = dict(task.params or {})
    if params.get("matching_media_id") is not None:
        face_ids = face_ids | set(_created_face_ids(
            session, params["matching_media_id"], params.get("matching_after_face_id", 0)))
    params["pending_face_ids"] = sorted(set(params.get("pending_face_ids", [])) | face_ids)
    params.pop("matching_media_id", None)
    params.pop("matching_after_face_id", None)
    task.params = params
    session.add(task)


def _finish_processing_task(session: Session, task: ProcessingTask, failures: int = 0) -> None:
    _finish_task(session, task, "failed" if failures else "completed")
    clear_task_progress(task.id)


def _prepare_failed_retries(session: Session, task: ProcessingTask) -> set[int]:
    failed_ids = set((task.params or {}).get("failed_media_ids", []))
    for media in session.exec(select(Media).where(col(Media.id).in_(failed_ids))).all():
        media.processing_error = None
        session.add(media)
    safe_commit(session)
    return failed_ids


def _latest_face_id(session: Session) -> int:
    # Flush reset deletions first: SQLite may reuse the deleted highest row ID.
    session.flush()
    return session.exec(select(func.max(Face.id))).one() or 0


def _created_face_ids(session: Session, media_id: int, after_id: int) -> list[int]:
    session.flush()
    return session.exec(
        select(Face.id).where(Face.media_id == media_id, Face.id > after_id)
    ).all()


def _record_face_matches(
    session: Session, task: ProcessingTask, face_ids: set[int], *, stopped: bool = False
) -> None:
    params = task.params or {}
    face_ids = set(face_ids) | set(params.get("pending_face_ids", []))
    if params.get("matching_media_id") is not None:
        face_ids.update(_created_face_ids(session, params["matching_media_id"], params.get("matching_after_face_id", 0)))
    _save_pending_faces(session, task, face_ids)
    matched = 0
    if (face_ids and not stopped and settings.face_recognition.match_new_faces_on_index
            and task.status not in ("cancelled", "interrupted", "failed")):
        set_task_progress(task.id, current_step="matching_known_persons")
        threshold, min_margin = matching_thresholds()
        matched = len(match_faces_to_persons(
            session, sorted(face_ids), threshold=threshold, min_margin=min_margin
        ))
        task.params = {**task.params, "pending_face_ids": []}
    task.result = {**(task.result or {}), "faces_matched": (task.result or {}).get("faces_matched", 0) + matched}
    session.add(task)



def run_media_processing_and_chain(task_id: str) -> None:
    clustering_chained = (
        settings.general.enable_people and settings.scan.auto_cluster_on_scan
    )
    run_media_processing(task_id, clustering_chained=clustering_chained)

    logger.info("Media processing finished.")
    with Session(db.engine) as session:
        previous = session.get(ProcessingTask, task_id)
        if previous is None or previous.status != Status.COMPLETED:
            return
    if clustering_chained:
        logger.info("Starting Person Clustering...")
        with Session(db.engine) as new_session:
            next_task = ProcessingTask(
                task_type="cluster_persons", total=0, processed=0, params={}
            )
            new_session.add(next_task)
            new_session.commit()
            new_session.refresh(next_task)

        from .person_clustering import run_person_clustering

        run_person_clustering(next_task.id)
    logger.info("Task chain completed")


def run_media_processing(task_id: str, *, clustering_chained: bool = False) -> None:
    apply_pillow_limits(settings.scan.max_image_pixels)
    try:
        _run_media_processing(task_id, clustering_chained=clustering_chained)
    except Exception:
        logger.exception("Unhandled error in run_media_processing (task %s)", task_id)
        try:
            with Session(db.engine) as s:
                task = s.get(ProcessingTask, task_id)
                if task:
                    _finish_task(s, task, "failed")
        except Exception:
            logger.exception("Failed to mark task %s as failed", task_id)
        clear_task_progress(task_id)


def _run_media_processing(task_id: str, *, clustering_chained: bool = False) -> None:
    configured_batch_size = getattr(
        settings.processors, "media_batch_size", None
    )
    try:
        batch_size = int(configured_batch_size or 0)
    except (TypeError, ValueError):
        batch_size = 0
    if batch_size <= 0:
        batch_size = 100

    with Session(db.engine) as session:
        task = session.get(ProcessingTask, task_id)
        if not task:
            logger.error("Task with id %s not found!", task_id)
            return
        if task.status in ("cancelled", "interrupted", "failed"):
            return

        if not _start_task(session, task):
            return

        set_task_progress(task_id, current_step="preparing", current_item=None)

        def is_cancelled() -> bool:
            return _is_task_cancelled(task_id)

        if not processors:
            logger.debug("Processor registry empty; loading processors now.")
            set_task_progress(task_id, current_step="loading_models")
            load_processors()

        with heavy_writer(
            name="process_media", cancelled=is_cancelled
        ) as acquired:
            if not acquired:
                _finish_processing_task(session, task)
                return

            for proc in processors:
                proc.active = False
                proc.load_model()

            collect_faces = (
                settings.face_recognition.match_new_faces_on_index
                and not clustering_chained
                and any(proc.name == "faces" and proc.active for proc in processors)
            )
            new_face_ids: set[int] = set((task.params or {}).get("pending_face_ids", []))
            task.total = _count_media_to_process(session)
            session.add(task)
            safe_commit(session)

            failures = 0
            retry_ids = _prepare_failed_retries(session, task)
            failed_ids = set(retry_ids)
            last_media_id = int((task.params or {}).get("last_media_id", 0))
            batch_index = 0
            while True:
                if _is_task_cancelled(task_id):
                    logger.info("Task cancelled. Stopping before next batch.")
                    break

                medias_batch = _fetch_media_batch_to_process(session, batch_size, last_media_id, retry_ids)
                if not medias_batch:
                    logger.info("No more media to process. Finishing.")
                    break

                logger.info(
                    "Processing batch of %d media items...",
                    len(medias_batch),
                )

                batch_dirty = False
                cancelled_mid_batch = False

                for media in medias_batch:
                    if _is_task_cancelled(task_id):
                        logger.info("Task cancelled mid-batch. Stopping.")
                        cancelled_mid_batch = True
                        break

                    last_media_id = max(last_media_id, media.id)
                    retry_ids.discard(media.id)
                    media_path = Path(media.path) if media.path else None

                    if media_path is None or not media_path.exists():
                        if not media.missing_since:
                            media.missing_since = datetime.now(timezone.utc)
                            session.add(media)
                            batch_dirty = True
                        continue
                    if media.missing_since:
                        media.missing_since = None
                        media.missing_confirmed = False
                        session.add(media)
                        batch_dirty = True

                    logger.info("Processing: %s", media.filename)
                    set_task_progress(
                        task_id,
                        current_item=os.fspath(media.path),
                        current_step="extracting_scenes",
                    )
                    scenes = _get_or_extract_scenes(media, session)
                    logger.debug(
                        "Scenes for %s: %s",
                        media.filename,
                        len(scenes) if scenes is not None else 0,
                    )
                    if not scenes and (
                        media_path is None or not media_path.exists()
                    ):
                        batch_dirty = True
                        set_task_progress(task_id, current_step="idle")
                        continue

                    before_id = _checkpoint_faces(session, task, media) if collect_faces else None
                    succeeded = _apply_processors(media, scenes, session, task_id=task_id)
                    failures += int(not succeeded)
                    if succeeded:
                        failed_ids.discard(media.id)
                    else:
                        failed_ids.add(media.id)
                    if before_id is not None:
                        new_face_ids.update(
                            _created_face_ids(session, media.id, before_id)
                        )
                    _save_pending_faces(session, task, new_face_ids)
                    task.params = {**(task.params or {}), "last_media_id": last_media_id,
                                   "failed_media_ids": sorted(failed_ids)}
                    session.add(media)

                    task.processed += 1
                    batch_dirty = True
                    session.add(task)
                    # Commit per item so the SQLite write lock is held for one
                    # item's work, not a whole batch; other writers time out at
                    # 30 s otherwise.
                    safe_commit(session)
                    set_task_progress(task_id, current_step="idle")

                batch_index += 1
                if batch_dirty:
                    # Recounting is only needed to track records deleted by
                    # processors; refreshing every few batches is enough.
                    if batch_index % 5 == 0:
                        remaining = _count_media_to_process(session)
                        task.total = task.processed + remaining
                    session.add(task)
                    safe_commit(session)

                if cancelled_mid_batch:
                    break

            for proc in processors:
                try:
                    proc.unload()
                except Exception:
                    pass

            session.refresh(task)
            remaining = _count_media_to_process(session)
            task.total = task.processed + remaining
            _record_face_matches(session, task, new_face_ids)
            session.add(task)
            safe_commit(session)
            _finish_processing_task(session, task, failures)


def run_single_processor(
    task_id: str, processor_name: str, force: bool = False
) -> None:
    """Run one named processor over all media.

    force=False  – skip media already processed by this processor (default)
    force=True   – reset each item's result and reprocess everything
    """
    if not processors:
        load_processors()

    target = next((p for p in processors if p.name == processor_name), None)
    if target is None:
        logger.error("Processor '%s' not found.", processor_name)
        return

    target.load_model()
    target.active = True  # run regardless of per-processor config flag

    pending_condition = None if force else target.get_pending_condition()
    batch_size = 100
    with Session(db.engine) as session:
        task = session.get(ProcessingTask, task_id)
        if not task or task.status in ("cancelled", "interrupted", "failed"):
            return
        if not _start_task(session, task):
            return
        retry_ids = _prepare_failed_retries(session, task)
        failed_ids = set(retry_ids)
        params = dict(task.params or {})
        params.update(processor_name=processor_name, force=force)
        last_id = int(params.get("last_media_id", 0))
        upper_id = params.get("upper_media_id")
        if upper_id is None:
            upper_id = session.exec(select(func.max(Media.id))).one() or 0
        params["upper_media_id"] = upper_id
        task.params = params
        session.add(task)
        safe_commit(session)
        failures = 0
        try:
            with heavy_writer(name=f"run_processor_{processor_name}",
                              cancelled=lambda: _is_task_cancelled(task_id)) as acquired:
                if not acquired:
                    return
                def candidates():
                    stmt = select(Media).where(or_(Media.id > last_id, col(Media.id).in_(retry_ids)), Media.id <= upper_id,
                        col(Media.missing_since).is_(None), col(Media.processing_error).is_(None))
                    if pending_condition is not None:
                        stmt = stmt.where(pending_condition)
                    return stmt
                task.total = session.exec(select(func.count()).select_from(candidates().subquery())).one()
                session.add(task)
                safe_commit(session)
                while not _is_task_cancelled(task_id):
                    batch = session.exec(candidates().order_by(Media.id).limit(batch_size)).all()
                    if not batch:
                        break
                    for media in batch:
                        if _is_task_cancelled(task_id):
                            break
                        media_path = Path(media.path)
                        if not media_path.exists():
                            media.missing_since = datetime.now(timezone.utc)
                            session.add(media)
                        else:
                            if force:
                                target.reset_for_media(media, session)
                            before_id = (_checkpoint_faces(session, task, media)
                                         if processor_name == "faces" and settings.face_recognition.match_new_faces_on_index else None)
                            set_task_progress(task_id, current_item=os.fspath(media.path), current_step="extracting_scenes")
                            scenes = _get_or_extract_scenes(media, session)
                            succeeded = _process_one(target, media, scenes, session, task_id)
                            failures += int(not succeeded)
                            if succeeded:
                                failed_ids.discard(media.id)
                            else:
                                failed_ids.add(media.id)
                            if before_id is not None:
                                _save_pending_faces(session, task, set(_created_face_ids(session, media.id, before_id)))
                        last_id = max(last_id, media.id)
                        retry_ids.discard(media.id)
                        task.params = {**task.params, "last_media_id": last_id,
                                       "failed_media_ids": sorted(failed_ids)}
                        task.processed += 1
                        session.add(task)
                        safe_commit(session)
                session.refresh(task)
                _record_face_matches(session, task, set())
                session.add(task)
                safe_commit(session)
        except Exception:
            failures += 1
            raise
        finally:
            try:
                target.unload()
            finally:
                _finish_processing_task(session, task, failures)


def edit_processor_names() -> list[str]:
    """Processors to run on an edited/repaired copy, honouring the active flags.

    Disabled processors never load their models (the CLIP extractor has no
    ``_preprocess`` until ``load_model`` runs), so asking for them fails the
    whole follow-up run.
    """
    names: list[str] = []
    if settings.processors.face_processor_active:
        names.append("faces")
    if settings.processors.image_embedding_processor_active:
        names.append("embedding_extractor")
    if settings.tagging.auto_tagging:
        names.append("auto_tagger")
    if settings.processors.blur_processor_active:
        names.append("blur")
    if settings.processors.exif_processor_active:
        names.append("exif")
    return names


def run_processors_for_media(
    task_id: str, processor_names: list[str], media_ids: list[int]
) -> None:
    """Reprocess selected items, checkpointing successful processor/item pairs."""
    if not processors:
        load_processors()
    targets = [p for p in processors if p.name in processor_names]
    with Session(db.engine) as session:
        task = session.get(ProcessingTask, task_id)
        if not task or task.status in ("cancelled", "interrupted", "failed"):
            return
        if not _start_task(session, task):
            return
        task.params = {**(task.params or {}), "processor_names": processor_names, "media_ids": media_ids}
        completed = {name: set(ids) for name, ids in task.params.get("completed_media", {}).items()}
        task.total = sum(sum(mid not in completed.get(target.name, set()) for mid in media_ids) for target in targets)
        session.add(task)
        safe_commit(session)
        failures = int(not targets)
        try:
            with heavy_writer(name="run_processors_for_media",
                              cancelled=lambda: _is_task_cancelled(task_id)) as acquired:
                if not acquired:
                    return
                for target in targets:
                    if _is_task_cancelled(task_id):
                        break
                    try:
                        target.load_model()
                        target.active = True
                    except Exception as exc:
                        logger.exception("Processor %s failed to load", target.name)
                        failures += 1
                        task.result = {**(task.result or {}), "error": f"{target.name}: {exc}"[:500]}
                        session.add(task)
                        safe_commit(session)
                        continue
                    done = completed.setdefault(target.name, set())
                    try:
                        for media_id in media_ids:
                            if _is_task_cancelled(task_id):
                                break
                            if media_id in done:
                                continue
                            media = session.get(Media, media_id)
                            if media is None:
                                done.add(media_id)
                            elif not Path(media.path).exists():
                                media.missing_since = datetime.now(timezone.utc)
                                session.add(media)
                                done.add(media_id)
                            else:
                                target.reset_for_media(media, session)
                                before_id = (_checkpoint_faces(session, task, media)
                                             if target.name == "faces" and settings.face_recognition.match_new_faces_on_index else None)
                                set_task_progress(task_id, current_item=os.fspath(media.path), current_step="extracting_scenes")
                                scenes = _get_or_extract_scenes(media, session)
                                succeeded = _process_one(target, media, scenes, session, task_id)
                                if before_id is not None:
                                    _save_pending_faces(session, task, set(_created_face_ids(session, media.id, before_id)))
                                if succeeded:
                                    done.add(media_id)
                                else:
                                    failures += 1
                                session.add(media)
                            task.processed += 1
                            task.params = {**task.params, "completed_media": {name: sorted(ids) for name, ids in completed.items()}}
                            session.add(task)
                            safe_commit(session)
                    finally:
                        target.unload()
                session.refresh(task)
                _record_face_matches(session, task, set(), stopped=_is_task_cancelled(task_id))
                session.add(task)
                safe_commit(session)
        except Exception:
            failures += 1
            raise
        finally:
            _finish_processing_task(session, task, failures)
