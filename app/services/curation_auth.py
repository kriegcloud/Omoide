"""Grant-bound WebAuthn ceremonies; no HTTP principal or credential issuance.

Attestation is deliberately not a hardware provenance claim. UP and UV plus an
exact single-use request binding are verified by the pinned WebAuthn library.
The trusted local operator and database remain part of the trust boundary.
"""
import base64
import json
import os
import re
import secrets
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit

from sqlmodel import Session, select

from app.config import settings
from app.curation_models import CurationChallenge, CurationCredential, CurationDataset, CurationGrant, now
from app.services.curation_artifacts import canonical
from app.services.curation_policy import (PRODUCTION_POLICY_VERSION, authorize, dataset_for,
    digest, fail, mode, require_revision)

CHALLENGE_SECONDS = 300
MAX_CREDENTIAL_BYTES = 65536


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b'=').decode('ascii')


def _unb64(value: str) -> bytes:
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]*', value):
        raise ValueError('Invalid base64url')
    return base64.b64decode(value + '=' * (-len(value) % 4), altchars=b'-_', validate=True)


def relying_party() -> tuple[str, str]:
    """Require exact server-owned configuration; never derive it from Host."""
    rp_id = os.environ.get('OMOIDE_CURATION_RP_ID', '')
    origin = os.environ.get('OMOIDE_CURATION_ORIGIN', '')
    try:
        parsed = urlsplit(origin)
        if (not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?', rp_id)
                or '..' in rp_id or parsed.hostname != rp_id
                or parsed.username is not None or parsed.password is not None
                or parsed.path or parsed.query or parsed.fragment
                or parsed.scheme not in {'http', 'https'}
                or (parsed.scheme == 'http' and rp_id != 'localhost')
                or (parsed.port is not None and not 1 <= parsed.port <= 65535)
                or origin != f'{parsed.scheme}://{rp_id}' + (f':{parsed.port}' if parsed.port is not None else '')):
            fail('presence_configuration_invalid', 503)
    except ValueError:
        fail('presence_configuration_invalid', 503)
    return rp_id, origin


def _production():
    if mode() != 'production':
        fail('production_authority_required', 403)


def credential_for(session, grant):
    return session.exec(select(CurationCredential).where(
        CurationCredential.grant_id == grant.id).execution_options(populate_existing=True)).first()


def auth_status(session: Session, token: str):
    grant = authorize(session, token)
    credential = credential_for(session, grant) if mode() == 'production' else None
    return {'actor_kind': grant.actor_kind,
            'enrolled': bool(credential and not credential.revoked),
            'enrollment_available': mode() == 'production' and grant.actor_kind == 'human' and credential is None and 'enroll' in grant.operations}


def _human(session, token, operation):
    _production()
    grant = authorize(session, token, operation=operation)
    if grant.actor_kind != 'human':
        fail('review_authority_required', 403)
    dataset, _ = dataset_for(session, token, grant.dataset_id, operation)
    return dataset, grant


def _request_hash(dataset, grant, purpose, request=None):
    return digest(canonical({'purpose': purpose, 'dataset_id': dataset.id,
        'grant_id': grant.id, 'actor_id': grant.actor_id, 'actor_kind': grant.actor_kind,
        'policy_version': dataset.policy_version, 'policy_sha256': digest(canonical(dataset.policy)),
        'request': request.model_dump(exclude={'presence'}) if request is not None else None}))


def _challenge(session, dataset, grant, purpose, request=None, credential=None):
    active = session.exec(select(CurationChallenge).where(
        CurationChallenge.grant_id == grant.id, CurationChallenge.consumed_at.is_(None),
        CurationChallenge.expires_at > now())).all()
    if len(active) >= 8:
        fail('presence_challenge_limit', 429)
    rp_id, origin = relying_party()
    row = CurationChallenge(grant_id=grant.id, purpose=purpose,
        challenge=_b64(secrets.token_bytes(32)), request_sha256=_request_hash(dataset, grant, purpose, request),
        credential_id=credential.id if credential else None, rp_id=rp_id, origin=origin,
        expires_at=now() + timedelta(seconds=CHALLENGE_SECONDS))
    session.add(row)
    session.flush()
    return row


def _load_challenge(session, dataset, grant, challenge_id, purpose, request=None):
    row = session.get(CurationChallenge, challenge_id, populate_existing=True)
    if not row or row.grant_id != grant.id or row.purpose != purpose:
        fail('presence_challenge_invalid', 403)
    if row.consumed_at is not None:
        fail('presence_challenge_used', 409)
    if row.expires_at <= now():
        fail('presence_challenge_expired', 409)
    if (row.rp_id, row.origin) != relying_party():
        fail('presence_configuration_changed', 409)
    if row.request_sha256 != _request_hash(dataset, grant, purpose, request):
        fail('presence_request_changed', 409)
    return row


def _credential_payload(credential):
    try:
        serialized = json.dumps(credential, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
        if not isinstance(credential, dict) or len(serialized) > MAX_CREDENTIAL_BYTES:
            fail('presence_response_invalid', 422)
        client = json.loads(_unb64(credential['response']['clientDataJSON']))
        # The library checks signed origin; explicitly exclude embedded ceremonies.
        if (not isinstance(client, dict) or client.get('crossOrigin', False) is not False
                or client.get('topOrigin') is not None):
            fail('presence_response_invalid', 403)
        return serialized
    except (KeyError, ValueError, TypeError, UnicodeError, RecursionError):
        fail('presence_response_invalid', 422)


def registration_options(session, token):
    from webauthn import generate_registration_options, options_to_json
    from webauthn.helpers.structs import AuthenticatorSelectionCriteria, UserVerificationRequirement
    from app.services.curation_plans import transaction
    with transaction(session):
        dataset, grant = _human(session, token, 'enroll')
        if credential_for(session, grant) is not None:
            fail('credential_already_enrolled')
        challenge = _challenge(session, dataset, grant, 'register')
        options = generate_registration_options(rp_id=challenge.rp_id, rp_name='Omoide still review',
            user_id=grant.id.encode(), user_name=grant.actor_id,
            challenge=_unb64(challenge.challenge), timeout=CHALLENGE_SECONDS * 1000,
            authenticator_selection=AuthenticatorSelectionCriteria(user_verification=UserVerificationRequirement.REQUIRED))
        result = {'challenge_id': challenge.id, 'public_key': json.loads(options_to_json(options))}
    return result


def registration_verify(session, token, request):
    from webauthn import verify_registration_response
    from app.services.curation_plans import transaction
    with transaction(session):
        dataset, grant = _human(session, token, 'enroll')
        if credential_for(session, grant) is not None:
            fail('credential_already_enrolled')
        challenge = _load_challenge(session, dataset, grant, request.challenge_id, 'register')
        raw = _credential_payload(request.credential)
        try:
            verified = verify_registration_response(credential=request.credential,
                expected_challenge=_unb64(challenge.challenge), expected_rp_id=challenge.rp_id,
                expected_origin=challenge.origin, require_user_presence=True, require_user_verification=True)
        except Exception:
            # Verifier messages can include client-controlled credential material.
            fail('presence_verification_failed', 403)
        identifier = _b64(verified.credential_id)
        if session.exec(select(CurationCredential).where(CurationCredential.credential_id == identifier)).first():
            fail('credential_already_registered')
        session.add(CurationCredential(grant_id=grant.id, credential_id=identifier,
            public_key=_b64(verified.credential_public_key), sign_count=verified.sign_count,
            rp_id=challenge.rp_id, origin=challenge.origin, registration_sha256=digest(raw)))
        challenge.consumed_at = now()
        session.add(challenge)
    return {'enrolled': True}


def _review_target(session, dataset, request):
    from app.services.curation_plans import artifact_for, generative_ancestry, latest_caption
    from app.services.curation_artifacts import artifact_bytes
    require_revision(dataset, request.expected_revision)
    artifact = artifact_for(session, dataset, request.artifact_id)
    if generative_ancestry(session, dataset, artifact):
        fail('generative_disabled', 403)
    caption = latest_caption(session, artifact)
    if not caption or caption.id != request.caption_id or caption.sha256 != request.caption_sha256 or artifact.sha256 != request.asset_sha256:
        fail('review_hash_conflict')
    artifact_bytes(dataset, artifact)
    return artifact, caption


def review_options(session, token, dataset_id, request):
    from webauthn import generate_authentication_options, options_to_json
    from webauthn.helpers.structs import PublicKeyCredentialDescriptor, UserVerificationRequirement
    from app.services.curation_plans import transaction
    with transaction(session):
        _production()
        dataset, grant = dataset_for(session, token, dataset_id, 'review')
        if request.presence is not None:
            fail('presence_not_allowed', 422)
        _review_target(session, dataset, request)
        credential = credential_for(session, grant)
        if not credential:
            fail('credential_not_enrolled', 403)
        if credential.revoked:
            fail('review_authority_revoked', 403)
        if (credential.rp_id, credential.origin) != relying_party():
            fail('presence_configuration_changed')
        challenge = _challenge(session, dataset, grant, 'review', request, credential)
        options = generate_authentication_options(rp_id=challenge.rp_id,
            challenge=_unb64(challenge.challenge), timeout=CHALLENGE_SECONDS * 1000,
            allow_credentials=[PublicKeyCredentialDescriptor(id=_unb64(credential.credential_id))],
            user_verification=UserVerificationRequirement.REQUIRED)
        result = {'challenge_id': challenge.id, 'public_key': json.loads(options_to_json(options))}
    return result


def verify_review_presence(session, dataset, grant, request):
    """Called inside the same IMMEDIATE transaction that inserts the review."""
    from webauthn import verify_authentication_response
    _production()
    if grant.actor_kind != 'human' or 'review' not in grant.operations:
        fail('review_authority_required', 403)
    if not request.presence:
        fail('human_presence_required', 403)
    challenge = _load_challenge(session, dataset, grant, request.presence.challenge_id, 'review', request)
    credential = credential_for(session, grant)
    if not credential or credential.revoked or credential.id != challenge.credential_id:
        fail('review_authority_revoked', 403)
    if (credential.rp_id, credential.origin) != (challenge.rp_id, challenge.origin):
        fail('presence_configuration_changed')
    raw = _credential_payload(request.presence.credential)
    try:
        if request.presence.credential['id'] != credential.credential_id:
            fail('presence_verification_failed', 403)
        user_handle = request.presence.credential['response'].get('userHandle')
        if user_handle is not None and _unb64(user_handle) != grant.id.encode():
            fail('presence_verification_failed', 403)
        verified = verify_authentication_response(credential=request.presence.credential,
            expected_challenge=_unb64(challenge.challenge), expected_rp_id=challenge.rp_id,
            expected_origin=challenge.origin, credential_public_key=_unb64(credential.public_key),
            credential_current_sign_count=credential.sign_count, require_user_verification=True)
    except Exception:
        fail('presence_verification_failed', 403)
    credential.sign_count = verified.new_sign_count
    challenge.consumed_at = now()
    session.add(credential)
    session.add(challenge)
    return {'method': 'webauthn', 'user_present': True, 'user_verified': True,
            'credential_id': credential.id, 'challenge_id': challenge.id,
            'request_sha256': challenge.request_sha256, 'assertion_sha256': digest(raw),
            'rp_id': challenge.rp_id, 'origin': challenge.origin,
            'verified_at': challenge.consumed_at.isoformat() + 'Z'}


def review_authority_error(session, dataset, review):
    """Historical review evidence remains bound to a non-revoked authority."""
    grant = session.get(CurationGrant, review.grant_id, populate_existing=True)
    if not grant or grant.revoked or grant.dataset_id != dataset.id or grant.actor_id != review.actor_id:
        return 'review_authority_revoked'
    if dataset.policy_version != PRODUCTION_POLICY_VERSION:
        return None if review.actor_kind == grant.actor_kind == 'fixture_human' else 'review_stale'
    evidence = review.presence_evidence
    if (review.actor_kind != 'human' or grant.actor_kind != 'human'
            or evidence.get('method') != 'webauthn' or evidence.get('user_present') is not True
            or evidence.get('user_verified') is not True):
        return 'human_presence_required'
    credential = session.get(CurationCredential, evidence.get('credential_id', ''), populate_existing=True)
    challenge = session.get(CurationChallenge, evidence.get('challenge_id', ''), populate_existing=True)
    if not credential or credential.revoked or credential.grant_id != grant.id:
        return 'review_authority_revoked'
    if (not challenge or challenge.grant_id != grant.id or challenge.purpose != 'review'
            or challenge.credential_id != credential.id or challenge.consumed_at is None
            or challenge.request_sha256 != evidence.get('request_sha256')
            or (credential.rp_id, credential.origin) != relying_party()):
        return 'review_stale'
    from app.schemas.curation import ReviewInput
    reviewed_request = ReviewInput(artifact_id=review.artifact_id, caption_id=review.caption_id,
        asset_sha256=review.asset_sha256, caption_sha256=review.caption_sha256,
        decision=review.decision, rationale=review.rationale, expected_revision=review.revision - 1)
    if challenge.request_sha256 != _request_hash(dataset, grant, 'review', reviewed_request):
        return 'review_stale'
    return None


def issue_production_grant(session: Session, *, dataset_id: str, actor_id: str,
        actor_kind: str, operations: list[str], credential_file: Path,
        disclosure: bool = False, lifetime_seconds: int = 28800) -> dict:
    """Trusted local operator only. Writes a new 0600 token file; never returns it.

    No HTTP route calls this helper. A failed commit can retain a private inactive
    token file, never a partially issued usable grant. Rotation issues a new grant.
    """
    from app.services.curation_plans import transaction
    _production()
    if settings.general.presentation_mode:
        fail('presentation_mode', 403)
    if (actor_kind not in {'human', 'agent'} or not isinstance(actor_id, str)
            or not 1 <= len(actor_id) <= 128 or any(ord(char) < 32 for char in actor_id)
            or type(lifetime_seconds) is not int or not 60 <= lifetime_seconds <= 86400):
        fail('invalid_grant', 422)
    allowed = {'read', 'materialize', 'caption', 'export'}
    if actor_kind == 'human':
        allowed |= {'review', 'enroll', 'preview'}
    if (not isinstance(operations, list) or not operations or not all(isinstance(item, str) for item in operations)
            or len(set(operations)) != len(operations) or not set(operations) <= allowed
            or ('preview' in operations and not disclosure) or (disclosure and actor_kind != 'human')):
        fail('invalid_grant', 422)
    with transaction(session):
        dataset = session.get(CurationDataset, dataset_id)
        if not dataset or dataset.policy_version != PRODUCTION_POLICY_VERSION or dataset.policy.get('fixture_only') is not False:
            fail('authority_mode_mismatch', 403)
        secret = secrets.token_urlsafe(32)
        grant = CurationGrant(token_sha256=digest(secret.encode()), actor_id=actor_id,
            actor_kind=actor_kind, dataset_id=dataset_id, disclosure=disclosure,
            operations=operations, expires_at=now() + timedelta(seconds=lifetime_seconds))
        session.add(grant)
        session.flush()
        fd = os.open(credential_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w') as output:
            output.write(secret + '\n')
            output.flush()
            os.fsync(output.fileno())
        result = {'grant_id': grant.id, 'dataset_id': dataset_id, 'actor_kind': actor_kind,
                  'expires_at': grant.expires_at.isoformat() + 'Z'}
    return result


def revoke_production_authority(session, *, grant_id: str, credential_id: str | None = None):
    """Trusted local revocation. Retains all provenance and prevents reenrollment."""
    from app.services.curation_plans import transaction
    _production()
    if settings.general.presentation_mode:
        fail('presentation_mode', 403)
    with transaction(session):
        grant = session.get(CurationGrant, grant_id)
        if not grant or grant.actor_kind not in {'human', 'agent'}:
            fail('not_found', 404)
        if credential_id is None:
            grant.revoked = True
            session.add(grant)
        else:
            credential = session.get(CurationCredential, credential_id)
            if not credential or credential.grant_id != grant_id:
                fail('not_found', 404)
            credential.revoked = True
            session.add(credential)
