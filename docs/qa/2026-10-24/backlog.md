# Nonblocking follow-up work

These items are not waivers of required findings. They describe existing diagnostics or bounded polish outside the repaired correctness contracts. External integrations that could not be exercised are recorded separately in coverage.md.

### Backlog: Runtime and test dependency diagnostics

- `source`: Baseline and all full backend runs; rounds.md historical diagnostics.
- `evidence`: datetime.utcnow deprecations, historical Alembic Inspector deprecations, TestClient dependency compatibility warnings, and SQLite ResourceWarnings remain visible. Some datetime call sites are in protected annotation files. ResourceWarning allocation ownership has not been established; the warning alone is not a demonstrated production leak.
- `suggestedFix`: Audit concrete call sites and dependency compatibility in a bounded follow-up; trace SQLite allocations before changing ownership. Respect protected-file rules.
- `acceptanceCriteria`: Applicable focused and full tests pass without the addressed diagnostics, without blanket warning suppression or unauthorized protected edits.
- `priority`: P3-low

### Backlog: Optional frontend bundle size

- `source`: Successful Vite production builds in the baseline and fixer rounds.
- `evidence`: Vite still reports large optional editor/graph chunks. No observed load failure was attributed to the advisory.
- `suggestedFix`: Measure these routes and investigate lazy loading or dependency chunk boundaries where useful.
- `acceptanceCriteria`: Preserve editor/graph behavior and verify an actual loading or bundle-size improvement; do not merely raise the warning threshold.
- `priority`: P3-low

### Backlog: Similar-media message when no embedding exists

- `source`: Chrome edited-copy verification; similarity endpoint response.
- `evidence`: A newly edited copy without an embedding receives HTTP404 No embedding found and displays the generic Failed to load similar media alert. The fixture image-embedding processor was intentionally disabled; editor saving succeeded.
- `suggestedFix`: Distinguish the no-embedding response from network/server failures in the UI.
- `acceptanceCriteria`: Add a response-specific UI regression and clear unavailable-state copy while retaining genuine error reporting and retry behavior.
- `priority`: P3-low

### Backlog: Broader frontend lint command scope

- `source`: UI/contracts review and AGENTS.md canonical source-lint command.
- `evidence`: Canonical npx eslint src --ext .ts,.tsx is clean. The broader npm lint scope applies TypeScript-oriented rules to CommonJS test harness/config files; this run does not claim that broader command is clean.
- `suggestedFix`: Define explicit CommonJS/tooling lint overrides and a documented all-files command in a bounded tooling change.
- `acceptanceCriteria`: Source lint remains strict and clean; the broader command validates test/config code with applicable rules without hiding source findings.
- `priority`: P3-low
