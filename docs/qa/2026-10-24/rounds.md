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

## Round 3

Target `b98f386e7ade216a944a38ccf5527ed52ce0794d`. Backend reviewer ownership rotated for independent checks. Three required findings: source identity must bind before encoding; unready profile candidates must not admit normal API requests; editor fit correction must retain source geometry through Undo/Redo history. These findings have permanent failing-first regressions and bounded ownership.

Editor evidence is qualified: the installed-provider forced persistent-fit/history regression failed, while the ordinary prepatch Chrome rotate/Undo/Flip/save sequence passed at the current viewport. Postfix Chrome added a persistent1000x650viewport change, then Undo/Flip/Save copy; fixture-02_edited-4.jpg is640x480 and the original remains intact. The temporary viewport override was reset. Frontend210/210, hotkeys76/76, image-edit/limit backend13/13, build and source lint passed after the editor-history fix.

Final R3 QA runtime loaded after confirming no active fixture tasks. Chrome Configuration Save exercised its real save/reload sequence; before/after canonical JSON hashes are identical, Save re-enables, and health returns200 at e0f1a2b3c4d5. No configuration field or matching threshold changed.


R3 profile acceptance additionally reproduced and repaired overlapping switches that left runtime and bootstrap on different profiles. Changes are serialized before bootstrap/filesystem mutation; busy config reads return503 and competing writes409. Candidate preparation accepts explicit URL/model/dimensions, including a real768-dimensional fresh migration while active runtime stays unchanged. A newly running task before publication aborts the switch. Same-database migration/vector failure stays degraded; a later admission conflict after successful preparation restores prior readiness. These are bounded interleaving proofs, not a claim of atomic isolation for every already-inflight request or arbitrary admission between the final check and publication.

The first R3 full-suite attempt was intentionally interrupted for the final phase-distinction regression; exit130 is not a pass. The final complete run is logged separately at `/tmp/omoide-qa-round3-backend-final.log`. Final MCP9/9 passed in15.364seconds at `/tmp/omoide-qa-round3-mcp-final.log`.

Final R3 gate: 930/930 backend tests passed in566.513seconds; frontend210/210, hotkeys76/76, realstdio MCP9/9, strict build and canonical source lint passed. Alembic has one head, e0f1a2b3c4d5. The final fixture read probe covered58routes:54HTTP200,2expected401,2expected400,zero500. Protected annotation files and matching thresholds remain unchanged. Source/test fixes are introduced by the round3 commit; its hash is recorded by the subsequent review.

## Round 4

Target `9c7a75983a9495dbcf2c98434675055ebb9db6ac`. Independent API/domain reviewer reports zero required findings after profile14, API domain13, profile pipeline9, health6 and14 cursor-sort traversals. Runtime review reproduced a destination replacement during catalog commit that loses retained recovery data. UI review reproduced a rejected queued fit callback whose coordinate correction is never retried. Both receive failing-first tests and bounded fixes before the final fifth review. The UI race is deterministic installed-provider/queued-reducer evidence; a browser timing reproduction is not claimed.

R4 focused acceptance: destination conflict checks retain both original and encoded recovery after durable commit; committed task/catalog state remains committed and an explicit warning identifies retained recovery. Editor correction now acknowledges actual queued acceptance, retries rejection and avoids repeated history scaling after delayed reads. Frontend213/213, hotkeys76/76, image-edit backend13/13, strict build and source lint passed. Final Chrome rotate→persistent1000×650viewport→Undo→FlipX→Save copy created fixture-02_edited-5.jpg at640×480; original remains640×480; viewport reset. The exact scheduling interleaving remains regression evidence, not a claimed Chrome timing reproduction.

Realstdio MCP9/9 passed in13.843seconds in the adapter environment. Corrected the test module’s documented command to omit `-t .`, which incorrectly requires the non-package tests directory to be importable under Python3.13; the initial application-environment attempt lacked the MCP SDK. Neither invocation error was reported as a product regression or passing run. Sole Alembic head and protected-file check remain unchanged. Final full backend gate passed: **931/931 tests in 435.402 seconds**, logged at `/tmp/omoide-qa-round4-backend-final.log`. The final 58-route probe again returned 54 HTTP200, two expected401, two expected400, and zero500.

Final fixture backend restarted after confirming zero active tasks; health200 at the sole migration head. Chrome replacement-conversion dialog correctly keeps Convert disabled until its permanent-deletion acknowledgment is checked. Cancel returned to the original video; no replacement job was submitted from that check. Replacement mutation/conflict behavior is established by isolated real-file/SQLite regression tests; live keep-original conversion was proved earlier.

## Round 5 (final allowed iteration)

Target `27f376be98a55bff95d389e7b5175e32f1df6ea6`. UI/contracts reviewer reports zero required findings and27focused tests passing. Independent conversion review reproduced one caller-level defect: the legacy worker unconditionally deletes an encoded staging file that publication deliberately retained after a postcommit conflict. Original recovery and committed task/catalog survive, but the logged encoded recovery path is absent. This finding is being repaired within the fifth fixer iteration; no sixth panel round is authorized or claimed.

Final quality/runtime reviewer also reports zero required findings in its assigned independent editor/quality scope: observer/history7/7 passed and the installed provider serializer excludes private revision/history state while retaining geometry. API/domain R5 finding was then fixed in the final fixer iteration: once publication owns encoded cleanup, the legacy caller no longer deletes deliberately retained recovery. Actual-worker tests failed all four postcommit conflict subcases before the fix and pass afterward; source-version3tests cover4postcommit conflicts,8source conflicts and4ordinary-success controls. Lifecycle8, transaction17 and the independent original reproducer pass. No remaining reproduced finding is intentionally waived. This is tested remediation after the fifth panel; no sixth clean-panel review is claimed. Final full backend suite passed: **932/932 tests in 411.756 seconds**, with exit0 and OK in `/tmp/omoide-qa-round5-backend-final.log`. Frontend source is unchanged from the verified round4 build:213frontend,76hotkey,13image-backend tests, strict build and canonical source lint remain applicable; MCP9/9 and the one-head migration check also passed.

Cleanup and production boundary: the owned Chrome QA tab was closed; fixture container omoide-qa-20261024 was stopped and removed only after its active-task count was zero. Synthetic fixture files and logs remain under `/tmp/omoide-qa-20261024` and the named `/tmp/omoide-qa-*` receipts. Production health remains200 at e0f1a2b3c4d5; image sha256:d8e626de3557048a54b2cef4e2a8ab8049a573235ab2d01a707cc6548c8284e4 and start2026-10-25T01:29:13.852597893Z are unchanged. No new production deployment or remote publication was performed for QA. Nonblocking follow-ups are itemized in backlog.md.

## Final disposition

The user-requested five-iteration budget is exhausted. Thirty reproduced findings are fixed: four baseline items and thirteen, seven, three, two and one across rounds1–5. No required finding is left open or waived. The fifth panel identified the final caller defect; the subsequent fix and full-suite acceptance are recorded above. A sixth clean-panel review was not run, so this record does not claim one.

Baseline commit: `0bd3091a8ca8fae5b61d4cfe68f152185dfa0e50`. Final source/test commit: `0f8fa8692ecf547fc7da49260cda3786f3fec6ca`. This final documentation commit records exact fix references and disposition. Coverage includes all27discovered feature families through the specific Chrome and automated evidence in coverage.md, with unavailable integrations explicitly distinguished. Changed-file inventory and nonblocking backlog are adjacent documents.

Publication scope is local commits and local master integration only. The prior upstream sync and original processing repair were published before this QA initiative; these additional QA changes have not been pushed or deployed.
