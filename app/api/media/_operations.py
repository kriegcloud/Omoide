"""Admission and replay for synchronous media mutation receipts."""
from uuid import UUID
from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from sqlmodel import Session
from app.models import MutationOperation
from app.schemas.mutation_operations import SkippedMutation
from app.services.mutation_operations import begin_operation, lookup_operation


def headers(operation_id):
    return {'X-Mutation-Operation-ID': str(operation_id),
            'X-Mutation-Operation-URL': f'/api/media/operations/{operation_id}'}


def execute(session, operation_id, snapshot, ids, callback):
    if operation_id is None:
        return callback(None)
    try:
        operation_id = str(UUID(str(operation_id)))
    except ValueError:
        raise HTTPException(400, 'operation_id must be a UUID.')
    row = session.get(MutationOperation, operation_id)
    if row is not None:
        saved = {k: v for k, v in row.snapshot.items() if k != 'selected_ids'}
        if saved != snapshot:
            raise HTTPException(409, 'operation_id belongs to a different request.', headers=headers(operation_id))
        if row.status_code is None:
            raise HTTPException(409, 'Operation outcome is not terminal; read its receipt.', headers=headers(operation_id))
        if row.status_code >= 400:
            raise HTTPException(row.status_code, row.response['detail'], headers=headers(operation_id))
        return {**row.response, 'receipt': row.receipt}
    operation = begin_operation(session, operation_id, snapshot, list(dict.fromkeys(ids)))
    session.info['mutation_operation'] = operation
    try:
        result = jsonable_encoder(callback(operation))
    except Exception as exc:
        session.rollback()
        status = exc.status_code if isinstance(exc, HTTPException) else 500
        detail = exc.detail if isinstance(exc, HTTPException) else 'Mutation failed; read its receipt before taking further action.'
        operation.finish({'detail': detail}, status)
        raise HTTPException(status, detail, headers=headers(operation_id)) from exc
    finally:
        session.info.pop('mutation_operation', None)
    return {**result, 'receipt': operation.finish(result, 200)}


def skip(operation, media_id, reason):
    if operation is None:
        return
    receipt = lookup_operation(operation.session, operation.id)
    key = f'media:{media_id}'
    receipt.unknown = [item for item in receipt.unknown if item != key]
    receipt.skipped.append(SkippedMutation(key=key, reason=reason))
    operation._save(receipt)


def checkpoint(operation, media_id):
    """Add the receipt to the same transaction as the catalog mutation."""
    if operation is not None:
        operation.applied(media_id, commit=False)
