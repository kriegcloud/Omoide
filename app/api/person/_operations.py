"""Opt-in durable receipts around the existing person mutation transactions.

Commit listeners checkpoint changed/deleted selected persons in the domain
transaction, including helpers which flush or commit individual bulk members.
A lost response therefore cannot turn a completed deletion into a fresh attempt.
"""
from functools import wraps
from inspect import signature
from uuid import UUID

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy import event

from app.models import MutationOperation, Person
from app.schemas.mutation_operations import FailedMutation, SkippedMutation
from app.services.mutation_operations import begin_operation, lookup_operation


def person_attempt(session, person_id):
    context = session.info.get('person_receipt_context')
    if context is not None:
        context['current'] = f'person:{person_id}'



def person_failure(session, person_id, error):
    """Preserve known member rejection versus a swallowed uncertain exception."""
    context = session.info.get('person_receipt_context')
    if context is None:
        return
    key = f'person:{person_id}'
    if isinstance(error, HTTPException):
        if error.status_code == 404:
            context['skipped'][key] = 'not_found'
        else:
            context['failed'][key] = error
    else:
        context['uncertain'].add(key)


def person_operation(action):
    def decorate(function):
        call_signature = signature(function)

        @wraps(function)
        def wrapped(*args, **kwargs):
            bound = call_signature.bind(*args, **kwargs)
            bound.apply_defaults()
            arguments = bound.arguments
            session = arguments['session']
            payload = next((arguments[name] for name in ('payload', 'request', 'body', 'data')
                            if name in arguments), None)
            operation_id = arguments.get('operation_id') or getattr(payload, 'operation_id', None)
            if not operation_id:
                return function(*args, **kwargs)
            values = {key: jsonable_encoder(value) for key, value in arguments.items()
                      if key not in ('session', 'operation_id')}
            for value in values.values():
                if isinstance(value, dict):
                    value.pop('operation_id', None)
            if hasattr(payload, 'person_ids'):
                selected = list(dict.fromkeys(payload.person_ids))
            elif hasattr(payload, 'source_ids'):
                selected = list(dict.fromkeys(payload.source_ids))
            elif hasattr(payload, 'source_id'):
                selected = [payload.source_id]
            else:
                selected = [arguments['person_id']]
            if action == 'person.merge_similar':
                from .merging import get_similarities
                from app.config import settings
                threshold = float(getattr(settings.face_recognition, 'person_merge_percent_similarity', 75.0))
                # Only first attempts enumerate candidates. Replay uses stored selection.
                selected = []
            snapshot = {'action': action, 'request': values}
            # Validate UUID before querying (canonical UUID is the storage key).
            try:
                operation_id = str(UUID(str(operation_id)))
            except (ValueError, TypeError, AttributeError):
                raise HTTPException(400, 'operation_id must be a UUID.')
            existing = session.get(MutationOperation, operation_id)
            headers = {'X-Mutation-Operation-ID': operation_id,
                       'X-Mutation-Operation-URL': f'/api/person/operations/{operation_id}'}
            if existing is not None:
                if {key: value for key, value in existing.snapshot.items() if key != 'selected_ids'} != snapshot:
                    raise HTTPException(409, 'operation_id is bound to a different request.', headers=headers)
                if existing.response is not None and existing.status_code is not None:
                    return JSONResponse(existing.response, status_code=existing.status_code, headers=headers)
                raise HTTPException(409, 'Operation outcome is not terminal; read its receipt.', headers=headers)
            if action == 'person.merge_similar':
                selected = list(dict.fromkeys(person.id for person in get_similarities(arguments['person_id'], session)
                                             if person.id is not None and person.similarity is not None
                                             and float(person.similarity) >= threshold))
                # Pass the frozen candidates into the handler; never rediscover after intent.
            operation = begin_operation(session, operation_id, snapshot, selected)
            if action == 'person.merge_similar':
                session.info['person_merge_candidates'] = selected
            receipt = lookup_operation(session, operation.id)
            receipt.state = 'unknown'
            receipt.unknown = [f'person:{pid}' for pid in selected]
            row = session.get(MutationOperation, operation.id)
            row.receipt = receipt.model_dump()
            session.add(row)
            session.commit()
            selected_set = set(selected)
            staged = set()
            context = {'failed': {}, 'skipped': {}, 'uncertain': set()}
            session.info['person_receipt_context'] = context

            def collect(_session, *_):
                for person in list(session.dirty) + list(session.deleted):
                    if isinstance(person, Person) and person.id in selected_set and (
                        person in session.deleted or session.is_modified(person, include_collections=False)
                    ):
                        staged.add(f'person:{person.id}')

            def checkpoint(_session):
                collect(session)
                if not staged:
                    return
                current = lookup_operation(session, operation.id)
                current.applied = list(dict.fromkeys(current.applied + [f'person:{pid}' for pid in selected
                                                                        if f'person:{pid}' in staged]))
                current.unknown = [key for key in current.unknown if key not in staged]
                row = session.get(MutationOperation, operation.id)
                row.receipt = current.model_dump()
                session.add(row)

            def reset(_session):
                staged.clear()

            event.listen(session, 'before_flush', collect)
            event.listen(session, 'before_commit', checkpoint)
            event.listen(session, 'after_commit', reset)
            event.listen(session, 'after_rollback', reset)
            try:
                try:
                    result = function(*args, **kwargs)
                    if isinstance(result, HTTPException):
                        raise result
                    response = jsonable_encoder(result) if result is not None else {}
                    status_code = 200
                    failure = None
                except HTTPException as exc:
                    session.rollback()
                    response, status_code, failure = {'detail': jsonable_encoder(exc.detail)}, exc.status_code, exc
                except Exception:
                    session.rollback()
                    # Preserve unknown keys for uncertain failures and crash recovery.
                    raise
                receipt = lookup_operation(session, operation.id)
                for key in list(receipt.unknown):
                    member_error = context['failed'].get(key)
                    if member_error is None and failure is not None:
                        if context.get('current') in (None, key):
                            member_error = failure
                        else:
                            context['skipped'].setdefault(key, 'not_attempted')
                    if member_error is not None:
                        receipt.failed.append(FailedMutation(key=key, code=f'http_{member_error.status_code}', message=str(member_error.detail)))
                    elif key in context['uncertain']:
                        continue
                    else:
                        receipt.skipped.append(SkippedMutation(key=key, reason=context['skipped'].get(key, 'not_changed')))
                    receipt.unknown.remove(key)
                if receipt.unknown:
                    receipt.state = 'unknown'
                elif receipt.failed or receipt.skipped:
                    receipt.state = 'partial' if receipt.applied else 'failed'
                else:
                    receipt.state = 'succeeded'
                receipt.response = dict(response)
                response['receipt'] = receipt.model_dump()
                row = session.get(MutationOperation, operation.id)
                row.receipt, row.response, row.status_code = receipt.model_dump(), response, status_code
                session.add(row)
                session.commit()
                return JSONResponse(response, status_code=status_code, headers=headers)
            finally:
                for name, listener in (('before_flush', collect), ('before_commit', checkpoint),
                                       ('after_commit', reset), ('after_rollback', reset)):
                    event.remove(session, name, listener)
                session.info.pop('person_merge_candidates', None)
                session.info.pop('person_receipt_context', None)
        return wrapped
    return decorate
