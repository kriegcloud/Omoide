# Review rounds

## Baseline

Starting commit76a91c7. Canonical backend879/879 passed in377.806seconds. Complete frontend186/186, selection46/46, hotkeys76/76, realstdio MCP9/9, strict frontend build and ESLint zero errors/zero warnings passed. One Alembic head e0f1a2b3c4d5.

Non-blocking historical diagnostics remain visible: Python datetime.utcnow deprecations (including hard-protected annotation code), migration Inspector API deprecations, test-client dependency compatibility warning, and SQLite ResourceWarnings whose allocation ownership is not established by their emission frames. These have no reproduced feature failure in the baseline; warning suppression is not used. They remain maintenance/diagnostic backlog, not claims of warning-free Python execution. Vite reports large optional editor/graph chunks; its advisory is retained and performance is a review item. Cross-platform desktop binaries are not part of this Linux workstation runtime proof.

Baseline remediation: B01–B04 in inventory.md. Main production instance and personal library remain untouched by this QA initiative.

## Round 1

Baseline commit: `0bd3091a8ca8fae5b61d4cfe68f152185dfa0e50`. Three read-only reviewer lanes returned12required findings (5API/domain,3runtime/storage,4UI/export); root added1confirmed README/compose mismatch. All13are locally fixed without waiver. Five API findings also cover nonfinite validation-error serialization. Permanent regressions failed before fixes.

Verification after fixes: canonical backend907/907 in374.629seconds; complete frontend199/199; hotkeys76/76; strict TypeScript/Vite build passed; source ESLint zero findings; MCP realstdio9/9 in12.095seconds; sole Alembic head e0f1a2b3c4d5; whitespace checks clean. Historical diagnostics listed above remain unchanged.

Chrome extension used isolated generated-media fixture18126. The fixture-only backend was restarted after tasks were terminal. Invalid page limits422, malformed cursor400, out-of-range/nonfinite GPS422, foreign profile-face400 were verified against changed runtime. Album confirmation captures exact name and cancels; people Name(Z-A) sort survives detail favorite change/back. Caption correction persists and clears warning; dataset export completes with4items; triage Keep/Undo restores0reviewed. Rename traversal rejected; valid fixture rename persists. Three-second video plays to end and keep-original conversion completes. Task panel reports21indexed,0skipped,1thumbnail failure, with invalid-image reason and exact fixture path. See coverage.md for all27families and honest limitations.

Primary local logs: `/tmp/omoide-qa-round1-backend.log`, `/tmp/omoide-qa-round1-mcp.log`; reviewer/fixer receipts `/tmp/omoide-qa-round1-api.md`, `/tmp/omoide-qa-round1-api-fixer.md`, `/tmp/omoide-qa-round1-runtime.md`, `/tmp/omoide-qa-round1-runtime-fix-report.md`, `/tmp/omoide-qa-round1-ui.md`. Temporary logs are supporting evidence; permanent regressions and this ledger are the durable deliverables.

Round1 fixedCommit fields refer to the commit introducing this section (its hash is recorded in the next review round to avoid self-referential commit IDs).
