"""Execute admitted curation operations as shared-system ProcessingTask jobs.

Admission remains the authority boundary. This module only governs *execution*
of an already-admitted, already-immutable operation snapshot:

* every admitted operation gets one `ProcessingTask` row, so it is visible,
  cancellable and resumable through the same `/api/tasks` surface as scans;
* a database lease taken under SQLite `BEGIN IMMEDIATE` fences a second worker
  out while a live worker holds the operation, with takeover only after the
  lease expires (or when the holder is provably a dead process on this host);
* cancellation is checked at explicit checkpoints before source reads and
  immediately before publication. After publication there is no cancellation:
  published export bytes are content-addressed and immutable.

Nothing here can create a dataset, grant, source, review or operation, lower a
threshold, or turn an agent recommendation into human acceptance.
"""
from __future__ import annotations

import os
import socket
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import HTTPException
from sqlmodel import Session, select

import app.database as db
from app.curation_models import CurationDataset, CurationEvent, CurationGrant, CurationOperation
from app.logger import logger
from app.models import ProcessingTask, Status
from app.services.curation_policy import fail, transaction

EXPORT_TASK_TYPE = 'curation_export'
MATERIALIZE_TASK_TYPE = 'curation_materialize'
TASK_TYPES = {'export': EXPORT_TASK_TYPE, 'materialize': MATERIALIZE_TASK_TYPE}
CURATION_TASK_TYPES = frozenset(TASK_TYPES.values())

# Long enough that an ordinary bounded still export never loses its own lease,
# short enough that a hard crash on a host we cannot probe recovers unattended.
LEASE_SECONDS = 300

_BOOT_NONCE = uuid4().hex


def worker_id() -> str:
    """Identity of this executing process: host, pid and a per-process nonce."""
    return '%s/%d/%s' % (socket.gethostname(), os.getpid(), _BOOT_NONCE)


def new_attempt_id() -> str:
    return uuid4().hex


def now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _code(exc: BaseException) -> str:
    if isinstance(exc, HTTPException) and isinstance(exc.detail, dict):
        return exc.detail.get('code', 'operation_failed')
    return 'operation_failed'


# --------------------------------------------------------------------------
# Lease fencing
# --------------------------------------------------------------------------

def lease_live(op: CurationOperation, moment: datetime | None = None) -> bool:
    return bool(op.lease_expires_at and op.lease_expires_at > (moment or now()))


def holder_is_gone(holder: str | None) -> bool:
    """True only when the lease holder can be *proved* dead; expiry is the rule.

    A worker id is `host/pid/nonce`. On this host a vanished pid is conclusive,
    so a restart recovers immediately instead of idling for the lease window.
    Anything we cannot prove — another host, a live pid, a permission error —
    falls back to waiting for expiry.
    """
    if not holder:
        return True
    parts = holder.split('/')
    if len(parts) != 3 or parts[0] != socket.gethostname() or holder == worker_id():
        return False
    try:
        pid = int(parts[1])
    except ValueError:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except OSError:
        return False
    return False


def acquire_lease(session: Session, operation_id: str, attempt_id: str,
                  seconds: int = LEASE_SECONDS) -> None:
    """Take the execution lease, or refuse to run behind a live worker."""
    holder = worker_id()
    with transaction(session):
        op = session.get(CurationOperation, operation_id, populate_existing=True)
        if op is None:
            fail('not_found', 404)
        moment = now()
        if lease_live(op, moment) and op.lease_worker != holder and not holder_is_gone(op.lease_worker):
            fail('operation_lease_held')
        op.lease_worker = holder
        op.lease_attempt = attempt_id
        op.lease_expires_at = moment + timedelta(seconds=seconds)
        session.add(op)


def require_lease(op: CurationOperation, attempt_id: str) -> None:
    """Re-check ownership inside the transaction that is about to publish."""
    if op.lease_worker != worker_id() or op.lease_attempt != attempt_id or not lease_live(op):
        fail('operation_lease_lost')


def renew_lease(op: CurationOperation, attempt_id: str, seconds: int = LEASE_SECONDS) -> None:
    if op.lease_worker == worker_id() and op.lease_attempt == attempt_id:
        op.lease_expires_at = now() + timedelta(seconds=seconds)


def release_lease(session: Session, operation_id: str, attempt_id: str) -> None:
    """Drop our own lease. Never raises: it must not mask an execution error."""
    try:
        with transaction(session):
            op = session.get(CurationOperation, operation_id, populate_existing=True)
            if op is not None and op.lease_worker == worker_id() and op.lease_attempt == attempt_id:
                op.lease_worker = None
                op.lease_attempt = None
                op.lease_expires_at = None
                session.add(op)
    except Exception:
        logger.warning('Could not release the curation lease for operation %s', operation_id)


# --------------------------------------------------------------------------
# Task linkage, progress and cancellation
# --------------------------------------------------------------------------

def attach_task(session: Session, op: CurationOperation, total: int | None = None) -> ProcessingTask:
    """Create the shared task row for a freshly admitted operation.

    Called inside the admission transaction so the operation and the row the
    operator can see commit together.
    """
    task = ProcessingTask(task_type=TASK_TYPES[op.kind], status=Status.PENDING,
                          total=op.item_count if total is None else total, processed=0,
                          params={'operation_id': op.id})
    session.add(task)
    session.flush()
    op.task_id = task.id
    session.add(op)
    return task


def task_of(session: Session, op: CurationOperation) -> ProcessingTask | None:
    if not op.task_id:
        return None
    return session.get(ProcessingTask, op.task_id, populate_existing=True)


def mark_task(session: Session, op: CurationOperation, status: str, *,
              processed: int | None = None, error: str | None = None) -> None:
    """Keep the task row consistent with the operation, inside the same transaction."""
    task = task_of(session, op)
    if task is None:
        return
    task.started_at = task.started_at or datetime.now()
    task.finished_at = None if status == 'running' else datetime.now()
    if processed is not None:
        task.processed = processed
    if error is not None:
        task.result = {**(task.result or {}), 'error': error}
    task.status = status
    session.add(task)


def cancel_requested(session: Session, op: CurationOperation) -> bool:
    task = task_of(session, op)
    return task is not None and str(task.status) == 'cancelled'


def checkpoint_cancellation(session: Session, op: CurationOperation) -> None:
    """Stop an operation the operator cancelled, before it can publish anything.

    Deliberately write-free: callers are inside the IMMEDIATE transaction that
    this failure rolls back, so the cancelled status is recorded afterwards by
    `curation_plans.record_failure` in its own committed transaction. A
    succeeded operation is never reopened: published bytes cannot be
    un-published.
    """
    if op.status == 'succeeded':
        return
    if op.status == 'cancelled' or cancel_requested(session, op):
        fail('operation_cancelled')


def checkpoint_progress(session: Session, operation_id: str, attempt_id: str, processed: int,
                        *, step: str, item: str | None = None) -> None:
    """Durably record how far this attempt got and renew the lease while working."""
    with transaction(session):
        op = session.get(CurationOperation, operation_id, populate_existing=True)
        if op is None:
            return
        op.progress_done = processed
        renew_lease(op, attempt_id)
        session.add(op)
        task = task_of(session, op)
        if task is not None:
            task.processed = processed
            task.total = op.item_count
            session.add(task)
            # Deferred: `app.tasks` pulls in the API package, which imports this
            # module's callers. Importing it at module scope is a cycle.
            from app.tasks.state import set_task_progress

            set_task_progress(task.id, current_item=item, current_step=step)


def on_task_cancelled(session: Session, task: ProcessingTask) -> None:
    """Propagate a `/api/tasks/{id}/cancel` to its curation operation.

    An operation that already published is left succeeded: cancelling the job
    never rewrites or removes export bytes that exist on disk.
    """
    if task.task_type not in CURATION_TASK_TYPES:
        return
    operation_id = (task.params or {}).get('operation_id')
    if not isinstance(operation_id, str):
        return
    op = session.get(CurationOperation, operation_id, populate_existing=True)
    if op is None or op.status in ('succeeded', 'cancelled'):
        return
    op.status = 'cancelled'
    op.error_code = 'operation_cancelled'
    session.add(op)
    session.add(CurationEvent(operation_id=op.id, event='cancelled', attempt=op.attempts))
    session.commit()


# --------------------------------------------------------------------------
# Workers
# --------------------------------------------------------------------------

def _linked_operation(session: Session, task_id: str, kind: str) -> tuple[ProcessingTask, CurationOperation]:
    task = session.get(ProcessingTask, task_id)
    if task is None:
        raise ValueError('Unknown task ' + str(task_id))
    operation_id = (task.params or {}).get('operation_id')
    if not isinstance(operation_id, str) or not operation_id:
        raise ValueError('Task carries no curation operation id')
    op = session.get(CurationOperation, operation_id)
    if op is None or op.kind != kind:
        raise ValueError('Task does not reference an admitted ' + kind + ' operation')
    return task, op


def _execute_linked_operation(task_id: str, kind: str) -> None:
    from app.services.curation_plans import execute_materialize
    from app.services.frozen_exports import execute_export

    with Session(db.engine) as session:
        task, op = _linked_operation(session, task_id, kind)
        operation_id = op.id
        # A resume successor is a new task row for the same immutable operation.
        # Point the operation at it so cancellation and progress reach the row
        # the operator is watching.
        if op.task_id != task_id:
            op.task_id = task_id
            session.add(op)
            session.commit()
        if op.status == 'succeeded':
            with transaction(session):
                op = session.get(CurationOperation, operation_id, populate_existing=True)
                mark_task(session, op, 'completed', processed=op.item_count)
            return
        if op.status == 'cancelled':
            return
        grant = session.get(CurationGrant, op.grant_id)
        if grant is None:
            with transaction(session):
                op = session.get(CurationOperation, operation_id, populate_existing=True)
                mark_task(session, op, 'failed', error='unauthorized')
            return
        try:
            if kind == 'export':
                execute_export(session, grant, operation_id)
            else:
                execute_materialize(session, grant, operation_id)
        except HTTPException as exc:
            # A blocked operation is a recorded receipt, not a crash. Keep the
            # code on the task so the feed shows why it stopped.
            code = _code(exc)
            logger.info('Curation %s operation %s stopped: %s', kind, operation_id, code)
            if code != 'operation_cancelled':
                with transaction(session):
                    op = session.get(CurationOperation, operation_id, populate_existing=True)
                    mark_task(session, op, 'failed', error=code)
            return


def run_curation_export(task_id: str) -> None:
    _execute_linked_operation(task_id, 'export')


def run_curation_materialize(task_id: str) -> None:
    _execute_linked_operation(task_id, 'materialize')


# --------------------------------------------------------------------------
# Startup reconciliation
# --------------------------------------------------------------------------

def _published_manifest_hash(session: Session, op: CurationOperation) -> str | None:
    """Return the manifest hash iff the export directory already matches the snapshot.

    This reuses the same verifier publication and resume use, so an uncertain
    outcome is only resolved as success when every image, caption, manifest,
    inventory entry and success marker matches the admitted snapshot exactly.
    """
    from app.services.curation_artifacts import directory
    from app.services.frozen_exports import _verify

    dataset = session.get(CurationDataset, op.dataset_id)
    if dataset is None:
        return None
    try:
        with directory(dataset.store_root, (dataset.store_device, dataset.store_inode)) as root:
            exports = os.open('exports', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root)
            try:
                published = os.open(op.id, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=exports)
                try:
                    manifest_hash = _verify(published, op.snapshot)
                finally:
                    os.close(published)
            finally:
                os.close(exports)
    except Exception:
        return None
    if op.manifest_sha256 and op.manifest_sha256 != manifest_hash:
        return None
    return manifest_hash


def _ensure_resumable_task(session: Session, op: CurationOperation, summary: dict) -> None:
    """Leave exactly one resumable task row for this operation. Starts nothing."""
    active = session.exec(select(ProcessingTask).where(
        ProcessingTask.task_type == TASK_TYPES[op.kind],
        ProcessingTask.status.in_(('pending', 'running')))).all()
    for other in active:
        if other.id != op.task_id and (other.params or {}).get('operation_id') == op.id:
            # A successor is already queued; a second row would duplicate work.
            return
    task = task_of(session, op)
    if task is None:
        task = ProcessingTask(task_type=TASK_TYPES[op.kind], status=Status.INTERRUPTED,
                              total=op.item_count, processed=op.progress_done,
                              finished_at=datetime.now(), params={'operation_id': op.id})
        session.add(task)
        session.flush()
        op.task_id = task.id
        session.add(op)
        summary['tasks_created'] += 1
        return
    if str(task.status) in ('pending', 'running'):
        task.status = Status.INTERRUPTED
        task.processed = op.progress_done
        task.finished_at = datetime.now()
        session.add(task)


def reconcile_curation_operations(session: Session) -> dict:
    """Resolve uncertain curation outcomes at startup. Never re-runs a published export.

    For each admitted or running operation whose lease is not held by a live
    worker: a running export whose directory already verifies against its own
    admitted snapshot becomes `succeeded`; anything else returns to `admitted`
    with a resumable task row. Execution itself is left to the ordinary resume
    path, so reconciliation can never launch a duplicate worker.
    """
    summary = {'succeeded': 0, 'readmitted': 0, 'cancelled': 0, 'skipped': 0, 'tasks_created': 0}
    operations = session.exec(select(CurationOperation).where(
        CurationOperation.status.in_(('admitted', 'running')))).all()
    for op in operations:
        op = session.get(CurationOperation, op.id, populate_existing=True)
        if lease_live(op) and not holder_is_gone(op.lease_worker):
            summary['skipped'] += 1
            continue
        task = task_of(session, op)
        if task is not None and str(task.status) == 'cancelled':
            op.status = 'cancelled'
            op.error_code = 'operation_cancelled'
            op.lease_worker = op.lease_attempt = op.lease_expires_at = None
            session.add(op)
            session.add(CurationEvent(operation_id=op.id, event='cancelled', attempt=op.attempts))
            summary['cancelled'] += 1
            continue
        if op.status == 'running' and op.kind == 'export':
            manifest_hash = _published_manifest_hash(session, op)
            if manifest_hash:
                op.status = 'succeeded'
                op.manifest_sha256 = manifest_hash
                op.error_code = None
                op.progress_done = op.item_count
                op.lease_worker = op.lease_attempt = op.lease_expires_at = None
                session.add(op)
                session.add(CurationEvent(operation_id=op.id, event='reconciled_published',
                                          attempt=op.attempts))
                mark_task(session, op, 'completed', processed=op.item_count)
                summary['succeeded'] += 1
                continue
        if op.status == 'running':
            op.status = 'admitted'
            op.error_code = None
            session.add(op)
            session.add(CurationEvent(operation_id=op.id, event='reconciled_admitted',
                                      attempt=op.attempts))
        op.lease_worker = op.lease_attempt = op.lease_expires_at = None
        session.add(op)
        _ensure_resumable_task(session, op, summary)
        summary['readmitted'] += 1
    session.commit()
    if any(summary[key] for key in ('succeeded', 'readmitted', 'cancelled')):
        logger.info('Curation operation reconciliation: %s', summary)
    return summary
