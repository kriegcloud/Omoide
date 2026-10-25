# Review rounds

## Baseline

Starting commit76a91c7. Canonical backend879/879 passed in377.806seconds. Complete frontend186/186, selection46/46, hotkeys 76/76, realstdio MCP9/9, strict frontend build and ESLint zero errors/zero warnings passed. One Alembic head e0f1a2b3c4d5.

Non-blocking historical diagnostics remain visible: Python datetime.utcnow deprecations (including hard-protected annotation code), migration Inspector API deprecations, test-client dependency compatibility warning, and SQLite ResourceWarnings whose allocation ownership is not established by their emission frames. These have no reproduced feature failure in the baseline; warning suppression is not used. They remain maintenance/diagnostic backlog, not claims of warning-free Python execution. Vite reports large optional editor/graph chunks; its advisory is retained and performance is a review item. Cross-platform desktop binaries are not part of this Linux workstation runtime proof.

Baseline remediation: B01–B04 in inventory.md. Main production instance and personal library remain untouched by this QA initiative.

## Round 1

Baseline commit: `0bd3091a8ca8fae5b61d4cfe68f152185dfa0e50`. Three read-only reviewer lanes returned 12 required findings (5API/domain,3runtime/storage,4UI/export); root added1confirmed README/compose mismatch. All 13 are locally fixed without waiver. Five API findings also cover nonfinite validation-error serialization. Permanent regressions failed before fixes.

Verification after fixes: canonical backend 907/907 in374.629seconds; complete frontend 199/199; hotkeys 76/76; strict TypeScript/Vite build passed; source ESLint zero findings; MCP real stdio 9/9 in12.095seconds; sole Alembic head e0f1a2b3c4d5; whitespace checks clean. Historical diagnostics listed above remain unchanged.

Chrome extension used isolated generated-media fixture on port 18126. The fixture-only backend was restarted after tasks were terminal. Invalid page limits422, malformed cursor400, out-of-range/nonfinite GPS422, foreign profile-face400 were verified against changed runtime. Album confirmation captures exact name and cancels; people Name(Z-A) sort survives detail favorite change/back. Caption correction persists and clears warning; dataset export completes with 4 items; triage Keep/Undo restores0reviewed. Rename traversal rejected; valid fixture rename persists. Three-second video plays to end and keep-original conversion completes. Task panel reports21indexed,0skipped,1thumbnail failure, with invalid-image reason and exact fixture path. See coverage.md for all27families and honest limitations.

Primary local logs: `/tmp/omoide-qa-round1-backend.log`, `/tmp/omoide-qa-round1-mcp.log`; reviewer/fixer receipts `/tmp/omoide-qa-round1-api.md`, `/tmp/omoide-qa-round1-api-fixer.md`, `/tmp/omoide-qa-round1-runtime.md`, `/tmp/omoide-qa-round1-runtime-fix-report.md`, `/tmp/omoide-qa-round1-ui.md`. Temporary logs are supporting evidence; permanent regressions and this ledger are the durable deliverables.

Round1 fixedCommit fields refer to the commit introducing this section (its hash is recorded in the next review round to avoid self-referential commit IDs).

## Round 2

Target `879e61f24d12da94843bd4ed17b740790e07fe0c`. Reviewers reproduced 7 required findings:2runtime,1profile activation,4UI. R1API cursor compatibility independently traversed all 8 person sorts,2 media sorts and4 missing-GPS sorts with one-item pages/ties/unnamed values. Required fixes cover concurrent file identity during conversion, late repair cancellation, failed profile migration activation, stale caption/poll lifecycle, cross-dataset export completions, rotation output dimensions, and accessible video preview activation. No waivers.

Additional Chrome proof: generated image uploaded via Chrome file chooser returned 13 similar results; face assignment to Alice followed by Undo restored 2 orphans, with detach provenance; suggestions rendered 2 synthetic matches; geotagger rendered 17 unlocated fixtures; Chicago detail showed 4 items. Rotation640x480 ->480x640 preview then Save copy reproduced the output-dimension mismatch; original file remained640x480. Browser console extension-origin warnings are distinguished from application failures.

The original Process Unindexed Media action was launched through Chrome against the isolated fixture: task `8ad3c0f6-1a41-4ecc-af5f-295431473aa9` completed 20/20 eligible files in 6 seconds, faces_matched=0, without migration gate failure. This is a real bounded processor run, not just a mocked admission test. Production health remained 200 with unchanged image d8e626de3557 and startup 2026-10-25T01:29:13Z.


Round 2 acceptance: canonical backend **922/922** in408.142seconds; complete frontend **206/206**; hotkeys **76/76**; strict build and source ESLint passed; realstdio MCP **9/9** in14.204seconds; sole Alembic head unchanged; whitespace checks clean. QA container restarted only after its tasks were terminal; health200. A58-route GET probe returned54HTTP200,2expected401,2expected400,zero500.

The first rotation patch was rejected by Chrome because viewport-fit drift still produced542x722. The installed dependency harness reproduced stale display-space crop coordinates after fit. Final integration synchronizes unchanged crop coordinates with displayed dimensions and reads rendered output dimensions; the mismatch guard remains. Final hardened build Chrome Save copy succeeds at480x640, opens fixture-02_edited-2.jpg, and original fixture-02.jpg remains640x480. Repeated fits, partial crop plus rotation/resize, and StrictMode callback safety are regression-covered. Temporary geometry-only diagnostics were removed from source and final assets. Accessible Play video button accepts Enter and plays to ended=true at3.018seconds.

All7round2findings are fixed without waiver. Conversion recovery explicitly retains original/encoded copies on identity conflict and documents the portable-filesystem stat/mutation TOCTOU limit. Replacement needs temporary disk capacity for independent recovery copies. Same-database failed migration remains degraded rather than claiming DDL rollback.

Nonblocking UI backlog: a newly edited copy without an embedding shows generic Failed to load similar media because its similarity API correctly returns404 No embedding found. In this fixture the image-embedding processor is deliberately disabled. Improve the unavailable-state copy without hiding genuine network/server errors; acceptance is a no-vector response-specific UI regression. No failed editor save or HTTP500 is implied by that message.

Round2 fixedCommit fields refer to the commit introducing this acceptance; the next review records its hash. Logs: `/tmp/omoide-qa-round2-backend.log`, `/tmp/omoide-qa-round2-mcp.log`; reviewer and fixer receipts use `/tmp/omoide-qa-round2-*`. Source/test changes and this ledger are durable proof.
