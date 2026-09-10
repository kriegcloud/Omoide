import io
import re
import threading
import time
import weakref
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime

import numpy as np
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from PIL import Image
from sqlalchemy import and_, desc, func, or_, text, tuple_
from sqlmodel import Session, select

from app.config import get_clip_bundle, retain_clip_for_search, settings
from app.api._media_filters import exclude_missing
from app.database import get_session
from app.logger import logger
from app.models import Media, MediaTagLink, Person, Scene, Tag
from app.schemas.media import MediaPreview
from app.schemas.person import PersonRead, PersonReadSimple
from app.schemas.search import (
    CombinedMediaSearchResult,
    CursorPage,
    SceneSearchResult,
)
from app.schemas.tag import TagRead
from app.utils import vector_to_blob

router = APIRouter()

# Candidate window for each relevance page; date ordering uses one bounded
# candidate set. Relevance cursors and exact tie recovery can advance beyond it.
_MAX_KNN_RESULTS = 300

# Derive raw sqlite-vec candidate predicates from the shared browsing rule.
# Filtering inside the vector query prevents missing files using up its k slots.
_VISIBLE_MEDIA_SQL = str(exclude_missing(select(Media.id)))
_VISIBLE_SCENES_SQL = str(exclude_missing(
    select(Scene.id).join(Media, Scene.media_id == Media.id)
))

# ---------------------------------------------------------------------------
# In-memory person name cache
#
# Rebuilt from the DB at most once per TTL period. Only (id, name) pairs are
# stored — no full ORM objects. A single compiled regex covers all names so
# matching is O(len(query)) regardless of how many persons exist.
# ---------------------------------------------------------------------------

@dataclass
class _PersonNameCache:
    entries: list[tuple[int, str]]  # sorted by name length desc
    name_to_id: dict[str, int]      # lowercase name → person id
    pattern: re.Pattern | None      # single compiled alternation regex
    loaded_at: float
    database_identity: object


_person_cache: _PersonNameCache | None = None
_PERSON_CACHE_TTL = 60.0  # seconds


def _build_person_cache(session: Session) -> _PersonNameCache:
    rows = session.exec(
        text("SELECT id, name FROM person WHERE name IS NOT NULL AND TRIM(name) != ''")
    ).all()
    entries: list[tuple[int, str]] = sorted(
        [(int(r[0]), str(r[1])) for r in rows],
        key=lambda e: len(e[1]),
        reverse=True,  # longest names first so "John Smith" beats "John"
    )
    name_to_id = {name.lower(): pid for pid, name in entries}
    pattern = (
        re.compile(
            r"\b(?:" + "|".join(re.escape(n) for _, n in entries) + r")\b",
            re.IGNORECASE,
        )
        if entries
        else None
    )
    return _PersonNameCache(
        entries=entries,
        name_to_id=name_to_id,
        pattern=pattern,
        loaded_at=time.monotonic(),
        database_identity=session.get_bind(),
    )


def _get_person_cache(session: Session) -> _PersonNameCache:
    global _person_cache
    if (
        _person_cache is None
        or _person_cache.database_identity is not session.get_bind()
        or (time.monotonic() - _person_cache.loaded_at) > _PERSON_CACHE_TTL
    ):
        _person_cache = _build_person_cache(session)
    return _person_cache


def invalidate_person_name_cache() -> None:
    """Call whenever persons are created, renamed, or deleted."""
    global _person_cache
    _person_cache = None


def _match_persons(query: str, cache: _PersonNameCache) -> tuple[list[int], str]:
    """Find person names in query using the cached compiled regex.

    Returns (ordered list of matched person IDs, query with names removed).
    O(len(query)) after the cache is warm — no per-person DB calls.
    """
    if cache.pattern is None:
        return [], query
    matches = list(cache.pattern.finditer(query))
    if not matches:
        return [], query
    seen: set[int] = set()
    person_ids: list[int] = []
    for m in matches:
        pid = cache.name_to_id.get(m.group(0).lower())
        if pid is not None and pid not in seen:
            person_ids.append(pid)
            seen.add(pid)
    cleaned = query
    for m in reversed(matches):
        cleaned = cleaned[:m.start()] + cleaned[m.end():]
    return person_ids, " ".join(cleaned.split())


# ---------------------------------------------------------------------------
# CLIP helpers
# ---------------------------------------------------------------------------

def encode_uploaded_image(image_bytes: bytes) -> np.ndarray:
    """
    Takes raw image bytes, preprocesses them for CLIP, and returns a
    normalized vector embedding.
    """
    try:
        image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    except Exception as e:
        logger.error(f"Failed to open uploaded image: {e}")
        raise HTTPException(
            status_code=400, detail="Invalid or corrupt image file."
        )

    import torch
    clip_model, preprocess, _ = get_clip_bundle()
    image_transformed = preprocess(image).unsqueeze(0)
    try:
        device = next(clip_model.parameters()).device
    except StopIteration:
        device = torch.device("cpu")
    if hasattr(image_transformed, "to"):
        image_transformed = image_transformed.to(device)
    with torch.no_grad():
        image_feat = clip_model.encode_image(image_transformed)
        image_feat /= image_feat.norm(dim=-1, keepdim=True)

    return image_feat.squeeze(0).cpu().numpy().tolist()


_TEXT_EMBEDDING_CACHE_SIZE = 256
_text_embedding_cache: OrderedDict[tuple[str, str], tuple[float, ...]] = OrderedDict()
_text_embedding_bundle_ref: weakref.ReferenceType | None = None
_text_embedding_lock = threading.RLock()


def _clear_released_text_bundle(reference: weakref.ReferenceType) -> None:
    global _text_embedding_bundle_ref
    with _text_embedding_lock:
        if _text_embedding_bundle_ref is reference:
            _text_embedding_cache.clear()
            _text_embedding_bundle_ref = None


def encode_text_query(query: str) -> list[float]:
    """Reuse query vectors for the current bundle without retaining its model."""
    global _text_embedding_bundle_ref
    clip_model, _, tokenizer = get_clip_bundle()
    retain_clip_for_search(clip_model)
    # Keep case and internal whitespace: multilingual tokenizers can use both.
    normalized = (settings.ai.clip_model.model_name, query.strip())
    with _text_embedding_lock:
        if _text_embedding_bundle_ref is None or _text_embedding_bundle_ref() is not clip_model:
            _text_embedding_cache.clear()
            _text_embedding_bundle_ref = weakref.ref(clip_model, _clear_released_text_bundle)
        cached = _text_embedding_cache.get(normalized)
        if cached is None:
            cached = tuple(_encode_text_query_uncached(normalized[1], clip_model, tokenizer))
            _text_embedding_cache[normalized] = cached
            if len(_text_embedding_cache) > _TEXT_EMBEDDING_CACHE_SIZE:
                _text_embedding_cache.popitem(last=False)
        else:
            _text_embedding_cache.move_to_end(normalized)
        # Callers get their own mutable result, never the cached tuple.
        return list(cached)


def _encode_text_query_uncached(query: str, clip_model, tokenizer) -> list[float]:
    import torch
    tokenized = tokenizer([query])
    try:
        device = next(clip_model.parameters()).device
    except StopIteration:
        device = torch.device("cpu")
    if hasattr(tokenized, "to"):
        tokenized = tokenized.to(device)
    with torch.no_grad():
        text_feat = clip_model.encode_text(tokenized)
    text_feat /= text_feat.norm(dim=-1, keepdim=True)
    return text_feat.squeeze(0).cpu().numpy().tolist()


# ---------------------------------------------------------------------------
# Multi-person search helpers
# ---------------------------------------------------------------------------

def _person_media_filter_sql(person_ids: list[int]) -> str:
    """SQL fragment yielding media_ids that contain ALL specified persons.

    Single person  → UNION of face + personmedialink rows (broad match).
    Multiple persons → INTERSECT of face-table rows only, so results are
    limited to media where every named person's face was actually detected
    together in the same image.
    """
    if len(person_ids) == 1:
        pid = person_ids[0]
        return (
            f"SELECT media_id FROM face WHERE person_id = {pid}"
            f" UNION SELECT media_id FROM personmedialink WHERE person_id = {pid}"
        )
    parts = [
        f"SELECT media_id FROM face WHERE person_id = {pid}"
        for pid in person_ids
    ]
    return "\n            INTERSECT\n            ".join(parts)


def _has_search_vectors(session: Session, table: str, key: str, visible: str) -> bool:
    """Avoid loading CLIP when this result type has no searchable embeddings."""
    return session.exec(text(
        f"SELECT 1 FROM {table} WHERE {key} IN ({visible}) LIMIT 1"
    )).first() is not None


def _knn_page(session, table, key, columns, visible, vector, max_dist,
              cursor_distance, cursor_id, count):
    """Page KNN results by exact (distance, id), including arbitrarily large ties.

    vec0 orders distance ties arbitrarily and limits them before Python sees
    them. Recover the cursor and page-boundary tie groups with bounded exact
    queries; fetching more KNN neighbors alone cannot make those ties stable.
    The identifiers here are internal constants, never request parameters.
    """
    def ties(distance, after_id, limit):
        return list(session.exec(text(f"""
            SELECT {columns}, vec_distance_L2(embedding, :vec) AS distance
            FROM {table}
            WHERE {key} IN ({visible}) AND {key} > :after_id
              AND vec_distance_L2(embedding, :vec) = :distance
            ORDER BY {key} LIMIT :limit
        """).bindparams(vec=vector, after_id=after_id, distance=distance, limit=limit)).all())

    rows = ties(cursor_distance, cursor_id, count) if cursor_id else []
    if len(rows) == count:
        return rows
    candidates = list(session.exec(text(f"""
        SELECT {columns}, distance FROM {table}
        WHERE embedding MATCH :vec AND k = :k
          AND distance < :max_dist AND distance > :min_dist
          AND {key} IN ({visible})
        ORDER BY distance
    """).bindparams(vec=vector, k=max(_MAX_KNN_RESULTS, count),
                     max_dist=max_dist, min_dist=cursor_distance)).all())
    candidates.sort(key=lambda row: (row[-1], row[0]))
    needed = count - len(rows)
    if candidates:
        boundary = candidates[min(needed, len(candidates)) - 1][-1]
        before = [row for row in candidates if row[-1] < boundary]
        rows.extend(before)
        rows.extend(ties(boundary, 0, count - len(rows)))
    return rows


# ---------------------------------------------------------------------------
# Filename exact-match helper
# ---------------------------------------------------------------------------

def _find_by_filename(query: str, session: Session) -> list[Media]:
    """Return media whose filename or stem (filename without last extension) exactly matches query.

    Matching is case-insensitive. Searching "DSC_0001" will return
    "DSC_0001.jpg", "DSC_0001.heic", etc. Searching "photo.jpg" returns
    only that exact filename.
    """
    if not query:
        return []
    q_lower = query.lower()
    escaped = q_lower.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return list(
        session.exec(
            exclude_missing(select(Media)).where(
                or_(
                    func.lower(Media.filename) == q_lower,
                    func.lower(Media.filename).like(escaped + ".%", escape="\\"),
                )
            )
        ).all()
    )


# ---------------------------------------------------------------------------
# Search endpoints
# ---------------------------------------------------------------------------

@router.post(
    "/by-image",
    summary="Search for similar media by uploading an image",
    response_model=list[MediaPreview],
)
def search_by_image(
    file: UploadFile = File(...),
    limit: int = 20,
    session: Session = Depends(get_session),
):
    image_bytes = file.file.read()
    query_vector = encode_uploaded_image(image_bytes)

    max_dist = 2.0 - settings.ai.min_similarity_dist
    vec_blob = vector_to_blob(query_vector)
    if vec_blob is None:
        logger.warning("Failed to encode query vector for uploaded image search")
        return []

    sql = text(
        f"""
        SELECT media_id, distance
        FROM media_embeddings
        WHERE embedding MATCH :vec
            AND k = :k
            AND distance < :max_dist
            AND media_id IN ({_VISIBLE_MEDIA_SQL})
        ORDER BY distance
        """
    ).bindparams(vec=vec_blob, max_dist=max_dist, k=limit)

    rows = session.exec(sql).all()
    media_ids = [row[0] for row in rows]
    if not media_ids:
        return []

    media_objs = session.exec(exclude_missing(select(Media)).where(Media.id.in_(media_ids))).all()
    id_to_obj = {m.id: m for m in media_objs}
    return [MediaPreview.model_validate(id_to_obj[mid]) for mid in media_ids if mid in id_to_obj]


@router.get(
    "/combined",
    summary="Search media by text query, surfaces matched persons alongside results",
    response_model=CombinedMediaSearchResult,
)
def search_combined(
    query: str = Query("", description="Free-text or embedding query"),
    limit: int = 20,
    cursor: str | None = Query(None, description="Opaque cursor from previous page"),
    order_by: str = Query(
        "relevance",
        description="Sort order for results: 'relevance' (similarity, default) or 'date' (newest first)",
    ),
    session: Session = Depends(get_session),
):
    if order_by not in ("relevance", "date"):
        raise HTTPException(status_code=400, detail="order_by must be 'relevance' or 'date'")

    if not query:
        return CombinedMediaSearchResult()

    # Exact filename match takes priority over vector search
    filename_hits = _find_by_filename(query.strip(), session)
    if filename_hits:
        return CombinedMediaSearchResult(
            media=[MediaPreview.model_validate(m) for m in filename_hits],
        )

    cache = _get_person_cache(session)
    person_ids, search_text = _match_persons(query, cache)

    # Fetch full person objects — only on the first page to avoid redundant data
    persons: list[PersonRead] = []
    if person_ids and cursor is None:
        person_objs = session.exec(
            select(Person).where(Person.id.in_(person_ids))
        ).all()
        id_to_p = {p.id: p for p in person_objs}
        persons = [
            PersonRead.model_validate(id_to_p[pid])
            for pid in person_ids
            if pid in id_to_p
        ]

    max_dist = 2.0 - settings.ai.min_search_dist

    # No semantic text left — return the persons' most recent media without vector search
    if not search_text and person_ids:
        media_filter = _person_media_filter_sql(person_ids)
        q = exclude_missing(select(Media)).where(Media.id.in_(text(media_filter)))
        if cursor:
            try:
                iso_str, id_str = cursor.rsplit("_", 1)
                cursor_dt, cursor_id = datetime.fromisoformat(iso_str), int(id_str)
            except (ValueError, TypeError):
                raise HTTPException(status_code=400, detail="Invalid cursor format")
            q = q.where(or_(Media.inserted_at < cursor_dt,
                           and_(Media.inserted_at == cursor_dt, Media.id < cursor_id)))
        rows = list(session.exec(q.order_by(desc(Media.inserted_at), desc(Media.id)).limit(limit + 1)).all())
        page = rows[:limit]
        last = page[-1] if page else None
        return CombinedMediaSearchResult(
            persons=persons,
            media=[MediaPreview.model_validate(m) for m in page],
            next_cursor=f"{last.inserted_at.isoformat()}_{last.id}" if last and len(rows) > limit else None,
        )

    if not search_text:
        return CombinedMediaSearchResult(persons=persons)

    if not _has_search_vectors(session, "media_embeddings", "media_id", _VISIBLE_MEDIA_SQL):
        return CombinedMediaSearchResult(persons=persons)

    vec = encode_text_query(search_text)
    vec_blob = vector_to_blob(vec)
    if vec_blob is None:
        logger.warning("Failed to encode query vector for combined search")
        return CombinedMediaSearchResult(persons=persons)

    # ------------------------------------------------------------------
    # Relevance order: paginate by (distance, media_id) composite cursor.
    # Using a composite key prevents duplicates when multiple items share
    # the same cosine distance value.
    # ------------------------------------------------------------------
    if order_by == "relevance":
        min_dist = -1.0
        cursor_media_id = 0
        if cursor:
            try:
                dist_str, id_str = cursor.rsplit("_", 1)
                min_dist = float(dist_str)
                cursor_media_id = int(id_str)
            except (ValueError, TypeError):
                raise HTTPException(status_code=400, detail="Invalid cursor format")

        if person_ids:
            # Brute-force exact cosine over the persons' media.
            # Multi-person → INTERSECT on the face table so only media where
            # every named person's face was detected together is searched.
            media_filter = _person_media_filter_sql(person_ids)
            sql = text(f"""
                SELECT media_id, distance FROM (
                    SELECT me.media_id,
                           vec_distance_cosine(me.embedding, :vec_blob) AS distance
                    FROM media_embeddings me
                    WHERE me.media_id IN (
                        {_VISIBLE_MEDIA_SQL}
                        AND media.id IN ({media_filter})
                    )
                ) ranked
                WHERE distance < :max_dist
                  AND (distance > :min_dist OR (distance = :min_dist AND media_id > :cursor_id))
                ORDER BY distance, media_id
                LIMIT :k
            """).bindparams(
                vec_blob=vec_blob, max_dist=max_dist,
                min_dist=min_dist, cursor_id=cursor_media_id, k=limit + 1,
            )
            rows = list(session.exec(sql).all())
        else:
            rows = _knn_page(
                session, "media_embeddings", "media_id", "media_id", _VISIBLE_MEDIA_SQL,
                vec_blob, max_dist, min_dist, cursor_media_id, limit + 1,
            )

        page_rows = rows[:limit]
        has_more = len(rows) > limit

        media_ids = [r[0] for r in page_rows]
        media_objs = session.exec(exclude_missing(select(Media)).where(Media.id.in_(media_ids))).all()
        id_map = {m.id: m for m in media_objs}
        ordered = [id_map[mid] for mid in media_ids if mid in id_map]

        next_cursor = (
            f"{page_rows[-1][1]}_{page_rows[-1][0]}" if page_rows and has_more else None
        )
        return CombinedMediaSearchResult(
            persons=persons,
            media=[MediaPreview.model_validate(m) for m in ordered],
            next_cursor=next_cursor,
        )

    # ------------------------------------------------------------------
    # Date order: over-fetch all vector candidates (up to _MAX_KNN_RESULTS),
    # then sort and paginate by (inserted_at DESC, id DESC).
    # Cursor format: "<iso_datetime>_<media_id>"
    # ------------------------------------------------------------------
    cursor_dt: datetime | None = None
    cursor_id: int | None = None
    if cursor:
        try:
            iso_str, id_str = cursor.rsplit("_", 1)
            cursor_dt = datetime.fromisoformat(iso_str)
            cursor_id = int(id_str)
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="Invalid cursor format")

    if person_ids:
        media_filter = _person_media_filter_sql(person_ids)
        vec_sql = text(f"""
            SELECT me.media_id
            FROM media_embeddings me
            WHERE me.media_id IN (
                {_VISIBLE_MEDIA_SQL}
                AND media.id IN ({media_filter})
            )
            AND vec_distance_cosine(me.embedding, :vec_blob) < :max_dist
        """).bindparams(vec_blob=vec_blob, max_dist=max_dist)
    else:
        vec_sql = text(f"""
            SELECT media_id
            FROM media_embeddings
            WHERE embedding MATCH :vec_blob
              AND k = :k
              AND distance < :max_dist
              AND media_id IN ({_VISIBLE_MEDIA_SQL})
            ORDER BY distance
        """).bindparams(vec_blob=vec_blob, max_dist=max_dist, k=_MAX_KNN_RESULTS)

    candidate_ids = [r[0] for r in session.exec(vec_sql).all()]
    if not candidate_ids:
        return CombinedMediaSearchResult(persons=persons)

    q = exclude_missing(select(Media)).where(Media.id.in_(candidate_ids))
    if cursor_dt is not None:
        q = q.where(
            or_(
                Media.inserted_at < cursor_dt,
                and_(Media.inserted_at == cursor_dt, Media.id < cursor_id),
            )
        )
    q = q.order_by(desc(Media.inserted_at), desc(Media.id)).limit(limit + 1)
    media_objs = list(session.exec(q).all())

    page_objs = media_objs[:limit]
    has_more = len(media_objs) > limit
    last = page_objs[-1] if page_objs else None
    next_cursor = f"{last.inserted_at.isoformat()}_{last.id}" if last and has_more else None

    return CombinedMediaSearchResult(
        persons=persons,
        media=[MediaPreview.model_validate(m) for m in page_objs],
        next_cursor=next_cursor,
    )


@router.get(
    "/scenes",
    summary="Search video scenes by text query",
    response_model=CursorPage[SceneSearchResult],
)
def search_scenes(
    query: str = Query("", description="Free-text query for scene search"),
    limit: int = 20,
    cursor: str | None = Query(
        None, description="Opaque cursor returned from previous request"
    ),
    session: Session = Depends(get_session),
):
    if not query:
        return CursorPage(items=[], next_cursor=None)

    cache = _get_person_cache(session)
    person_ids, search_text = _match_persons(query, cache)

    if not search_text:
        return CursorPage(items=[], next_cursor=None)

    max_dist = 2.0 - settings.ai.min_search_dist

    # Cursor format: "<distance>_<scene_id>" — composite key prevents duplicates
    # when multiple scenes share the same cosine distance value.
    min_dist = -1.0
    cursor_scene_id = 0
    if cursor:
        try:
            dist_str, id_str = cursor.rsplit("_", 1)
            min_dist = float(dist_str)
            cursor_scene_id = int(id_str)
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="Invalid cursor format")

    if not _has_search_vectors(session, "scene_embeddings", "scene_id", _VISIBLE_SCENES_SQL):
        return CursorPage(items=[], next_cursor=None)

    vec = encode_text_query(search_text)
    vec_blob = vector_to_blob(vec)
    if vec_blob is None:
        logger.warning("Failed to encode query vector for scene search")
        return CursorPage(items=[], next_cursor=None)

    if person_ids:
        # Brute-force exact cosine over the persons' scenes.
        # Multi-person → INTERSECT on the face table so only scenes from media
        # where every named person's face was detected together are returned.
        media_filter = _person_media_filter_sql(person_ids)
        sql = text(f"""
            SELECT scene_id, media_id, distance FROM (
                SELECT se.scene_id, se.media_id,
                       vec_distance_cosine(se.embedding, :vec_blob) AS distance
                FROM scene_embeddings se
                WHERE se.scene_id IN ({_VISIBLE_SCENES_SQL})
                  AND se.media_id IN ({media_filter})
            ) ranked
            WHERE distance < :max_dist
              AND (distance > :min_dist OR (distance = :min_dist AND scene_id > :cursor_id))
            ORDER BY distance, scene_id LIMIT :k
        """).bindparams(vec_blob=vec_blob, max_dist=max_dist, min_dist=min_dist,
                        cursor_id=cursor_scene_id, k=limit + 1)
        rows = list(session.exec(sql).all())
    else:
        rows = _knn_page(
            session, "scene_embeddings", "scene_id", "scene_id, media_id", _VISIBLE_SCENES_SQL,
            vec_blob, max_dist, min_dist, cursor_scene_id, limit + 1,
        )

    if not rows:
        return CursorPage(items=[], next_cursor=None)

    page_rows = rows[:limit]
    has_more = len(rows) > limit

    scene_ids = [r[0] for r in page_rows]
    distance_map = {r[0]: float(r[2]) for r in page_rows}

    scene_data = session.exec(
        exclude_missing(select(Scene, Media))
        .join(Media, Scene.media_id == Media.id)
        .where(Scene.id.in_(scene_ids))
    ).all()
    if not scene_data:
        return CursorPage(items=[], next_cursor=None)

    scene_map = {scene.id: (scene, media) for scene, media in scene_data}
    results: list[SceneSearchResult] = []
    for scene_id in scene_ids:
        if scene_id not in scene_map:
            continue
        scene, media = scene_map[scene_id]
        results.append(
            SceneSearchResult(
                scene_id=scene.id,
                media_id=media.id,
                media_filename=media.filename,
                media_thumbnail_path=media.thumbnail_path,
                scene_thumbnail_path=scene.thumbnail_path,
                start_time=float(scene.start_time),
                end_time=float(scene.end_time) if scene.end_time is not None else None,
                distance=distance_map.get(scene.id, 0.0),
            )
        )

    next_cursor = (
        f"{results[-1].distance}_{results[-1].scene_id}" if results and has_more else None
    )
    return CursorPage(items=results, next_cursor=next_cursor)


@router.get(
    "/person",
    summary="Search people by name",
    response_model=CursorPage[PersonReadSimple],
)
def search_people(
    limit: int = Query(20, ge=1, le=500),
    cursor: str | None = Query(None, pattern=r"^[0-9]{1,19}_[1-9][0-9]{0,18}$", description="Encoded as `<count>_<id>`"),
    query: str = Query("", description="Person name query"),
    session: Session = Depends(get_session),
):
    if not query:
        return CursorPage(items=[], next_cursor=None)

    q = select(Person).where(
        Person.name.ilike(f"%{query}%"),
        Person.hidden_at.is_(None),
    )

    if cursor:
        try:
            cursor_count, cursor_id = map(int, cursor.split("_"))
            if not (0 <= cursor_count <= 9223372036854775807 and 1 <= cursor_id <= 9223372036854775807):
                raise ValueError("Cursor is out of range")
            q = q.where(
                tuple_(Person.appearance_count, Person.id)
                < (cursor_count, cursor_id)
            )
        except (ValueError, TypeError):
            raise HTTPException(status_code=422, detail="Invalid cursor format")

    q = q.order_by(desc(Person.appearance_count), desc(Person.id)).limit(limit)
    people = session.exec(q).all()

    next_cursor = None
    if len(people) == limit:
        last = people[-1]
        next_cursor = f"{last.appearance_count}_{last.id}"
    return CursorPage(
        items=[PersonReadSimple.model_validate(p) for p in people],
        next_cursor=next_cursor,
    )


@router.get(
    "/tags",
    summary="Search tags by name",
    response_model=CursorPage[TagRead],
)
def search_tags(
    limit: int = Query(20, ge=1, le=500),
    cursor: str | None = Query(
        None, pattern=r"^[1-9][0-9]{0,18}$", description="The ID of the last tag from the previous page"
    ),
    query: str = Query("", description="Tag name query"),
    session: Session = Depends(get_session),
):
    if not query:
        return CursorPage(items=[], next_cursor=None)

    q = select(Tag).where(Tag.name.ilike(f"%{query}%"))

    if cursor:
        try:
            cursor_id = int(cursor)
            if not 1 <= cursor_id <= 9223372036854775807:
                raise ValueError("Tag id is out of range")
            q = q.where(Tag.id < cursor_id)
        except (ValueError, TypeError):
            raise HTTPException(status_code=422, detail="Invalid cursor format")

    q = q.order_by(desc(Tag.id)).limit(limit)
    tags = session.exec(q).all()

    next_cursor = None
    if len(tags) == limit:
        next_cursor = str(tags[-1].id)

    return CursorPage(
        items=[
            TagRead(
                id=tag.id,
                name=tag.name,
                media=session.exec(
                    exclude_missing(select(Media))
                    .join(MediaTagLink, MediaTagLink.media_id == Media.id)
                    .where(MediaTagLink.tag_id == tag.id)
                ).all(),
                persons=tag.persons,
            )
            for tag in tags
        ],
        next_cursor=next_cursor,
    )
