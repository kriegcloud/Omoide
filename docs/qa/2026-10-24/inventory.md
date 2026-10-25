# Finding inventory

Baseline items precede reviewer round 1. No blockers are implicitly waived.

### B01: Frontend lint and cache contracts

- `round`: baseline
- `reviewer`: frontend quality
- `label`: issue
- `blockingStatus`: blocking
- `severity`: P2-medium
- `doctrineBucket`: not-doctrine
- `sourceRefs`: AGENTS.md: Verify before merging; invoked quality-review-fix-loop Phase1
- `affectedFiles`: frontend/src/hooks, frontend/src/stores, frontend/src/services, frontend/eslint.config.mjs
- `evidence`: Fresh ESLint: nine errors, two warnings; strict types exposed API timeline entry mismatch.
- `impact`: Prevents reliable feature behavior or baseline verification.
- `suggestedFix`: Typed cache read boundary and service contracts; preserve ref lifecycle; ESM config extension.
- `recommendedSkillOrAgent`: frontend quality
- `fixerGroup`: frontend
- `acceptanceCommands`: ESLint zero findings; strict build;186 frontend tests;76 hotkeys.
- `testsNeeded`: runtime/type
- `dependencies`: none
- `status`: fixed
- `fixedCommit`: baseline commit (see rounds.md)

### B02: Obsolete map search responses repopulate results

- `round`: baseline
- `reviewer`: frontend asynchronous boundary
- `label`: issue
- `blockingStatus`: blocking
- `severity`: P1-high
- `doctrineBucket`: not-doctrine
- `sourceRefs`: AGENTS.md: Verify before merging; invoked quality-review-fix-loop Phase1
- `affectedFiles`: frontend/src/pages/MapEditorPage.tsx; frontend/tests/task-pages.test.cjs
- `evidence`: Repaired test mocks exposed failing shorten-query, ignored-abort and unmount regressions.
- `impact`: Prevents reliable feature behavior or baseline verification.
- `suggestedFix`: Abort obsolete request and gate every callback by generation.
- `recommendedSkillOrAgent`: frontend asynchronous boundary
- `fixerGroup`: frontend
- `acceptanceCommands`: Three failing-before tests now pass; Chrome query shortening clears location results.
- `testsNeeded`: runtime/browser
- `dependencies`: none
- `status`: fixed
- `fixedCommit`: baseline commit (see rounds.md)

### B03: Curation preview byte-bound test depended on random artifact order

- `round`: baseline
- `reviewer`: test quality
- `label`: issue
- `blockingStatus`: blocking
- `severity`: P2-medium
- `doctrineBucket`: not-doctrine
- `sourceRefs`: AGENTS.md: Verify before merging; invoked quality-review-fix-loop Phase1
- `affectedFiles`: tools/curation-mcp/tests/fixture_app.py; tools/curation-mcp/tests/test_adapter_stdio.py
- `evidence`: Rectangle fixture normalized to684bytes below unchanged1024-byte test cap; randomUUID ordering selected it intermittently.
- `impact`: Prevents reliable feature behavior or baseline verification.
- `suggestedFix`: Use deterministic textured fixtures and assert all artifact preconditions and cap rejections.
- `recommendedSkillOrAgent`: test quality
- `fixerGroup`: test harness
- `acceptanceCommands`: Frozen realstdio MCP suite9/9 passes; deterministic strengthened test failed before fixture repair.
- `testsNeeded`: fixture/integration
- `dependencies`: none
- `status`: fixed
- `fixedCommit`: baseline commit (see rounds.md)

### B04: Alembic omitted current path separator option

- `round`: baseline
- `reviewer`: migration configuration
- `label`: issue
- `blockingStatus`: blocking
- `severity`: P2-medium
- `doctrineBucket`: not-doctrine
- `sourceRefs`: AGENTS.md: Verify before merging; invoked quality-review-fix-loop Phase1
- `affectedFiles`: alembic.ini
- `evidence`: Canonical backend run emitted missing path_separator deprecation warning.
- `impact`: Prevents reliable feature behavior or baseline verification.
- `suggestedFix`: Set path_separator=os without changing migration locations.
- `recommendedSkillOrAgent`: migration configuration
- `fixerGroup`: migration config
- `acceptanceCommands`: alembic heads returns sole e0f1a2b3c4d5 without separator warning.
- `testsNeeded`: configuration
- `dependencies`: none
- `status`: fixed
- `fixedCommit`: baseline commit (see rounds.md)
