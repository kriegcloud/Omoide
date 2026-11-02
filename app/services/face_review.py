"""Read-only face-to-face suggestions, independent of automatic thresholds.

Call from a synchronous HTTP handler: the session and all NumPy work stay in
one worker thread. Matrix workspace is bounded; graph storage scales with
the number of qualifying edges.
"""
from threading import Lock

import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from sqlmodel import Session, select, text
from sqlalchemy.orm import selectinload
from threadpoolctl import threadpool_limits

from app.models import Face, Person
from app.schemas.face import FaceRead
from app.services.face_matching import PrototypeIndex, score_faces
from app.vectors import vector_from_stored


def normalized(matrix):
    matrix = np.asarray(matrix, dtype=np.float32)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    return np.ascontiguousarray(matrix / np.maximum(norms, 1e-30))


def load_assigned_index(session: Session):
    rows = session.exec(text('''SELECT f.id, f.person_id, fe.embedding FROM face f
        JOIN face_embeddings fe ON fe.face_id=f.id
        WHERE f.person_id IS NOT NULL AND f.ignored_at IS NULL ORDER BY f.id''')).all()
    ids, persons, vectors = [], [], []
    for fid, pid, raw in rows:
        vector = vector_from_stored(raw)
        if vector is None or vector.shape != (512,) or not np.isfinite(vector).all() or not np.any(vector):
            continue
        ids.append(fid)
        persons.append(pid)
        vectors.append(vector)
    names = {pid: name or f'Person {pid}' for pid, name in session.exec(select(Person.id, Person.name)).all()}
    matrix = normalized(np.vstack(vectors)) if vectors else np.empty((0, 512), dtype=np.float32)
    return ids, PrototypeIndex(np.asarray(persons), matrix, names)


def near_matches(session, face_ids, embeddings, frontalities, rejected):
    assigned_ids, index = load_assigned_index(session)
    matches = score_faces(index, face_ids, embeddings, frontalities, rejected_persons=rejected)
    best_ids = {}
    # score_faces already chooses a distinct-person runner-up. Recover the
    # winning face deterministically, using the same normalized vectors.
    if assigned_ids and len(face_ids):
        vectors = normalized(embeddings)
        offsets = {fid: i for i, fid in enumerate(face_ids)}
        by_person = {}
        for match in matches:
            by_person.setdefault(match.person_id, []).append(match)
        for pid, members in by_person.items():
            columns = np.flatnonzero(index.person_ids == pid)
            for start in range(0, len(members), 256):
                chunk = members[start:start+256]
                scores = vectors[[offsets[m.face_id] for m in chunk]] @ index.matrix[columns].T
                winners = np.argmax(scores, axis=1)
                for match, winner in zip(chunk, winners):
                    best_ids[match.face_id] = assigned_ids[columns[winner]]
    return matches, best_ids, index.names


def build_groups(embeddings):
    """Exact >=.70 graph, then peel weak members until every mean is >=.70.

    Means exclude self-similarity. Peeling is deterministic (lowest mean, then
    original row). Statistics are over all distinct member pairs.
    """
    if len(embeddings) < 4:
        return []
    matrix = normalized(embeddings)
    n = len(matrix)
    left, right = [], []
    # BLAS oversubscription can make this dramatically slower in worker pools.
    with threadpool_limits(limits=2, user_api='blas'):
        for start in range(0, n, 512):
            scores = matrix[start:start+512] @ matrix.T
            a, b = np.nonzero(scores >= .70)
            a = a + start
            keep = b > a
            if np.any(keep):
                left.append(a[keep].astype(np.int32))
                right.append(b[keep].astype(np.int32))
        if not left:
            return []
        left = np.concatenate(left)
        right = np.concatenate(right)
        graph = coo_matrix(
            (np.ones(len(left), dtype=np.uint8), (left, right)), shape=(n, n)
        ).tocsr()
        _, labels = connected_components(graph, directed=False)
        groups = []
        order = np.argsort(labels, kind='stable')
        boundaries = np.flatnonzero(np.diff(labels[order])) + 1
        for indices in np.split(order, boundaries):
            if len(indices) < 4:
                continue
            while len(indices) >= 4:
                members = matrix[indices]
                means = (members @ members.sum(axis=0) - 1) / (len(indices)-1)
                weakest = int(np.argmin(means))
                if means[weakest] >= .70 - 1e-6:
                    break
                indices = np.delete(indices, weakest)
            if len(indices) < 4:
                continue
            members = matrix[indices]
            minimum = 1.
            for start in range(0, len(indices), 256):
                scores = members[start:start+256] @ members.T
                scores[np.arange(len(scores)), np.arange(start,start+len(scores))] = 1.
                minimum = min(minimum, float(scores.min()))
            groups.append(dict(indices=indices.tolist(), mean_cos=float(np.clip(means.mean(),-1,1)), min_cos=float(np.clip(minimum,-1,1))))
    groups.sort(key=lambda g: (-len(g['indices']), -g['mean_cos'], g['indices']))
    return groups


_cache_lock = Lock()
_cache = {}  # One snapshot per database, with a small global bound.


def get_groups(session: Session):
    from app.services.face_matching import load_unassigned_face_embeddings
    connection = session.connection().connection.driver_connection
    # Counter is durable across API workers; local counters also cover changes
    # to assigned faces, names and vectors used for the candidate information.
    has_state = session.exec(text("SELECT 1 FROM sqlite_master WHERE name='face_review_state'")).first()
    stats = session.exec(text(
        'SELECT max(id), count(*) FROM face WHERE person_id IS NULL AND ignored_at IS NULL'
    )).one()
    if has_state:
        # The migration bumps this durable counter on every face insert,
        # update or delete, including ignore/unignore and ownership changes.
        # Unlike PRAGMA data_version, it is comparable across pooled connections.
        generation = session.exec(text(
            'SELECT ignored_generation FROM face_review_state WHERE id=1'
        )).one()[0]
        names = tuple(session.exec(select(Person.id, Person.name).order_by(Person.id)).all())
        key = (session.get_bind(), tuple(stats), generation, names)
    else:
        # SQLModel.create_all fixtures do not install migration-owned triggers.
        generation = tuple(session.exec(
            select(Face.id, Face.ignored_at).where(Face.ignored_at.is_not(None))
        ).all())
        key = (session.get_bind(), connection, tuple(stats), generation,
               connection.total_changes, session.exec(text('PRAGMA data_version')).one()[0])
    # Serialize cache construction so concurrent page loads do not duplicate the
    # quadratic calculation. The owning session never leaves its worker.
    with _cache_lock:
        if key in _cache:
            return _cache[key]
        ids, vectors, _ = load_unassigned_face_embeddings(session)
        groups = build_groups(vectors)
        faces = {face.id: face for face in session.exec(select(Face).where(Face.person_id.is_(None), Face.ignored_at.is_(None)).options(selectinload(Face.media))).all()}
        _, assigned = load_assigned_index(session)
        result = []
        for group in groups:
            indices = group.pop('indices')
            group_ids = [ids[i] for i in indices]
            members = [faces[fid] for fid in group_ids]
            representative = max(members, key=lambda f: ((f.det_score or 0)*(f.frontality or 0), -f.id))
            candidate = None
            if len(assigned.matrix):
                mean_vector = normalized(vectors[indices]).mean(axis=0)
                scores = assigned.matrix @ mean_vector
                persons, inverse, counts = np.unique(
                    assigned.person_ids, return_inverse=True, return_counts=True
                )
                means = np.bincount(inverse, weights=scores) / counts
                ranked = np.lexsort((persons, -means))
                pid, score = int(persons[ranked[0]]), float(means[ranked[0]])
                runner = float(means[ranked[1]]) if len(ranked) > 1 else -1.
                if score >= .48:
                    candidate = dict(person_id=pid, person_name=assigned.names[pid], mean_cos=score, runner_up_person_cos=runner, margin=score-runner)
            result.append(dict(**group, face_ids=group_ids, size=len(group_ids), media_count=len({f.media_id for f in members}), representative_face=FaceRead.model_validate(representative).model_dump(mode='json'), member_faces=[FaceRead.model_validate(face).model_dump(mode='json') for face in members], candidate=candidate))
        if len(_cache)>=8:
            _cache.clear()
        _cache[key] = result
        return result
