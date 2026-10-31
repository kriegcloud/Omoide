# Integrated fixture acceptance

The approved fixture still slice is integrated in this worktree on `feat/curation-still-slice`, based on `717761a87e978dcaadc0eef0d7bb986363e5ee52`. The [AI hub handoff](/home/elpresidank/ai/docs/implementation/krea2-curation-still-slice/README.md) records full evidence, baseline comparison, browser proof and remaining phase boundaries. The [contract](contracts.md) is the application behavior reference.

Final checks on 2026-09-16: 716 backend tests passed; frontend build passed; 43 selection tests and 72 hotkey tests passed; 36 curation/dataset frontend tests passed. Alembic has one head, `5f60718293a4`. Lint retains precisely the baseline 50 errors and one warning, with no added findings. Protected annotation/config hashes and `git diff --check` passed. The independent 14-case regression suite is included in the 716 backend total.

An isolated real browser verified original/derivative comparison, explicit decisions, stale revision protection, caption-change invalidation, preserved draft text, and byte-verified frozen export. Read-only fixture materialization and original SHA-256 preservation passed independently. The temporary server and browser are stopped. This is fixture UI/API acceptance, not shared-container deployment, complete startup health, real human presence, or model-quality evidence.

Changes remain uncommitted and unpushed. Generative acceptance is disabled. Production release, private-source processing, inference/training and MCP registration remain outside this implementation phase.
