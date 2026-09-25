# Backend implementation report

Implemented in a linked worktree on branch `feat/curation-still-slice` on 2026-09-16. The baseline was `717761a87e978dcaadc0eef0d7bb986363e5ee52`. This report covers the backend lane; root owns complete repository checks and isolated browser proof. Nothing here asserts a production deployment, real human presence, or dataset/training quality result.

## Result and ownership

The additive backend provides a disabled-by-default fixture authority, exact-source registration, deterministic final still materialization, immutable caption proposals, exact image/caption decisions, revision conflicts, immutable export admission, and restart verification. Server-generated fixture reviewer credentials exercise the reviewer role while reporting `human_presence_verified=false`. Generative acceptance and generation are unconditionally unavailable. The [contract](contracts.md) defines inputs, API responses, narrowed formats, storage semantics, and later integration gaps.

Owned files:

- `app/curation_models.py`: eight additive tables, immutable content and append-only decision/event records through the service; no legacy Media/Person/Annotation foreign keys.
- `app/schemas/curation.py`, `app/api/curation.py`: closed request schemas and bounded authenticated API routes.
- `app/services/curation_policy.py`: common service authorization, feature/presentation gates, actor/expiry/revocation checks and application middleware that freezes legacy mutations in fixture mode.
- `app/services/curation_artifacts.py`: descriptor-anchored reads, source-change fences, supported format policy, exact image/pixel identity, durable no-clobber artifact publication.
- `app/services/curation_plans.py`: read-only state, revision serialization, durable materialization admissions, caption and review services.
- `app/services/frozen_exports.py`: exact immutable snapshots, known split/byte/pixel closure, current grant/review/source revalidation, independent byte copies, atomic no-clobber Linux directory publication and crash reconciliation.
- `app/services/curation_fixtures.py`: local-only rights-clear test registration and random scoped credentials; no HTTP credential issuance.
- `app/main.py`, `app/models.py`: four-line router/guard integration and three-line metadata registration.
- `alembic/versions/5f60718293a4_fixture_still_curation.py`: additive child of live-source-verified head `4e5f60718293`; populated downgrade refuses to erase provenance.
- `tests/test_curation_authority_gap.py`, `tests/test_curation_still_slice.py`: backend integration tests. Independent reviewer owns `tests/test_curation_adversarial.py`.

Protected annotation modules, thresholds, shared virtual environment, main checkout, live database/container, GPU/model state, and existing training campaigns were not changed. No commit, push, deploy or private-drive traversal was performed by this lane.

## Focused evidence

All commands below ran from the ux5 worktree using its existing `.venv` symlink. Fixtures and databases were created under temporary task-owned directories.

| Command / check | Result |
| --- | --- |
| `.venv/bin/alembic heads` before implementation, 22:50 UTC | One head, `4e5f60718293` |
| `.venv/bin/python -m unittest discover -s tests -p test_curation_authority_gap.py` before guard, 22:52 UTC | Failed as expected: forged agent/client actor fields reached an unguarded human-stamping handler; HTTP 200 versus expected 403 |
| Same command after guard, 22:59 UTC | 1 test passed |
| `.venv/bin/python -m unittest discover -s tests -p test_curation_still_slice.py`, final own-suite run 23:08 UTC | 22 tests passed in 8.300 s, process exit 0 |
| `.venv/bin/python -m unittest discover -s tests -p 'test_curation*py'`, earlier 23:06 UTC | 24 then-existing tests passed; later four own and fourteen reviewer cases increased coverage, so this is not the final aggregate count |
| Independent adversarial script at AI hub `runs/implementation/krea2-curation-still-slice/review/test_review_regressions.py` | Initial source-root race reproduced; later normalized-pixel holdout gap reproduced. Reviewer retained failing-first logs and reports 14 permanent cases passing; root runs final aggregate |
| `.venv/bin/alembic heads`, 23:07 UTC | One head, `5f60718293a4` |
| Temporary full Alembic chain, 23:09 UTC: fresh XDG config/data path, `command.upgrade(Config('alembic.ini'),'head')`, integrity/FK checks, `command.downgrade(...,'4e5f60718293')`, `command.upgrade(...,'head')` | Full chain succeeded; actual head `5f60718293a4`; integrity `ok`; zero FK violations; eight curation tables; empty additive downgrade/reupgrade passed |

The own-suite migration test separately verifies the empty schema downgrade/upgrade and refuses downgrade after inserting a curation record. It never points at a production DB.

Meaningful tested behaviors include server actor spoof rejection, disabled status, immutable GETs, agent media-disclosure denial, direct-service presentation denial, stale revision and exact-caption conflicts, reject/defer blocking, agent/reviewer revocation, indirect loose-preset rejection, transitive generative ancestry, source unknown lineage, EXIF orientation, explicit unsupported ICC/alpha/TIFF/corrupt input, FIFO/symlink/traversal/source-swap rejection, source unchanged hashes, sidecar exact UTF-8 preservation during concurrent caption edits, independent export inode identity, two-process same-key single logical export, changed-payload conflict, request size, expired credential, revoked grant during copy, crash before artifact promotion, crash after staging creation, crash after publication before DB success, and verified retry.

Independent review found and drove fixes for destination-root replacement, stale directory enumeration, normalized-pixel holdout leakage, late known holdout conflicts, child output-directory replacement, and replacing the published version directory during verification. The final service reopens and verifies the configured published directory under the terminal DB transaction before recording success. OS permissions and private task-owned roots remain necessary; this does not claim defense against a hostile administrator continuously rewriting the SQLite/filesystem state.

## Explicit limits

- This is an isolated fixture lane. Enabling the flag freezes all legacy API mutations, and must not be done on the shared production application. Real browser proof against a task-owned loopback server is separate from production-container release proof.
- `fixture_human` is a server-issued test role. No real identity provider, human-presence challenge, or generative acceptance exists. It must not be relabeled as authenticated person presence.
- Maximum 20 registered stills, bounded CPU/I/O, no inference. ICC/color-managed conversion, transparency, HDR, HEIC/TIFF, animation, video/frame mining, crop/repair/masks and generative transformations are explicitly outside this slice.
- Source roots use device/inode identity for temporary fixtures. Removable-drive UUID binding, production filesystem authorization and physical read-only mounts remain deployment work.
- Known group, byte and normalized-pixel holdout conflicts are checked. There is no learned duplicate/session discovery, calibrated subject inference, or evidence that arbitrary real datasets have independent splits.
- SQLite/OS-lock synchronous operations are durable and replayable. There is no generic ProcessingTask integration, external bridge/outbox, background worker lease fleet, cancellation API, successor-job chain, or automated orphan cleanup. Retry retains the same immutable logical admission and records attempt events. Unfinished staging and artifact temporary files remain task-owned evidence.
- New caption drafts after admission cannot change already admitted bytes. Current policy, grant revocation, explicit rejection of the admitted caption, source identity and late split conflicts are checked before publication. Published versions remain immutable; broad invalidation/retention management is deferred.
- The output manifest pins source module digests, transform parameters, Pillow/codec versions and review provenance. It is a fixture export profile, not an executable Krea 2 trainer preset or a training activation signal. [Trainer geometry validation](trainer-preprocessing.md) is a separate root-owned proof.
