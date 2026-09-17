"""Independent production-boundary regressions; generated pixels and keys only."""
import json
import unittest

from fastapi import HTTPException
from sqlmodel import Session, select

from app.curation_models import CurationCredential, CurationEvent, CurationOperation, CurationReview
from app.schemas.curation import ExportInput, RegistrationInput
from app.services import curation_auth as auth
from app.services.frozen_exports import admit_export, execute_export
import test_curation_auth as auth_fixtures


class ProductionAdversarialTests(unittest.TestCase):
    setUp = auth_fixtures.ProductionAuthorityTests.setUp
    tearDown = auth_fixtures.ProductionAuthorityTests.tearDown
    issue = auth_fixtures.ProductionAuthorityTests.issue
    enroll = auth_fixtures.ProductionAuthorityTests.enroll
    proof = auth_fixtures.ProductionAuthorityTests.proof
    accept = auth_fixtures.ProductionAuthorityTests.accept

    def test_non_object_client_data_registration_is_rejected_without_uncaught_exception(self):
        for value in ([], None, 1, 'not a client-data object'):
            with self.subTest(value_type=type(value).__name__), Session(self.engine) as session:
                options = auth.registration_options(session, self.human)
                credential = self.authenticator.credential(options, registration=True)
                credential['response']['clientDataJSON'] = auth_fixtures.b64(json.dumps(value).encode())
                with self.assertRaises(HTTPException) as raised:
                    auth.registration_verify(session, self.human,
                        RegistrationInput(challenge_id=options['challenge_id'], credential=credential))
                self.assertIn(raised.exception.status_code, (403, 422))
                self.assertEqual(session.exec(select(CurationCredential)).all(), [])

    def test_non_object_client_data_assertion_is_rejected_without_uncaught_exception(self):
        self.enroll()
        for value in ([], None, 1, 'not a client-data object'):
            with self.subTest(value_type=type(value).__name__):
                proof = self.proof()
                proof.presence.credential['response']['clientDataJSON'] = auth_fixtures.b64(json.dumps(value).encode())
                with self.assertRaises(HTTPException) as raised:
                    self.accept(proof)
                self.assertIn(raised.exception.status_code, (403, 422))
                with Session(self.engine) as session:
                    self.assertEqual(session.exec(select(CurationReview)).all(), [])

    def test_foreign_grant_export_denial_does_not_mutate_owner_receipt_or_journal(self):
        self.enroll()
        self.accept(self.proof())
        with Session(self.engine) as session:
            operation_id = admit_export(session, self.agent, self.dataset_id,
                ExportInput(expected_revision=3, idempotency_key='owner-only-export'))
            before = session.get(CurationOperation, operation_id).model_dump()
            events_before = [row.model_dump() for row in session.exec(select(CurationEvent).where(
                CurationEvent.operation_id == operation_id)).all()]
            with self.assertRaises(HTTPException) as raised:
                execute_export(session, self.human, operation_id)
            self.assertEqual(raised.exception.detail['code'], 'operation_owner_required')
            self.assertEqual(session.get(CurationOperation, operation_id, populate_existing=True).model_dump(), before)
            self.assertEqual([row.model_dump() for row in session.exec(select(CurationEvent).where(
                CurationEvent.operation_id == operation_id)).all()], events_before)
        self.assertFalse((self.store / 'exports' / operation_id).exists())
        self.assertFalse((self.store / 'exports').exists())
