"""Durable synchronous receipts, without retrying uncertain destructive work.

The deletion helper checkpoints in the SAME transaction as catalog removal.
An intent checkpoint precedes each attempt, so a crash/failed commit cannot be
mistaken for a definitive rejection. Sessions remain in their owning thread.
"""
from contextlib import contextmanager
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from app.models import MutationOperation
from app.schemas.mutation_operations import FailedMutation, MutationReceipt, SkippedMutation

_CHECKPOINT = 'mutation_operation_checkpoint'


def operation_headers(operation_id: str) -> dict[str, str]:
    return {'X-Mutation-Operation-ID': operation_id,
            'X-Mutation-Operation-URL': f'/api/duplicates/operations/{operation_id}'}


def lookup_operation(session: Session, operation_id: str) -> MutationReceipt:
    row = session.get(MutationOperation, operation_id)
    if row is None:
        raise HTTPException(404, 'Mutation operation not found.')
    return MutationReceipt.model_validate(row.receipt)


class Operation:
    def __init__(self, session: Session, row: MutationOperation):
        self.session, self.id = session, row.id

    def _save(self, receipt: MutationReceipt, *, commit: bool = True):
        row = self.session.get(MutationOperation, self.id)
        row.receipt = receipt.model_dump()
        self.session.add(row)
        if commit:
            self.session.commit()

    def start_member(self, media_id: int):
        receipt = lookup_operation(self.session, self.id)
        key = f'media:{media_id}'
        if key not in receipt.unknown:
            receipt.unknown.append(key)
        receipt.state = 'unknown'
        self._save(receipt)

    def applied(self, media_id: int, *, commit: bool = True):
        receipt = lookup_operation(self.session, self.id)
        key = f'media:{media_id}'
        receipt.unknown = [item for item in receipt.unknown if item != key]
        if key not in receipt.applied:
            receipt.applied.append(key)
        self._save(receipt, commit=commit)

    @contextmanager
    def member(self, media_id: int):
        if _CHECKPOINT in self.session.info:
            raise RuntimeError('Nested mutation checkpoint')
        state = {'commit_started': False}
        def checkpoint():
            state['commit_started'] = True
            self.applied(media_id, commit=False)
        self.session.info[_CHECKPOINT] = checkpoint
        try:
            yield
        except Exception as exc:
            self.session.rollback()
            # A commit may have succeeded before transport/cleanup failed. Read
            # the durable checkpoint rather than guessing from the exception.
            self.session.expire_all()
            receipt = lookup_operation(self.session, self.id)
            key = f'media:{media_id}'
            if key not in receipt.applied and isinstance(exc, HTTPException) and not state['commit_started']:
                detail = exc.detail
                code = str(detail.get('code', f'http_{exc.status_code}')) if isinstance(detail, dict) else f'http_{exc.status_code}'
                message = str(detail.get('message', detail)) if isinstance(detail, dict) else str(detail)
                receipt.unknown = [item for item in receipt.unknown if item != key]
                receipt.failed.append(FailedMutation(key=key, code=code, message=message))
                # Validate typed members before storing.
                receipt = MutationReceipt.model_validate(receipt.model_dump())
                self._save(receipt)
            raise
        finally:
            self.session.info.pop(_CHECKPOINT, None)

    def finish(self, response: dict, status_code: int, *, applied_keys=()):
        receipt = lookup_operation(self.session, self.id)
        for key in applied_keys:
            if key not in receipt.applied:
                receipt.applied.append(key)
        row = self.session.get(MutationOperation, self.id)
        if row.snapshot.get('action') != 'MARK_NOT_DUPLICATE':
            accounted = set(receipt.applied + receipt.unknown)
            accounted.update(item.key for item in receipt.failed)
            accounted.update(item.key for item in receipt.skipped)
            for media_id in row.snapshot['selected_ids']:
                key = f'media:{media_id}'
                if key not in accounted:
                    receipt.skipped.append(SkippedMutation(key=key, reason='not_attempted'))
        if receipt.unknown:
            receipt.state = 'unknown'
        elif receipt.failed or receipt.skipped:
            receipt.state = 'partial' if receipt.applied else 'failed'
        elif status_code >= 400:
            receipt.state = 'partial' if receipt.applied else 'failed'
        else:
            receipt.state = 'succeeded'
        row = self.session.get(MutationOperation, self.id)
        row.receipt = receipt.model_dump()
        row.response = response
        row.status_code = status_code
        self.session.add(row)
        self.session.commit()
        return receipt.model_dump()


def begin_operation(session: Session, operation_id: str, snapshot: dict,
                    selected_ids: list[int], *, skipped=()) -> Operation:
    try:
        operation_id = str(UUID(operation_id))
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(400, 'operation_id must be a UUID.')
    existing = session.get(MutationOperation, operation_id)
    if existing is not None:
        raise HTTPException(409, 'Operation already exists; read its receipt.', headers=operation_headers(operation_id))
    # selected IDs are an immutable snapshot, never recomputed during recovery.
    receipt = MutationReceipt(operation_id=operation_id, state='pending', skipped=list(skipped))
    row = MutationOperation(id=operation_id, snapshot={**snapshot, 'selected_ids': list(selected_ids)},
                            receipt=receipt.model_dump())
    session.add(row)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(409, 'Operation already exists; read its receipt.', headers=operation_headers(operation_id))
    return Operation(session, row)


@contextmanager
def mutation_member(session: Session, media_id: int):
    """No-op for legacy requests; opt-in requests get durable intent/checkpoint."""
    operation = session.info.get('mutation_operation')
    if operation is None:
        yield
    else:
        operation.start_member(media_id)
        with operation.member(media_id):
            yield
        # Production deletion checkpoints before its commit. This idempotent
        # fallback also covers alternate deletion implementations/tests.
        operation.applied(media_id)
