# Adversarial application QA

- Repository: Omoide; linked checkout `Omoide-ux-qa-20261024`, branch `qa/adversarial-20261024`.
- Starting/base reference: `76a91c7f55c4814d86a286768cccf3e70e376550` (`origin/master` at start). Starting checkout clean; unrelated worktrees are untouched.
- Initiative: exercise every discovered feature family following the upstream v0.7.6 integration and unindexed-media repair, using the Chrome extension for UI interaction, real isolated API/database fixtures, and regression tests for safety boundaries. Review includes existing feature defects, not merely the latest diff.
- Maximum budget: five reviewer/fixer iterations. Baseline preparation precedes round 1. Stop early only after the panel reports zero required findings and the coverage ledger accounts for every discovered feature family. An unexercised integration is a coverage limitation, never an implied pass.
- Zero gate: no unresolved required findings, green applicable commands, committed changes, explicit coverage and residual-risk records. No implicit waivers.
- Publication: local Conventional Commits with the required co-author trailer; no new push or production deployment is included in this QA request.
- Production: read-only observations only. Mutating and destructive scenarios use disposable synthetic fixtures in a separate container on loopback port 18126. No personal media is mounted. Real face model/service access, if used, is read-only and identified in evidence.

## Grounding

The local sources of truth are `AGENTS.md`, `README.md`, `frontend/src/routes.tsx`, the registered API routers and OpenAPI schema, `app/models.py`, configuration schemas, migration graph, `docs/curation/`, `docs/workstation-ux/DESIGN.md`, and executable tests. This is FastAPI/SQLModel/SQLite plus React; the skill's Beep/Effect-specific doctrines and commands do not apply.

Protected annotation files remain read-only. No matching thresholds are changed. Any confirmed blocker that requires a protected-file edit must remain explicitly open for the user; it cannot be silently waived.

## Baseline commands

- `.venv/bin/python -m unittest discover -s tests` using the normal UUID-bearing temporary filesystem.
- `npm run build` and `npx eslint src --ext .ts,.tsx` in `frontend/`.
- `.venv/bin/alembic heads` (one head).
- `node --test-isolation=none --test frontend/tests/selection.test.cjs`.
- Every `frontend/src/hotkeys/tests/*.test.cjs` via Node.
- Additional feature-specific tests and curation MCP tests where their dependencies are available.

## Adapted reviewer panel

Three read-only reviewers cover the skill's roles in parallel, with evidence-based inventories in the required work-order format:

1. API/domain: schema and domain invariants, architecture boundaries, error boundaries, migrations and compatibility.
2. Quality/runtime: quality gates, testing, observability, resource/concurrency safety, evolution and deployment contracts.
3. UI/contracts: frontend behavior and asynchronous boundaries, public API/documentation alignment, reuse and duplication, accessibility and destructive-action safety.

The orchestrator owns Chrome UI execution, fixture provenance, deduplication, blocker classification, bounded fixer ownership, and final acceptance. Fixes require a reproducing test before implementation. Each subsequent round reviews the committed fixes and any remaining uncovered scenarios.
