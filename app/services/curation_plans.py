"""Guarded bounded synchronous operations with durable admission and receipts."""
from contextlib import contextmanager

from sqlalchemy import text
from sqlmodel import Session, select

from app.curation_models import (CurationArtifact, CurationCaption, CurationDataset,
    CurationEvent, CurationGrant, CurationOperation, CurationReview, CurationSource)
from app.services.curation_artifacts import (TRANSFORM, artifact_bytes, canonical,
    publish_artifact, source_bytes)
from app.services.curation_media import materialize_media
from app.services.curation_policy import (PRODUCTION_POLICY_VERSION, authorize, dataset_for,
    digest, fail, mode, require_revision)


@contextmanager
def transaction(session: Session):
    # Services own their session transaction; commit before yielding responses.
    # SQLite IMMEDIATE serializes admission/revisions, including cross-process.
    session.rollback()
    session.exec(text('BEGIN IMMEDIATE'))
    try:
        yield
        session.commit()
    except BaseException:
        session.rollback()
        raise


def source_for(session, dataset, source_id):
    source = session.get(CurationSource, source_id)
    if not source or source.dataset_id != dataset.id:
        fail('not_found', 404)
    return source


def artifact_for(session, dataset, artifact_id):
    artifact = session.get(CurationArtifact, artifact_id)
    if not artifact or artifact.dataset_id != dataset.id:
        fail('not_found', 404)
    return artifact


def generative_ancestry(session, dataset, artifact):
    seen = set()
    generative = False
    current = artifact
    while current:
        if current.id in seen or current.dataset_id != dataset.id:
            fail('invalid_lineage')
        seen.add(current.id)
        source = source_for(session, dataset, current.source_id)
        if not source.lineage_known:
            fail('unknown_lineage')
        generative = generative or current.generative or source.generative
        if current.parent_id:
            current = artifact_for(session, dataset, current.parent_id)
        else:
            current = None
    return generative


def latest_caption(session, artifact):
    return session.exec(select(CurationCaption).where(
        CurationCaption.artifact_id == artifact.id).order_by(CurationCaption.revision.desc())).first()


def latest_review(session, artifact, caption):
    if not caption:
        return None
    return session.exec(select(CurationReview).where(
        CurationReview.artifact_id == artifact.id, CurationReview.caption_id == caption.id
    ).order_by(CurationReview.revision.desc())).first()


def item_state(session, dataset, artifact):
    caption = latest_caption(session, artifact)
    review = latest_review(session, artifact, caption)
    blockers = []
    try:
        generative = generative_ancestry(session, dataset, artifact)
    except Exception as exc:
        from fastapi import HTTPException
        if not isinstance(exc, HTTPException):
            raise
        blockers.append(exc.detail['code'])
        generative = True
    if generative:
        blockers.append('generative_disabled')
    if not caption:
        blockers.append('caption_required')
    if not review or review.decision != 'accept':
        blockers.append('review_required' if not review else f'review_{review.decision}')
    elif (review.asset_sha256 != artifact.sha256 or review.caption_sha256 != caption.sha256
          or review.policy_version != dataset.policy_version):
        blockers.append('review_stale')
    else:
        from app.services.curation_auth import review_authority_error
        authority_error = review_authority_error(session, dataset, review)
        if authority_error:
            blockers.append(authority_error)
    return {'artifact_id': artifact.id, 'source_id': artifact.source_id, 'sha256': artifact.sha256,
            'width': artifact.width, 'height': artifact.height, 'generative_ancestry': generative,
            'transform_summary': 'EXIF orientation once; RGB PNG; no resizing; metadata stripped.',
            'uncertainties': artifact.provenance.get('uncertainties', []),
            'caption': {'id': caption.id, 'text': caption.text, 'sha256': caption.sha256} if caption else None,
            'review': {'id': review.id, 'decision': review.decision, 'actor_kind': review.actor_kind,
                       'human_presence_verified': bool(review.presence_evidence),
                       'presence_evidence': review.presence_evidence} if review else None,
            'eligible': not blockers, 'blockers': blockers}


def receipt(op):
    return {'id': op.id, 'dataset_id': op.dataset_id, 'status': op.status,
            'snapshot_revision': op.snapshot_revision, 'manifest_sha256': op.manifest_sha256,
            'item_count': op.item_count, 'error_code': op.error_code, 'attempts': op.attempts}


def detail(session, token, dataset_id):
    dataset, grant = dataset_for(session, token, dataset_id)
    sources = session.exec(select(CurationSource).where(CurationSource.dataset_id == dataset_id).order_by(CurationSource.id)).all()
    artifacts = session.exec(select(CurationArtifact).where(CurationArtifact.dataset_id == dataset_id).order_by(CurationArtifact.id)).all()
    operations = session.exec(select(CurationOperation).where(CurationOperation.dataset_id == dataset_id).order_by(CurationOperation.created_at)).all()
    materialized = {a.source_id for a in artifacts}
    source_rows = []
    for source in sources:
        failure = next((op.error_code for op in reversed(operations) if op.kind == 'materialize' and op.snapshot.get('source_id') == source.id and op.error_code), None)
        source_rows.append({'id': source.id, 'label': source.label, 'sha256': source.sha256,
                            'status': 'materialized' if source.id in materialized else (failure or 'registered')})
    items = [item_state(session, dataset, artifact) for artifact in artifacts]
    pending = sum(source.split == 'train' and source.id not in materialized for source in sources)
    blockers = ['unmaterialized_sources'] if pending else []
    if not any(source.split == 'train' for source in sources):
        blockers.append('empty_dataset')
    if any(not item['eligible'] for item in items):
        blockers.append('item_review_incomplete')
    from app.services.curation_auth import credential_for
    credential = credential_for(session, grant) if mode() == 'production' else None
    return {'id': dataset.id, 'name': dataset.name, 'revision': dataset.revision,
            'policy_version': dataset.policy_version,
            'actor': {'id': grant.actor_id, 'kind': grant.actor_kind, 'human_presence_verified': False,
                      'operations': grant.operations, 'enrolled': bool(credential and not credential.revoked)},
            'sources': source_rows, 'items': items,
            'exports': [receipt(op) for op in operations if op.kind == 'export'],
            'remaining_count': pending + sum(not item['eligible'] for item in items),
            'blockers': blockers}


def admitted(session, grant, kind, key, payload):
    request_hash = digest(canonical(payload))
    previous = session.exec(select(CurationOperation).where(
        CurationOperation.grant_id == grant.id, CurationOperation.kind == kind,
        CurationOperation.idempotency_key == key)).first()
    if previous and previous.request_sha256 != request_hash:
        fail('idempotency_conflict')
    return previous, request_hash


def materialize(session: Session, token: str, dataset_id: str, request):
    with transaction(session):
        dataset, grant = dataset_for(session, token, dataset_id, 'materialize')
        op, request_hash = admitted(session, grant, 'materialize', request.idempotency_key, request.model_dump())
        if op is None:
            require_revision(dataset, request.expected_revision)
            source = source_for(session, dataset, request.source_id)
            if not source.lineage_known:
                fail('unknown_lineage')
            if source.generative:
                fail('generative_disabled', 403)
            if source.split != 'train':
                fail('holdout_materialization_disabled', 403)
            if request.frame is not None and request.frame.kind == 'repair_evidence':
                fail('generative_disabled', 403)
            frame = request.frame.model_dump() if request.frame is not None else None
            op = CurationOperation(dataset_id=dataset_id, grant_id=grant.id, kind='materialize',
                idempotency_key=request.idempotency_key, request_sha256=request_hash,
                snapshot_revision=dataset.revision, snapshot={'source_id': source.id, 'sha256': source.sha256,
                                                             'frame': frame,
                                                             'policy_version': dataset.policy_version})
            session.add(op)
            session.flush()
            session.add(CurationEvent(operation_id=op.id, event='admitted', attempt=0))
        operation_id = op.id
    try:
        with transaction(session):
            dataset, grant = dataset_for(session, token, dataset_id, 'materialize')
            op = session.get(CurationOperation, operation_id, populate_existing=True)
            if op.status != 'succeeded':
                require_revision(dataset, op.snapshot_revision)
                source = source_for(session, dataset, op.snapshot['source_id'])
                if source.generative or not source.lineage_known:
                    fail('invalid_lineage')
                if source.sha256 != op.snapshot['sha256']:
                    fail('source_changed')
                op.attempts += 1
                data = source_bytes(dataset, source)
                frame = op.snapshot.get('frame')
                encoded, pixel_hash, width, height, provenance = materialize_media(dataset, source, data, frame)
                # The selected frame and any external decoder identity are part of
                # the cache key: two frames of one source are two artifacts, and a
                # decoder upgrade cannot silently reuse another decoder's entry.
                cache_key = digest(canonical({'source_sha256': source.sha256, 'transform': TRANSFORM,
                    'frame': frame, 'decoder': provenance.get('decoder_identity')}))
                artifact = session.exec(select(CurationArtifact).where(CurationArtifact.dataset_id == dataset_id,
                    CurationArtifact.source_id == source.id, CurationArtifact.cache_key == cache_key)).first()
                if artifact and artifact.sha256 != digest(encoded):
                    fail('cache_key_collision')
                publish_artifact(dataset, encoded)
                if not artifact:
                    artifact = CurationArtifact(dataset_id=dataset_id, source_id=source.id,
                        sha256=digest(encoded), pixel_sha256=pixel_hash, cache_key=cache_key,
                        size=len(encoded), width=width, height=height, provenance=provenance)
                    session.add(artifact)
                    dataset.revision += 1
                    session.add(dataset)
                op.status = 'succeeded'
                op.error_code = None
                op.item_count = 1
                session.add(op)
                session.add(CurationEvent(operation_id=op.id, event='succeeded', attempt=op.attempts))
    except Exception as exc:
        record_failure(session, operation_id, exc)
        raise
    return detail(session, token, dataset_id)


def record_failure(session, operation_id, exc):
    from fastapi import HTTPException
    code = exc.detail.get('code', 'operation_failed') if isinstance(exc, HTTPException) and isinstance(exc.detail, dict) else 'operation_failed'
    with transaction(session):
        op = session.get(CurationOperation, operation_id, populate_existing=True)
        if op and op.status != 'succeeded':
            op.status = 'blocked'
            op.error_code = code
            session.add(op)
            session.add(CurationEvent(operation_id=op.id, event=code, attempt=op.attempts))


def add_caption(session, token, dataset_id, request):
    with transaction(session):
        dataset, grant = dataset_for(session, token, dataset_id, 'caption')
        require_revision(dataset, request.expected_revision)
        artifact = artifact_for(session, dataset, request.artifact_id)
        if not request.text.strip() or '\x00' in request.text:
            fail('invalid_caption', 422)
        # Exact UTF-8 bytes, no implicit trimming or newline insertion.
        caption = CurationCaption(dataset_id=dataset_id, artifact_id=artifact.id,
            revision=dataset.revision + 1, text=request.text, sha256=digest(request.text.encode()),
            actor_id=grant.actor_id, actor_kind=grant.actor_kind)
        session.add(caption)
        dataset.revision += 1
        session.add(dataset)
    return detail(session, token, dataset_id)


def review(session, token, dataset_id, request):
    with transaction(session):
        dataset, grant = dataset_for(session, token, dataset_id, 'review')
        require_revision(dataset, request.expected_revision)
        artifact = artifact_for(session, dataset, request.artifact_id)
        if generative_ancestry(session, dataset, artifact):
            fail('generative_disabled', 403)
        caption = latest_caption(session, artifact)
        if not caption or caption.id != request.caption_id or caption.sha256 != request.caption_sha256 or artifact.sha256 != request.asset_sha256:
            fail('review_hash_conflict')
        artifact_bytes(dataset, artifact)  # Revalidate exact bytes at acceptance.
        presence_evidence = {}
        if dataset.policy_version == PRODUCTION_POLICY_VERSION:
            from app.services.curation_auth import verify_review_presence
            presence_evidence = verify_review_presence(session, dataset, grant, request)
        elif request.presence is not None:
            fail('presence_not_allowed', 422)
        session.add(CurationReview(dataset_id=dataset_id, artifact_id=artifact.id, caption_id=caption.id,
            asset_sha256=artifact.sha256, caption_sha256=caption.sha256, decision=request.decision,
            rationale=request.rationale, revision=dataset.revision + 1, actor_id=grant.actor_id,
            actor_kind=grant.actor_kind, grant_id=grant.id, policy_version=dataset.policy_version,
            presence_evidence=presence_evidence))
        dataset.revision += 1
        session.add(dataset)
    return detail(session, token, dataset_id)
