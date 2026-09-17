# Production still authority implementation

This additive phase implements a disabled production still-review mode. It does
not enroll a person, verify physical hardware, authorize a production migration,
or enable generative changes. The fixture contract remains documented separately
in `contracts.md`.

`OMOIDE_CURATION_MODE=production` selects `production-stills-v1`; unset mode is
disabled unless the existing `OMOIDE_CURATION_FIXTURES=1` compatibility flag is
set. An explicit mode takes precedence over that flag. Invalid modes fail closed.
Fixture grants and fixture datasets are ineligible in production. Production mode
also denies the legacy human-stamping operations listed below.

## Authority and enrollment

`curation_auth.issue_production_grant` is a trusted local operator helper with no
HTTP equivalent. It requires an existing exact production dataset, explicit actor
ID/kind and operations, an 8-hour default lifetime (bounded to 60 seconds–24 hours),
and a new credential file. The helper creates that file exclusively with mode
0600 and stores only a hash of the random bearer token in the database. It returns
grant metadata without the token. Presentation mode blocks issuance and revocation.

Agents may receive `read`, `materialize`, `caption`, and `export` operations. Human
grants may additionally receive `enroll`, `review`, and `preview`; previews also
require a disclosure grant. A bearer authenticates the granted operation only;
it is never evidence of a human review. A grant with `read` alone is the metadata
lane. Grant issuance is not HTTP enrollment and is not inferred from old records.

The human enrollment ceremony requires that grant's `enroll` operation. Its first
successful registration permanently binds one public key to the grant. Revoking
the public key does not reopen enrollment: the local operator must issue a new
grant for rotation. `revoke_production_authority` can revoke a grant or its exact
credential while retaining historical evidence. Grant expiry stops new requests;
explicit revocation also blocks publication relying on its existing reviews.

RP configuration is mandatory and server owned:

- `OMOIDE_CURATION_RP_ID` is the exact origin hostname.
- `OMOIDE_CURATION_ORIGIN` is the exact scheme, hostname and optional port, with
  no path, fragment, userinfo or query. HTTP is allowed only on `localhost`.
- The browser must actually use that configured origin. The API never derives
  authority from the incoming Host, Origin or Referer headers.

For example an isolated demonstration can use `localhost` and
`http://localhost:18124`. This is configuration documentation, not deployment.
Changing RP configuration invalidates outstanding challenges and prevents use of
credentials registered for the previous configuration.

`webauthn==3.0.0` verifies registration and authentication responses, including
signature, challenge, RP ID, exact signed origin, user presence and user
verification. Embedded/cross-origin ceremonies are rejected. Attestation defaults
to `none`: accepted UP/UV proves the protocol assertions under the configured
trust boundary, not hardware provenance or a claim that an agent could not own a
software authenticator. The grant, authenticator, local operator, database and
configuration must be kept outside the adapter's authority. This does not protect
against an administrator or hostile process with full access to the same OS user.

## Exact review ceremony

The following endpoints are under `/api/curation`, with bearer authorization:

| Endpoint | Input and response |
| --- | --- |
| `GET /auth/status` | `{enrolled, actor_kind, enrollment_available}`. Enrollment is never review presence. |
| `POST /auth/registration/options` | `{}` → `{challenge_id, public_key}` with browser WebAuthn creation options. |
| `POST /auth/registration/verify` | `{challenge_id, credential}` → `{enrolled:true}`. |
| `POST /datasets/{id}/review-options` | Exact existing review request without presence → `{challenge_id, public_key}`. |
| `POST /datasets/{id}/reviews` | Existing review fields plus `presence:{challenge_id,credential}`. |

All production decisions (`accept`, `reject`, `defer`) require a fresh assertion.
The challenge binds its random value to the grant, actor, dataset, complete policy
hash, requested draft revision, artifact/caption identifiers and hashes, decision
and exact rationale. It expires after five minutes. At most eight unconsumed,
unexpired challenges are allowed per grant. Requests are bounded to 256 KiB and
individual credential payloads to 64 KiB. Error responses do not contain verifier
exception text or credentials.

The review service rechecks permission, presentation mode, revision, lineage,
caption and current artifact bytes. Signature verification, authenticator counter
update, challenge consumption and append-only review insertion occur in the same
SQLite IMMEDIATE transaction. A changed decision/rationale cannot reuse a proof;
a new caption or draft revision invalidates it. Direct service callers use the
same gate. Fixture decisions do not acquire production evidence.

`actor.human_presence_verified` remains false: a browsing bearer cannot prove
presence. An individual verified review exposes its separate evidence. The UI
must not interpret enrollment as acceptance or suppress existing eligibility
blockers. No generative or unknown-lineage item becomes trainable through this
ceremony.

## Legacy coexistence

In production mode the central middleware denies these operations regardless of
whether Authorization is supplied, forged or omitted:

- `PATCH /api/datasets/{dataset_id}/items/{item_id}/caption`
- `POST /api/datasets/{dataset_id}/items/{item_id}/caption/reviewed`
- `POST /api/datasets/{dataset_id}/items/{item_id}/review`
- `POST /api/annotations/annotations/{annotation_id}/revisions`
- `POST /api/annotations/annotations/{annotation_id}/approve`
- `PATCH /api/datasets/{dataset_id}/items/{item_id}` when the JSON object contains
  `reviewed_at`, including null or a client-supplied historical date.

Ordinary item edits and other legacy operations retain their existing routes.
The protected annotation modules are unchanged. These denied UI actions must use
the new curation review flow; deployment must disclose that change. Old markers
are never backfilled as verified acceptance. Fixture mode retains its original
blanket legacy-mutation denial and remains unsuitable for a shared service.

## Export evidence and retention

Eligibility and publication recheck the exact review evidence, credential and
grant revocation, policy, source bytes and split conflicts. Export admissions
retain the complete private snapshot needed for exact revalidation. Published
production manifests project that snapshot into an explicit safe shape:

- exact image/caption/source hashes, membership and transform provenance;
- review IDs, authority, UP/UV and evidence hashes;
- complete policy and source-registration hashes plus a small policy summary;
- a hash of the RP/origin pair, not the raw authentication origin.

Volume UUIDs, mountpoints, source device names, relative source locators, operator
statements and private evidence references stay in the private database snapshot.
The manifest hash, success marker, verifier and crash recovery all use the same
published projection. Existing fixture manifests retain their historical shape.
Source-registration assertions are operator attestations, not independently
verified identity or rights facts.

Already published files remain immutable after authority revocation. A receipt
records what was verified at publication; it does not claim authority is still
valid today. Revalidation/recovery or new publication can be blocked by later
revocation without rewriting the earlier bytes or receipt.

The new migration is `60718293a4b5`, descending from fixture head `5f60718293a4`.
It adds public credential/challenge records and review presence evidence. Empty
upgrade/downgrade is supported; populated authority/evidence downgrade is refused.
Disabling the feature retains all history and output.

## Scoped verification

`tests/test_curation_auth.py` constructs real P-256 signed synthetic WebAuthn
registration/assertion payloads and uses the actual pinned verifier. It covers
positive API/service/export flow; incorrect origin/RP/challenge/signature/identity;
missing UP/UV; cross-origin ceremonies; replay/expiry; changed policy/decision/
rationale/image/caption; revoked or expired authority; one-time enrollment;
presentation mode; generative denial; legacy-route coexistence; migration; and
private-manifest projection plus recovery after publication.

Tests use generated geometric pixels, temporary SQLite databases and a synthetic
volume observer supplied only by test mocks. Descriptor and file-hash checks still
run. They do not enroll the user, touch a live library, prove physical human
presence, or establish production-container readiness. Repository-wide checks and
real-browser integration proof are retained by the parent implementation lane.

Protocol reference: [py_webauthn registration](https://duo-labs.github.io/py_webauthn/registration.html),
[authentication](https://duo-labs.github.io/py_webauthn/authentication.html).
