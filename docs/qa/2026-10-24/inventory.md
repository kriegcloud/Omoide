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
- `fixedCommit`: 0bd3091a8ca8fae5b61d4cfe68f152185dfa0e50

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
- `fixedCommit`: 0bd3091a8ca8fae5b61d4cfe68f152185dfa0e50

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
- `fixedCommit`: 0bd3091a8ca8fae5b61d4cfe68f152185dfa0e50

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
- `fixedCommit`: 0bd3091a8ca8fae5b61d4cfe68f152185dfa0e50

### R1-API-01: Tag browse endpoints expose media explicitly flagged missing

- `round`:1
- `reviewer`:API/domain architecture, schema and invariants
- `label`:issue
- `blockingStatus`:blocking
- `severity`:P1-high
- `doctrineBucket`:not-doctrine
- `sourceRefs`:AGENTS.md:86-90 requires every browse query to use exclude_missing; app/api/_media_filters.py:11-13 keeps missing records for detail/missing review; tests/test_round6_04_visibility.py:test_tag_search_nested_media_excludes_missing documents the neighboring search contract.
- `affectedFiles`:app/api/tags.py:26-39; app/api/tags.py:117-123; app/schemas/tag.py:7-11.
- `evidence`:Seeded one visible media row and one missing_since-marked row linked to one tag. GET /api/tags/ and GET /api/tags/1 both returned nested media IDs[1,2], including missing ID2. list_tags eager-loads Tag.media without a visibility predicate; get_tag returns the same unfiltered relationship. Both real HTTP assertions reproduce.
- `impact`:Missing records reappear in tag browsing, previews and counts despite the detect/hide/review rule, and the tag API disagrees with tag-search visibility.
- `suggestedFix`:Serialize tag read models through an explicit visible-media query/predicate for both list and detail. Do not filter by mutating ORM relationship collections, which could delete associations. Preserve tag/person metadata and pagination.
- `recommendedSkillOrAgent`:Backend visibility/schema fixer.
- `fixerGroup`:tag-api.
- `acceptanceCommands`:PYTHONPATH=. .venv/bin/python /tmp/omoide-qa-round1-api-probes.py ApiReviewProbes.test_missing_tag_members_are_not_browsable; .venv/bin/python -m unittest discover -s tests -p test_round6_04_visibility.py; full backend suite after permanent regressions are added.
- `testsNeeded`:Permanent list/detail HTTP tests covering mixed visible/missing membership, counts/previews, unchanged association rows and valid pagination.
- `dependencies`:none.
- `status`: fixed
- `fixedCommit`: 879e61f24d12da94843bd4ed17b740790e07fe0c

### R1-API-02: Invalid pagination input escapes as internal server errors

- `round`:1
- `reviewer`:API schemas and error boundaries
- `label`:issue
- `blockingStatus`:blocking
- `severity`:P2-medium
- `doctrineBucket`:not-doctrine
- `sourceRefs`:docs/qa/2026-10-24/plan.md requires public API/schema/error-boundary review; app/pagination.py:58-68 describes invalid/mismatched cursors as client errors; tests/test_round6_safety_validation.py establishes client-error behavior for malformed list input.
- `affectedFiles`:app/api/person/core.py:101-113; app/pagination.py:58-76; app/pagination.py:98-109; other public paginate callers whose limits have no positive bound need the same inventory check.
- `evidence`:GET /api/person/?limit=0 returns500 because limit0 produces no rows and len(rows)==limit then accesses rows[-1]. GET /api/media/?cursor=WyJuZXdlc3QiLCBbMV0sIDFd returns500: this is syntactically valid opaque JSON["newest",[1],1], accepted by decode_cursor although its date key is a list. Confirmed through both the root-owned18126 GET-only fixture and the independent in-memory HTTP suite.
- `impact`:Malformed public input reaches ORM/bind or indexing errors instead of controlled400/422 responses. Negative/unbounded public limits can also bypass intended page-size limits; only the two500 cases are asserted as confirmed here.
- `suggestedFix`:Validate positive bounded limits at public API boundaries and reject cursor values incompatible with the actual sort-key types before building/executing SQL. Keep all valid opaque cursor formats and sort tags unchanged. Do not hide unrelated database failures with a broad exception catch.
- `recommendedSkillOrAgent`:Pagination/API validation fixer.
- `fixerGroup`:pagination-and-list-boundaries.
- `acceptanceCommands`:PYTHONPATH=. .venv/bin/python /tmp/omoide-qa-round1-api-probes.py ApiReviewProbes.test_invalid_list_arguments_are_client_errors; existing upstream API sync and safety validation regressions; full backend suite.
- `testsNeeded`:Permanent HTTP tests for zero/negative/excessive limits and invalid cursor key types/ranges, plus valid datetime/string/numeric sort round trips and tied-page traversal.
- `dependencies`:none.
- `status`: fixed
- `fixedCommit`: 879e61f24d12da94843bd4ed17b740790e07fe0c

### R1-API-03: Profile-face mutations accept a face owned by another person

- `round`:1
- `reviewer`:Domain invariants and API architecture
- `label`:issue
- `blockingStatus`:blocking
- `severity`:P2-medium
- `doctrineBucket`:not-doctrine
- `sourceRefs`:app/utils.py:1000-1009 explicitly repairs a profile face whose person_id differs from the person; app/persons.py:241-245 applies the same ownership invariant; docs/qa/2026-10-24/plan.md includes identity/domain invariants.
- `affectedFiles`:app/api/person/core.py:201-226; app/api/person/extensions.py:596-614; app/schemas/person.py:78-81.
- `evidence`:Created Owner(person1), Target(person2) and a face belonging to Owner. PATCH /api/person/2 with {"profile_face_id":1} and POST /api/person/2/profile_face?face_id=1 both returned200 and persisted Target.profile_face_id=1 while Face.person_id remained Owner. Both real HTTP assertions reproduce; no face assignment was made or inferred.
- `impact`:The public mutations can persist person cards/detail avatars identifying another person, violating the invariant already enforced by aggregate refresh. This is avatar ownership corruption, not a claim that Face.person_id is overwritten.
- `suggestedFix`:Use one shared profile-face validation path for both mutations: allow explicit null to clear, require a non-null face to exist and belong to the target person, and reject otherwise before changing the person. Preserve valid own-face changes.
- `recommendedSkillOrAgent`:Person domain/API fixer.
- `fixerGroup`:person-profile-face.
- `acceptanceCommands`:PYTHONPATH=. .venv/bin/python /tmp/omoide-qa-round1-api-probes.py ApiReviewProbes.test_profile_face_must_belong_to_target_person; person/provenance regression families; full backend suite.
- `testsNeeded`:Permanent HTTP tests for own, foreign, orphan, nonexistent and null face IDs across both mutation routes, including unchanged state on rejection.
- `dependencies`:none.
- `status`: fixed
- `fixedCommit`: 879e61f24d12da94843bd4ed17b740790e07fe0c

### R1-API-04: Repeating a person-tag attachment returns an uncaught database error

- `round`:1
- `reviewer`:API error boundaries and relationship invariants
- `label`:issue
- `blockingStatus`:blocking
- `severity`:P2-medium
- `doctrineBucket`:not-doctrine
- `sourceRefs`:app/api/tags.py:88-109 explicitly avoids duplicate media-tag membership; app/models.py:167-173 defines unique person/tag composite identity; docs/qa/2026-10-24/plan.md includes tag CRUD and error boundaries.
- `affectedFiles`:app/api/tags.py:157-168.
- `evidence`:POST /api/tags/person/1/1 returns204 once and500 when repeated with the same IDs. Direct real-session reproduction raises IntegrityError from the duplicate PersonTagLink insert. HTTP fixture confirms the second request is the failing one.
- `impact`:A retry/double submission of an already-present association becomes an internal error rather than an idempotent add or an explicit domain conflict. This differs from the corresponding media-tag route.
- `suggestedFix`:Make attachment idempotent with a SQLite conflict-safe insert or equivalent existing-link handling that remains correct under concurrent retries. Preserve404 for missing person/tag and one relationship row.
- `recommendedSkillOrAgent`:Tag relationship/API fixer.
- `fixerGroup`:tag-api.
- `acceptanceCommands`:PYTHONPATH=. .venv/bin/python /tmp/omoide-qa-round1-api-probes.py ApiReviewProbes.test_duplicate_person_tag_is_idempotent; tag mutation/visibility tests; full backend suite.
- `testsNeeded`:Permanent repeat/concurrent attachment test, membership count1, missing IDs404 and detach/re-attach coverage.
- `dependencies`:Coordinate ownership with R1-API-01 because both edit app/api/tags.py.
- `status`: fixed
- `fixedCommit`: 879e61f24d12da94843bd4ed17b740790e07fe0c

### R1-API-05: Geolocation mutation accepts impossible latitude and longitude

- `round`:1
- `reviewer`:Schema/domain validation
- `label`:issue
- `blockingStatus`:blocking
- `severity`:P2-medium
- `doctrineBucket`:not-doctrine
- `sourceRefs`:app/schemas/media.py:153-155 defines geographic latitude/longitude; app/api/media/core.py:205-213 persists these coordinates, sets the manual-location flag and can pass them to source metadata writing; docs/qa/2026-10-24/plan.md includes schema invariants/map review.
- `affectedFiles`:app/schemas/media.py:153-155; app/api/media/core.py:181-213.
- `evidence`:PATCH /api/media/1/geolocation with {"latitude":999,"longitude":999} returns200 in the real HTTP fixture with file-writing disabled. GeoUpdate accepts both unrestricted floats, and the handler assigns them to ExifData. No source file was read or modified by the reproduction.
- `impact`:Invalid geographic input can overwrite valid catalog locations, invalidate the previous place and mark the result manually set; in a writable deployment it also reaches the metadata-write path.
- `suggestedFix`:Reject nonfinite or out-of-range coordinates at the schema boundary (latitude[-90,90], longitude[-180,180]) before querying/writing. Preserve edge values, zero, negative coordinates and the existing catalog-only/read-only source behavior.
- `recommendedSkillOrAgent`:Media geolocation schema fixer.
- `fixerGroup`:media-coordinate-validation.
- `acceptanceCommands`:PYTHONPATH=. .venv/bin/python /tmp/omoide-qa-round1-api-probes.py ApiReviewProbes.test_invalid_geographic_coordinates_are_rejected; test_upstream_api_sync_contracts.py; full backend suite.
- `testsNeeded`:Permanent HTTP tests for each range boundary, over/underflow and nonfinite input, no DB/file writer call on rejection, unchanged prior location/place and valid catalog-only edits.
- `dependencies`:none.
- `status`: fixed
- `fixedCommit`: 879e61f24d12da94843bd4ed17b740790e07fe0c


### R1-RUNTIME-01: Dataset workers revive cancelled or interrupted tasks

- `round`: 1
- `reviewer`: Quality/runtime — resource/concurrency and task lifecycle
- `label`: issue
- `blockingStatus`: blocking
- `severity`: P1-high
- `doctrineBucket`: not-doctrine
- `sourceRefs`: AGENTS.md Tasks (explicit cancellation does not auto-resume); app/tasks/common.py:44-79 (canonical guarded start/finish contracts); docs/qa/2026-10-24/plan.md Zero gate and existing-feature scope
- `affectedFiles`: app/tasks/dataset_export.py:26-31,37-47; app/tasks/dataset_caption_generation.py:69,83,152-153; app/tasks/dataset_frame_mining.py:131,140,203-204
- `evidence`: Real SQLite cancelled ProcessingTask + cancelled DatasetExport passed to export_dataset calls mocked build_export once and ends both rows completed. Interrupted caption task with empty dataset finishes completed. Cancelled frame-mining task with empty video list finishes completed. Reproducer prints `CANCELLED_EXPORT build_calls=1 task=completed export=completed`, `INTERRUPTED_CAPTIONS task=completed`, `CANCELLED_FRAME_MINING task=completed`. All three workers unconditionally assign RUNNING; loops/terminal writes check only CANCELLED and bypass _start_task/_finish_task.
- `impact`: Cancellation in the admitted-to-background-worker gap is ignored. With actual items the revived job can write export/frame bytes or invoke annotation work after cancellation; interrupted predecessors can overwrite shutdown state and finish as completed while a resumed successor is admitted.
- `suggestedFix`: Use conditional task admission before any export/frame/caption side effects, honor interrupted/failed terminal states during work, and conditional finalization so cancellation/shutdown wins. Mirror DatasetExport status without resetting a cancelled terminal row; preserve already-generated work and resume inputs. Keep changes outside protected annotation implementation.
- `recommendedSkillOrAgent`: task lifecycle fixer
- `fixerGroup`: app/tasks/dataset_export.py, dataset_caption_generation.py, dataset_frame_mining.py and focused dataset/task tests
- `acceptanceCommands`: Add failing admission and mid-worker interruption tests, then run `.venv/bin/python -m unittest discover -s tests -p 'test_round6_safety_exports.py'`, corresponding caption/frame-mining tests, test_task_resume.py, and full canonical backend suite on UUID-bearing tempfile filesystem.
- `testsNeeded`: Runtime regression for cancelled/interrupted/failed worker admission, no export/annotation/mining call when rejected, mid-work terminal-state preservation, and pending/running success/resume cases.
- `dependencies`: none
- `status`: fixed
- `fixedCommit`: 879e61f24d12da94843bd4ed17b740790e07fe0c

### R1-RUNTIME-02: Replacement conversion deletes the original before durable catalog commit

- `round`: 1
- `reviewer`: Quality/runtime — transactional filesystem and recovery safety
- `label`: issue
- `blockingStatus`: blocking
- `severity`: P1-high
- `doctrineBucket`: not-doctrine
- `sourceRefs`: AGENTS.md Audit-driven fixes and media safety; README.md warning to fully validate originals; app/tasks/export.py:90-106 demonstrates repository filesystem compensation on database commit failure; docs/qa/2026-10-24/plan.md destructive synthetic fixtures
- `affectedFiles`: app/conversion.py:222-242; app/tasks/conversion.py:374-387
- `evidence`: In a real SQLite catalog containing synthetic original.avi, mock encoding writes a converted staging file; injecting session.commit RuntimeError into convert_media(..., 'replace_original') then rolling back leaves `original_exists=False`, `converted_exists=True`, and `catalog_source_exists=False`. The original bytes were deleted before commit; catalog retains the old .avi path. The separate single-item worker has the same ordering: replace destination, unlink original at381, then _finish_task database commit at387. The existing recovery_path logic only protects failures before destination publication, not a commit failure afterward.
- `impact`: A database lock/disk/commit failure can remove the original and leave its catalog path dangling after a failed conversion. For same-path MP4 replacement the original bytes are overwritten before a failed commit as well. Conversion output survives in the extension-change case, but the original is irretrievable and the UI/catalog no longer resolves the new copy.
- `suggestedFix`: Preserve the original/recovery backup until catalog/task publication is durable, provide compensating restoration or an explicit recoverable state on commit failure, and apply the same contract to batch/shared and single-item replacement. Do not discard recovery output on rollback. Preserve existing final task CAS and cancellation safety.
- `recommendedSkillOrAgent`: media conversion/storage safety fixer
- `fixerGroup`: app/conversion.py, app/tasks/conversion.py and conversion lifecycle/path tests
- `acceptanceCommands`: Add failing commit-fault tests for extension-changing AVI-to-MP4 and same-path MP4 for both worker paths; run test_round6_safety_conversion_lifecycle.py, test_round6_safety_paths.py, new shared-conversion tests and full canonical backend suite.
- `testsNeeded`: Runtime fault injection at final catalog/task commit, original/recovery byte verification, catalog/filesystem agreement after rollback, no unrelated file overwrite, and existing cancellation/final-claim tests.
- `dependencies`: none
- `status`: fixed
- `fixedCommit`: 879e61f24d12da94843bd4ed17b740790e07fe0c

### R1-RUNTIME-03: Training launch request is visible before its database transaction commits

- `round`: 1
- `reviewer`: Quality/runtime — deployment/host execution admission
- `label`: issue
- `blockingStatus`: blocking
- `severity`: P1-high
- `doctrineBucket`: not-doctrine
- `sourceRefs`: packaging/README-training.md host launcher contract; packaging/omoide-train-launcher:80-85,238 (consumes REQUESTED independently of database); app/services/training_runs.py:create_run; docs/qa/2026-10-24/plan.md training feature scope
- `affectedFiles`: app/services/training_runs.py:148-158
- `evidence`: create_run writes config and REQUESTED at154-156, then commits TrainingRun at157. With real SQLite rows and synthetic run directory, inject commit failure and rollback: reproducer prints `TRAINING_COMMIT_FAULT rows=0 launchable_REQUESTED=1`. The host systemd/path/timer launcher scans and atomically moves any matching REQUESTED to STARTED, then launches ai-toolkit without checking whether TrainingRun committed. Even a successful but slow commit exposes the request prematurely.
- `impact`: Host training can begin after a failed API transaction with no durable run row available for UI monitoring/cancellation. This can consume significant compute and leave an untracked run; half-published request files also lack atomic visibility.
- `suggestedFix`: Make durable run admission precede publication of the launchable marker; publish marker atomically after commit, with explicit recoverable failure handling if publication fails. Do not leave REQUESTED on rollback and do not allow launcher visibility before the committed run exists.
- `recommendedSkillOrAgent`: training launcher/admission fixer
- `fixerGroup`: app/services/training_runs.py plus test_training_runs.py; launcher only if a shared handshake is necessary
- `acceptanceCommands`: Add failing commit-fault and marker-publication-fault tests and a watcher/hook assertion that launchable REQUESTED appears only after durable TrainingRun; run `.venv/bin/python -m unittest discover -s tests -p test_training_runs.py`, training preset tests and full canonical backend suite.
- `testsNeeded`: Runtime isolated filesystem/SQLite tests for commit rollback without launch marker, atomic marker publication, postcommit marker-write failure recovery, and normal launch request contents. No actual training process needed.
- `dependencies`: none
- `status`: fixed
- `fixedCommit`: 879e61f24d12da94843bd4ed17b740790e07fe0c


### UI-R1-001: Stale album/event detail responses can cross route identities and misdirect destructive actions

- `round`: 1
- `reviewer`: UI/contracts
- `label`: issue
- `blockingStatus`: blocking
- `severity`: P1-high
- `doctrineBucket`: target-doctrine-violation
- `sourceRefs`: `AGENTS.md`, Hotkeys convention (destructive confirmation target snapshotted at open); `docs/qa/2026-10-24/plan.md`, initiative and review scope (asynchronous boundaries and destructive scenarios); existing route isolation standard exercised by `frontend/tests/detailRaces.test.cjs` for media/person details
- `affectedFiles`: `frontend/src/pages/AlbumDetailPage.tsx:53`, `frontend/src/pages/AlbumDetailPage.tsx:73`, `frontend/src/pages/AlbumDetailPage.tsx:85`, `frontend/src/pages/EventDetailPage.tsx:43`, `frontend/src/pages/EventDetailPage.tsx:77`, `frontend/src/pages/EventDetailPage.tsx:99`
- `evidence`: Both GET effects commit their responses without abort/current-route guards and retain the previous entity on route changes. The actual-source temporary harness navigates album 1 -> 2, resolves album 2 then the old album 1 request, opens the delete dialog and confirms: `ALBUM_RACE {"route":"2","displayed":"album-A","deleted":[2]}`. The equivalent event reproduction gives `EVENT_RACE {"route":"2","displayed":"event-A","grid":"event-2","converted":[1]}`. The event conversion removes event 1 despite the current page/grid belonging to event 2. Evidence: `/tmp/omoide-round1-ui-repro.cjs`, `/tmp/omoide-round1-ui-repro.log`, `/tmp/omoide-round1-ui-route-repro.cjs`, `/tmp/omoide-round1-ui-route-repro.log`.
- `impact`: Users can see one album/event while controls operate on another; album deletion or event-to-album conversion can mutate the wrong entity. Ordinary out-of-order GET completion is sufficient; this does not depend on production data or a malicious server.
- `suggestedFix`: Apply the established route identity/generation guard to initial and refresh requests; clear stale entity/error/dialog state when identity changes; render mutation controls only for the currently loaded route entity. Snapshot confirmation identity at open and use the shared ConfirmDialog contract for destructive delete/convert flows. Reject or close stale confirmations rather than retargeting them to a new route.
- `recommendedSkillOrAgent`: frontend asynchronous-boundary and destructive-action fixer
- `fixerGroup`: frontend-album-event-route-safety
- `acceptanceCommands`: Add deferred-response and open-confirmation/route-change regressions to `frontend/tests/detailRaces.test.cjs`; run `node --test-isolation=none --test frontend/tests/detailRaces.test.cjs`; `npm --prefix frontend run build`; orchestrator browser proof of album/event route changes with delayed reads and synthetic destructive targets
- `testsNeeded`: Out-of-order album/event GETs, stale GET failures, request completion after unmount, rename completion after navigation, confirmation opened before route change, and exact mutation target assertions
- `dependencies`: none
- `status`: fixed
- `fixedCommit`: 879e61f24d12da94843bd4ed17b740790e07fe0c

### UI-R1-002: Person deletion confirmation silently retargets after navigation

- `round`: 1
- `reviewer`: UI/contracts
- `label`: issue
- `blockingStatus`: blocking
- `severity`: P1-high
- `doctrineBucket`: target-doctrine-violation
- `sourceRefs`: `AGENTS.md`, Hotkeys convention: destructive target id snapshotted at open; `docs/qa/2026-10-24/plan.md`, destructive-action safety; existing media-viewer target capture regression in `frontend/src/hotkeys/tests/viewer-regression.test.cjs`
- `affectedFiles`: `frontend/src/hooks/usePersonDetailPage.ts:157`, `frontend/src/hooks/usePersonDetailPage.ts:738`, `frontend/src/pages/PersonDetailPage.tsx:211`, `frontend/src/pages/PersonDetailPage.tsx:281`
- `evidence`: `confirmDelete` is only a boolean; the route-reset effect does not close it, while `handleDeletePerson` dereferences the latest route `id`. Actual-hook reproduction opens confirmation for person 1, changes the route to person 2, lets that detail load and invokes the current confirmation handler: `PERSON_CONFIRM_RETARGET {"dialogOpenedFor":1,"route":"2","stillOpenBeforeConfirm":true,"deleted":[2]}`. Evidence: `/tmp/omoide-round1-ui-route-repro.cjs` and `/tmp/omoide-round1-ui-route-repro.log`.
- `impact`: A confirmation initiated for one person can delete a different person after browser/history/programmatic navigation. The existing shared dialog guards repeated Enter presses but cannot supply the missing target snapshot.
- `suggestedFix`: Store the immutable person id/name when opening the deletion confirmation. Close it when route identity changes or confirm only its original target with explicit identity copy; protect completion handlers from navigating or changing messages on a subsequently opened person. Preserve existing API provenance/audit behavior.
- `recommendedSkillOrAgent`: frontend person-action safety fixer
- `fixerGroup`: frontend-person-route-safety
- `acceptanceCommands`: Add person delete target-capture regression to `frontend/tests/detailRaces.test.cjs` or `frontend/src/hotkeys/tests/viewer-regression.test.cjs`; run the relevant Node file and all hotkey tests; `npm --prefix frontend run build`
- `testsNeeded`: Open delete for person A, navigate to B, complete B's load, confirm; assert B is never deleted by A's dialog. Cover failed deletion and response completion after subsequent navigation.
- `dependencies`: none
- `status`: fixed
- `fixedCommit`: 879e61f24d12da94843bd4ed17b740790e07fe0c

### UI-R1-003: Person detail cache patches override the selected upstream people sort

- `round`: 1
- `reviewer`: UI/contracts
- `label`: issue
- `blockingStatus`: blocking
- `severity`: P2-medium
- `doctrineBucket`: not-doctrine
- `sourceRefs`: `docs/qa/2026-10-24/plan.md`, user-facing feature correctness and upstream integration scope; `frontend/src/components/PeopleGrid.tsx:74` (eight offered sort choices); `app/api/person/core.py:66` (server sort contract)
- `affectedFiles`: `frontend/src/stores/peopleCache.ts:19`, `frontend/src/stores/peopleCache.ts:44`, `frontend/src/stores/peopleCache.ts:65`, `frontend/src/hooks/usePersonDetailPage.ts:180`, `frontend/src/components/PeopleGrid.tsx:83`
- `evidence`: Cache keys now encode selected sort, but `patchPersonInGrids` and `resortPeopleGrid` always apply `appearance_count desc, id desc` to every people grid. Actual-source reproduction initializes `people-grid-all-name_asc-` with Amy then Zoe, patches Amy's detail, and prints `PEOPLE_SORT {"requested":"name_asc","names":["Zoe","Amy"]}`. The detail hook invokes this patch on normal detail loading, not only on a mutation, and cached lists are reused when returning to the people page. Evidence: `/tmp/omoide-round1-ui-repro.cjs` and `/tmp/omoide-round1-ui-repro.log`.
- `impact`: Returning from a person detail can silently reorder the grid contrary to Name A–Z/Fewest media/Favorites/newest/oldest choices. The pagination cursor was issued for the original server order, so mixing reordered loaded rows with later pages further breaks stable list behavior.
- `suggestedFix`: Keep non-default sorted/filtered grids under server-owned ordering: invalidate/refetch the affected variants or store explicit sort/filter metadata and apply their actual comparator/membership rules. Do not infer sort by an ambiguous raw key suffix or reorder all variants with the default comparator. Preserve the fork's local patch behavior where its ordering invariant is valid.
- `recommendedSkillOrAgent`: frontend cache reconciliation fixer
- `fixerGroup`: frontend-people-cache-contract
- `acceptanceCommands`: Add exact-key cache regressions to `frontend/tests/mutations.test.cjs` or a focused people-cache Node test; run `node --test-isolation=none --test frontend/tests/*.test.cjs`; `npm --prefix frontend run build`
- `testsNeeded`: At minimum name ascending and appearances ascending retain server order or invalidate cleanly after a detail patch; cover favorites and name-filter membership and appending a cursor page after the patch
- `dependencies`: none
- `status`: fixed
- `fixedCommit`: 879e61f24d12da94843bd4ed17b740790e07fe0c

### UI-R1-004: Person export reports capped failure samples as the total and can announce false success

- `round`: 1
- `reviewer`: UI/contracts
- `label`: issue
- `blockingStatus`: blocking
- `severity`: P1-high
- `doctrineBucket`: not-doctrine
- `sourceRefs`: `docs/qa/2026-10-24/plan.md`, public API/frontend contract correctness; established Phase 1 task-issue sample-vs-total boundary in `frontend/src/components/TasksPanel.tsx:85`; `app/tasks/state.py:53` (bounded retained examples independently of totals)
- `affectedFiles`: `frontend/src/pages/PersonDetailPage.tsx:131`, `app/tasks/export.py:151`, `app/api/tasks.py:56`
- `evidence`: Export increments `processed` for every attempted media, including failures. PersonDetail instead calculates `skipped = (await getTaskFailures(id)).length` and `exported = processed - skipped`. `/failures` contains at most 500 recent examples. Actual-page completion reproduction supplies processed 1,000, failure_count 1,000 and 500 samples and captures the snackbar: `EXPORT_COUNT {"open":true,"message":"Exported 500 media files; skipped 500.","severity":"success"}`. All 1,000 files failed in that scenario. Evidence: `/tmp/omoide-round1-ui-repro.cjs` and `/tmp/omoide-round1-ui-repro.log`. Export worker also does not currently persist an exact export result, so counts may become unavailable after failure-cache eviction/restart.
- `impact`: The user receives a success message claiming copied/moved files that were never exported. After cache loss the same logic can claim every attempted item exported successfully. This directly affects confidence in export completion and recovery decisions.
- `suggestedFix`: Base exported/skipped totals on authoritative task result/counters, never the samples array length. Persist exact export outcome counts in the worker if needed. For historical tasks with no trustworthy total, report completion with unknown outcomes rather than inventing success. Keep sample entries as bounded details only.
- `recommendedSkillOrAgent`: task API/export accounting fixer plus frontend completion-feedback fixer
- `fixerGroup`: person-export-accounting
- `acceptanceCommands`: Add a person-export completion regression with more than 500 failed items to a focused frontend Node test and a backend export accounting/restart test; run `node --test-isolation=none --test frontend/tests/*.test.cjs`; run the focused Python person-export test module; `npm --prefix frontend run build`
- `testsNeeded`: More than 500 failures, zero successful exports, mixed success/failure, empty or evicted samples, persisted outcomes after restart, and historical unknown totals; success severity only when supported by actual results
- `dependencies`: none
- `status`: fixed
- `fixedCommit`: 879e61f24d12da94843bd4ed17b740790e07fe0c


### R1-DOC-01: Workstation media permissions are misrepresented

- `round`: 1
- `reviewer`: root documentation and deployment safety
- `label`: issue
- `blockingStatus`: blocking
- `severity`: P1-high
- `doctrineBucket`: not-doctrine
- `sourceRefs`: docker-compose.workstation.yml environment and media volumes
- `affectedFiles`: README.md personal workstation profile
- `evidence`: README claimed all source media was read-only and only data persisted writes; compose explicitly sets media read_only false and DOCKER_MEDIA_READ_ONLY false.
- `impact`: Operators could rely on nonexistent source-file immutability during destructive actions.
- `suggestedFix`: Document actual writable mount, disabled backend guard, manual mutation scope and requirements for read-only deployment.
- `recommendedSkillOrAgent`: documentation reviewer
- `fixerGroup`: README
- `acceptanceCommands`: Compare README with compose mount and environment declarations; git diff --check.
- `testsNeeded`: none; direct configuration consistency check
- `dependencies`: none
- `status`: fixed
- `fixedCommit`: 879e61f24d12da94843bd4ed17b740790e07fe0c

### R2-API-01: Failed target-profile migration is advertised as successful activation

- `round`:2
- `reviewer`:API/domain architecture, error boundaries and migration compatibility
- `label`:issue
- `blockingStatus`:blocking
- `severity`:P1-high
- `doctrineBucket`:not-doctrine
- `sourceRefs`:app/database.py:233-239 explicitly requires callers to treat Alembic failures as degraded schema and never advertise migration success; docs/qa/2026-10-24/plan.md requires profile/migration/error-boundary correctness; tests/test_round6_pipeline_profile.py:93-104 establishes restoration of prior bootstrap identity on rejected profile activation.
- `affectedFiles`:app/config/__init__.py:306-401, especially327-374; app/api/config.py:352-366 and equivalent create_profile activation path.
- `evidence`:A temporary isolated regression invokes the real switch_profile→reload_settings path with a new task-owned profile directory and real CLIP prevalidation, injecting RuntimeError only at database.run_migrations while spying reset_engine. switch_profile raises no exception (the route therefore reports204), runtime data_dir changes to the target, bootstrap is written only once with no restoration, vector/schema completion is never reached, and configure_auto_scan_job is called. reload_settings catches the migration exception and then logs Settings reloaded successfully. Probe:/tmp/omoide-qa-round2-profile-probe.py; failing log:/tmp/omoide-qa-round2-profile-probe.log. The process used isolated configuration and mocked engine reset/scheduler; no live profile, engine or database was changed.
- `impact`:A profile whose required migrations failed becomes the advertised active profile and can drive subsequent requests/scheduling against unavailable schema. The previous identity is not restored. Existing health checks may correctly return503 after a real migration failure; this finding does not claim that health incorrectly returns200 or that data loss occurred.
- `suggestedFix`:Make activation failure explicit and keep or restore the prior settings, bootstrap and runtime engine identity when target migration/schema preparation fails. Do not schedule or advertise target activation before schema readiness. Preserve ordinary same-profile reload contracts and successful activation behavior; avoid adding a broad success fallback.
- `recommendedSkillOrAgent`:Configuration/profile migration-boundary fixer.
- `fixerGroup`:profile-activation.
- `acceptanceCommands`:PYTHONPATH=. .venv/bin/python /tmp/omoide-qa-round2-profile-probe.py; permanent regression family for switch/create/reload migration failure and restoration; .venv/bin/python -m unittest discover -s tests -p test_round6_pipeline_profile.py; migration health/lifecycle suites; full backend suite.
- `testsNeeded`:Failing-first permanent tests for target migration and vector/schema failures, prior settings/bootstrap/engine retention, skipped target scheduling/model release on failure, unchanged successful switch/create/reload behavior and controlled API failure responses.
- `dependencies`:none.
- `status`: fixed
- `fixedCommit`: b98f386e7ade216a944a38ccf5527ed52ce0794d

One required finding. Protected annotation mutation guards and curation fixture/production legacy-authority guards were inspected read-only; no additional reproduced blocker is claimed there. Genuine passkey/inference/training and production profile activation remain outside this review's execution proof. Source coverage/history and green907backend/199frontend/76hotkey/9MCP gates are accepted from the orchestrator; this review does not reclassify coverage limitations as passes.


### R2-RUNTIME-01: Conversion recovery and cleanup can overwrite/delete a newer source inode

- `round`: 2
- `reviewer`: Quality/runtime — storage identity and compensation
- `label`: issue
- `blockingStatus`: blocking
- `severity`: P1-high
- `doctrineBucket`: not-doctrine
- `sourceRefs`: docs/qa/2026-10-24/plan.md destructive synthetic safety; R1-RUNTIME-02 compensation contract; AGENTS.md media safety and failing-test-first rule
- `affectedFiles`: app/conversion.py:publish_replacement, especially backup.replace(source) rollback and postcommit obsolete.unlink loop
- `evidence`: Two deterministic filesystem interleavings in real SQLite fixture: commit callback commits an AVI-to-MP4 row, then atomically replaces original AVI with a newer unrelated file; postcommit cleanup deletes that newer file (`concurrent_source_exists=False`). For same-path MP4, commit callback atomically replaces newly published MP4 with newer edited bytes then raises commit fault; compensation unconditionally backup.replace(source) overwrites that newer edit (`concurrent_edit_preserved=False`). Cross-destination rollback already checks inode identity, but source cleanup and same-path restore do not.
- `impact`: Another import/editor or concurrent file operation can lose newer bytes during conversion cleanup or rollback. Ordinary commit-fault tests pass because they do not replace filesystem identity during the failure window.
- `suggestedFix`: Bind source and published-file identities, verify ownership before every destructive cleanup/compensation, and leave foreign/newer source bytes untouched on mismatch. Retain original backup/encoded recovery where available and report an explicit identity conflict rather than overwriting/deleting another inode.
- `recommendedSkillOrAgent`: conversion storage safety fixer
- `fixerGroup`: app/conversion.py and focused transaction safety tests
- `acceptanceCommands`: Add failing tests for both exact concurrent replacement interleavings, then test_runtime_transaction_safety.py, safety_conversion_lifecycle.py, safety_paths.py and full canonical backend suite.
- `testsNeeded`: Runtime atomic source replacement after commit and before rollback, unchanged-source successful cleanup, original/encoded recovery retention, and no foreign-inode unlink/replace.
- `dependencies`: none
- `status`: fixed
- `fixedCommit`: b98f386e7ade216a944a38ccf5527ed52ce0794d

### R2-RUNTIME-02: Repair completion overwrites cancellation accepted during thumbnail finalization

- `round`: 2
- `reviewer`: Quality/runtime — repair terminal-state concurrency
- `label`: issue
- `blockingStatus`: blocking
- `severity`: P1-high
- `doctrineBucket`: not-doctrine
- `sourceRefs`: app/api/repairs.py:cancel_repair cancellation contract; AGENTS.md task cancellation and terminal-state precedence; docs/qa/2026-10-24/coverage.md Repairs/output lineage and tasks families
- `affectedFiles`: app/tasks/image_repair.py:149-173 and follow-up dispatch
- `evidence`: Existing FakeRepairClient produces synthetic JPEG. During mocked generate_thumbnail, a separate Session commits ImageRepairJob.status=cancelled after the generated Media row was registered. Worker then unconditionally writes succeeded and schedules follow-up processing. Reproducer prints `REPAIR_CANCEL final_status=succeeded followup_calls=1`. No actual Comfy operation ran.
- `impact`: A successful cancellation is lost during output finalization, cancelled work becomes succeeded, and additional processing runs. The earlier job.status check before first Media commit does not protect the later terminal transition.
- `suggestedFix`: Use a guarded running-to-succeeded CAS for final repair publication and dispatch follow-up only if that claim succeeds. Preserve cancelled state and clean or explicitly account for task-owned provisional output/media/thumbnail if cancellation wins; never delete or modify source media.
- `recommendedSkillOrAgent`: repair task concurrency fixer
- `fixerGroup`: app/tasks/image_repair.py and focused repair/transaction tests; protected annotation code remains untouched
- `acceptanceCommands`: Add failing cancellation-during-thumbnail regression using FakeRepairClient, then test_image_repairs.py, test_round6_pipeline_repairs.py, transaction safety tests and full canonical backend suite.
- `testsNeeded`: Runtime accepted cancellation during metadata/thumbnail finalization, no follow-up when CAS loses, original bytes unchanged, no dangling provisional output rows, and normal success/error behavior.
- `dependencies`: none
- `status`: fixed
- `fixedCommit`: b98f386e7ade216a944a38ccf5527ed52ce0794d

No additional confirmed training admission, durable counter/feed, curation lease/authority or streaming failure was reproduced in this bounded round. External codec/training/Comfy/passkey/platform limits remain as recorded in coverage.md; they are not implied passes.


### UI-R2-001: Caption filter responses and regeneration polling outlive their owning view

- `round`: 2
- `reviewer`: UI/contracts
- `label`: issue
- `blockingStatus`: blocking
- `severity`: P1-high
- `doctrineBucket`: not-doctrine
- `sourceRefs`: `docs/qa/2026-10-24/plan.md` asynchronous boundaries; `docs/qa/2026-10-24/coverage.md` captions/export/history; existing route/filter generation contracts in DatasetDetailPage and DatasetTriagePage
- `affectedFiles`: `frontend/src/components/DatasetCaptionsPanel.tsx:74`, `frontend/src/components/DatasetCaptionsPanel.tsx:97`, `frontend/src/components/DatasetCaptionsPanel.tsx:149`, `frontend/src/components/DatasetCaptionsPanel.tsx:180`
- `evidence`: Actual component harness changes All -> Approved, resolves Approved first then the old All request: `CAPTION_FILTER_RACE {"filter":"approved","caption":"old all response"}`. A second reproduction starts Regenerate, unmounts, then releases its delay; it still invokes the annotations API: `CAPTION_POLL_UNMOUNT ["after-unmount"]`. The load/save/approve/regenerate/pagination/task-poll callbacks have no view generation or mounted guard; setInterval can also overlap polls. `/tmp/omoide-r2-ui-repro.cjs` and `/tmp/omoide-r2-ui-repro.log`.
- `impact`: Obsolete responses replace the current filter's rows and draft captions. Regeneration continues polling after leaving the view, and obsolete completion can reload an old filter or write state after unmount. Existing successful caption persistence does not cover these delayed-response cases.
- `suggestedFix`: Scope reads, mutations and polling to dataset/filter/task generation; abort reads where supported and independently reject ignored-abort responses. Reset dataset-owned state on identity changes. Serialize task polling using the existing visible polling pattern. Stop client regeneration polling on view invalidation without cancelling already-started server work. Show pagination errors and retain retryable loaded rows.
- `recommendedSkillOrAgent`: frontend caption asynchronous-lifecycle fixer
- `fixerGroup`: frontend-caption-lifecycle
- `acceptanceCommands`: Add permanent ignored-abort filter race, delayed mutation completion, task/regeneration unmount and pagination failure tests; `node --test-isolation=none --test frontend/tests/*.test.cjs`; `npm --prefix frontend run build`; source ESLint
- `testsNeeded`: Out-of-order filters, dataset identity changes, concurrent polling, stale errors, pagination failure, and no API polling/state writes after unmount
- `dependencies`: none
- `status`: fixed
- `fixedCommit`: b98f386e7ade216a944a38ccf5527ed52ce0794d

### UI-R2-002: Dataset export creation completion contaminates the next dataset's history

- `round`: 2
- `reviewer`: UI/contracts
- `label`: issue
- `blockingStatus`: blocking
- `severity`: P1-high
- `doctrineBucket`: not-doctrine
- `sourceRefs`: `docs/qa/2026-10-24/plan.md` public contract/async correctness; `docs/qa/2026-10-24/coverage.md` captions/export/history; existing routeGeneration invariant in DatasetDetailPage
- `affectedFiles`: `frontend/src/pages/DatasetDetailPage.tsx:519`, `frontend/src/pages/DatasetDetailPage.tsx:407`
- `evidence`: Actual page harness starts Export now in dataset 1, navigates and loads dataset 2, then resolves the old export: `EXPORT_ROUTE_RACE {"route":"2","showsOldExport":true}`. The callback unconditionally prepends the created export to current state. Unlike load/loadItems/saveSettings, this mutation completion does not compare routeGeneration. Bulk membership callbacks have the same unguarded presentation pattern. `/tmp/omoide-r2-ui-repro.cjs` and `/tmp/omoide-r2-ui-repro.log`.
- `impact`: Dataset 2 displays another dataset's export and can expose the wrong manifest/output/training choice. Late mutation errors and selection cleanup can also affect the newly opened dataset.
- `suggestedFix`: Capture dataset identity and generation at operation start, snapshot target ids, and guard success/error/finally presentation callbacks. Reset remaining per-dataset notices and pending state on route changes. Audit sibling inline mutation handlers rather than adding a guard solely to the reproduced export path.
- `recommendedSkillOrAgent`: frontend dataset mutation-lifecycle fixer
- `fixerGroup`: frontend-dataset-lifecycle
- `acceptanceCommands`: Add delayed export and membership mutation regressions across dataset navigation; `node --test-isolation=none --test frontend/tests/*.test.cjs`; strict build and source ESLint
- `testsNeeded`: Export success/error after route change, old membership completion with media shared by both datasets, stale selection cleanup, and late operation completion after unmount
- `dependencies`: none
- `status`: fixed
- `fixedCommit`: b98f386e7ade216a944a38ccf5527ed52ce0794d

### UI-R2-003: Right-angle image rotation cannot save because output validation reads unrotated metadata

- `round`: 2
- `reviewer`: UI/contracts
- `label`: issue
- `blockingStatus`: blocking
- `severity`: P1-high
- `doctrineBucket`: not-doctrine
- `sourceRefs`: `docs/qa/2026-10-24/coverage.md` edit/crop feature contract; `frontend/src/utils/editorOps.ts` replay geometry/output invariant; installed Filerobot 4.9.1 `lib/hooks/useTransformedImgData.js`
- `affectedFiles`: `frontend/src/components/ImageEditorDialog.tsx:443`, `frontend/src/components/ImageEditorDialog.tsx:460`, `frontend/src/utils/editorOps.ts:125`
- `evidence`: Root Chrome: synthetic 640x480 image -> Rotate +90 degrees -> editor shows 480x640 -> Save copy rejects with expected 640x480/replay 480x640, and no file changes. Installed Filerobot computes a rotated canvas (`getSizeAfterRotation`, stage dimensions) but returns imageData.width/height as unrotated crop dimensions unless resize overrides them. App passes those metadata fields into designStateToOps despite imageData.imageCanvas carrying 480x640. Temporary isolated regression evaluates the actual app save-call initializer with that real provider contract and fails with the same message: `/tmp/omoide-r2-rotation-repro.cjs`, `/tmp/omoide-r2-rotation-repro.log`.
- `impact`: A basic supported right-angle rotation cannot be saved as a copy or overwrite. The safety guard is rejecting correct replay geometry because its expected-size input is wrong.
- `suggestedFix`: Derive expected rendered dimensions from the actual image canvas, accounting for configured pixel ratio, while retaining replay output validation. Preserve crop, resize and virtual-edit behavior; do not remove the mismatch guard or silently resize back to the unrotated metadata.
- `recommendedSkillOrAgent`: image editor serialization/replay fixer
- `fixerGroup`: frontend-image-edit-contract
- `acceptanceCommands`: Add failing permanent rotation save contract regression, plus 90/180/270, crop+rotate and resize cases; frontend suites/build/lint; relevant Python image-edit tests; root Chrome Save copy verifies a 480x640 result from the 640x480 fixture
- `testsNeeded`: Actual canvas vs metadata disagreement, right-angle rotations, cropped rotations, resize, pixel ratio and genuine output mismatch rejection
- `dependencies`: none
- `status`: fixed
- `fixedCommit`: b98f386e7ade216a944a38ccf5527ed52ce0794d

### UI-R2-004: Video preview is keyboard-focusable but has no accessible role or name

- `round`: 2
- `reviewer`: UI/contracts
- `label`: issue
- `blockingStatus`: blocking
- `severity`: P2-medium
- `doctrineBucket`: not-doctrine
- `sourceRefs`: `docs/qa/2026-10-24/plan.md` accessibility review role; `docs/qa/2026-10-24/coverage.md` shell/keyboard and video controls; installed ReactPlayer 2.16.0 Preview render contract
- `affectedFiles`: `frontend/src/components/VideoPlayer.tsx:381`, `frontend/src/localFilePlayer.ts`, installed `react-player/lib/Preview.js:123`
- `evidence`: App enables light/thumbnail preview without supplying accessible preview controls. Installed Preview renders a div with tabindex 0, no role and default empty previewAriaLabel; direct installed-class reproduction returns `{"element":"div","tabIndex":0,"role":null,"ariaLabel":null}`. Root Chrome independently confirms this markup. Clicking produces a native video with controls and complete 3.018-second playback, so this is an accessibility defect, not a playback failure.
- `impact`: Keyboard focus reaches an unnamed generic element, and assistive technology cannot identify the Play action as a named control before video activation.
- `suggestedFix`: Provide an app-owned named button/semantic preview activation control with Enter/Space support and one coherent tab stop. Preserve lazy thumbnail behavior, native controls after activation and streaming fallback. A label alone on an otherwise generic focusable div is insufficient to establish button semantics.
- `recommendedSkillOrAgent`: frontend accessible-video-control fixer
- `fixerGroup`: frontend-video-preview-accessibility
- `acceptanceCommands`: Add preview semantics/name/Enter/Space regression, run frontend/hotkey tests and build/lint; root Chrome accessibility/DOM proof verifies named Play button before activation and native controls afterwards
- `testsNeeded`: Accessible role/name, exactly one intended tab stop, Enter and Space activation, pointer activation, and unchanged post-activation playback
- `dependencies`: none
- `status`: fixed
- `fixedCommit`: b98f386e7ade216a944a38ccf5527ed52ce0794d

No source or test file was edited during this review. Temporary synthetic harnesses intercepted all services; no HTTP, private media, or production state was touched.

### R3-API-01: Replacement conversion adopts a source version changed during encoding and deletes it

- `round`: 3
- `reviewer`: API/domain architecture reviewer, rotated independent runtime identity/CAS review
- `label`: issue
- `blockingStatus`: blocking
- `severity`: P0-critical
- `doctrineBucket`: not-doctrine
- `sourceRefs`: `AGENTS.md` — failing-test-first audit rule; `docs/qa/2026-10-24/rounds.md` — R2 source identity/recovery preservation; `app/conversion.py:199` — independent recovery and identity contract; synthetic command/log below.
- `affectedFiles`: `app/conversion.py:209`, `app/conversion.py:351`, `app/conversion.py:367`, `app/tasks/conversion.py:235`, `app/tasks/conversion.py:380`; corresponding conversion regression tests.
- `evidence`: `PYTHONPATH=. .venv/bin/python /tmp/omoide-qa-round3-source-change-probe.py` failed both `.mp4` same-path and `.avi` extension-changing replacement subcases. The fake encoder reads the initial source, produces encoded bytes, then writes a distinguishable newer version to the source before returning. Real `convert_media(..., 'replace_original')` finishes successfully, leaving exactly `[b'encoded-original-before-encode']`; `b'newer-user-version-during-encode'` is absent from every remaining file. Assertions fail at temporary probe line 31. Full output: `/tmp/omoide-qa-round3-source-change-probe.log`. Encoding occurs at line 351, while the first source identity is only captured inside `publish_replacement` at line 209. Consequently the new source version is copied to a backup, treated as the approved source, overwritten/deleted, and its backup then deleted by lines 303–317. The legacy task caller likewise starts FFmpeg without carrying its pre-encoding source identity into publication.
- `impact`: Confirmed loss of a newer source version in isolated synthetic fixtures when a source is edited or replaced during conversion. Long video encodes make this an extended conflict window. No production loss is claimed. This is separate from the documented narrow final-stat-to-filesystem-mutation race: the edit is fully complete before the first publication identity snapshot.
- `suggestedFix`: Capture an expected source identity before encoding/probing starts in both replacement callers; require publication to validate against that expected identity rather than adopting the current source as its baseline. Reject a mismatch before publication/catalog commit, preserve the newer source, retain/report encoded recovery as appropriate, and leave the catalog pointing to the original source. Preserve the documented narrow portable stat/mutation limitation without claiming external atomic locks.
- `recommendedSkillOrAgent`: Runtime/conversion fixer agent
- `fixerGroup`: `app/conversion.py`, `app/tasks/conversion.py`, focused conversion transaction/lifecycle tests
- `acceptanceCommands`: `PYTHONPATH=. .venv/bin/python /tmp/omoide-qa-round3-source-change-probe.py`; `TMPDIR=/tmp .venv/bin/python -m unittest discover -s tests -p 'test_runtime_transaction_safety.py' -v`; `TMPDIR=/tmp .venv/bin/python -m unittest discover -s tests -p 'test_round6_safety_conversion_lifecycle.py' -v`; permanent new focused source-version tests chosen by fixer, followed by root full suite.
- `testsNeeded`: Permanent failing-first regressions for source edits during encoding for same-path and extension-changing replacement, covering shared `convert_media` and legacy `_run_conversion` entry points; assert newer bytes survive, no stale catalog publication/completed task, and usable encoded recovery is retained/reported. Include both in-place edits and inode replacements where practical; preserve successful ordinary conversion coverage.
- `dependencies`: none
- `status`: fixed
- `fixedCommit`: 9c7a75983a9495dbcf2c98434675055ebb9db6ac

### R3-RUNTIME-01: Unprepared profile globals admit ordinary requests before activation succeeds

- `round`: 3
- `reviewer`: Quality/runtime — readiness and transactional activation isolation
- `label`: issue
- `blockingStatus`: blocking
- `severity`: P1-high
- `doctrineBucket`: not-doctrine
- `sourceRefs`: R2 profile activation rollback contract in inventory/rounds; docs/qa/2026-10-24/plan.md readiness and existing-feature scope; app/config/__init__.py:348-361 preparation-before-retiring-old-engine intent; app/database.py:get_session; AGENTS.md health readiness
- `affectedFiles`: app/config/__init__.py:355-361; app/database.py:get_session; shared API/readiness boundary if preparation cannot be made explicit; alembic/env.py:_prepare_config currently forces global settings URL
- `evidence`: reload_settings mutates global settings and db.engine before run_migrations/ensure_vec_tables. During mocked migration callback, ordinary GET /api/tags/ goes from200 before activation to500 while candidate schema is absent and back to200 after switch409 restores old runtime. A second real HTTP fixture creates partial candidate tables, POST /api/tags/ succeeds201 with candidate-only-tag while activation is still preparing, then migration fails409; restored active catalog remains empty. Health's migration-state check does not gate normal request/session admission. Existing8 tests assert final rollback identity but do not exercise concurrent request admission during preparation.
- `impact`: Requests can fail on an unfinished schema or successfully write into a profile that is then rejected, leaving user actions in the wrong/inactive catalog despite the profile switch failing. Retaining/restoring old engine afterward cannot compensate those requests.
- `suggestedFix`: Prepare the candidate against explicit engine/config arguments without exposing global settings/engine until readiness succeeds. Alembic environment must honor an explicit target while retaining active-profile CLI defaults; initial vector dimension migration must use candidate dimensions. If globals must temporarily change, gate all ordinary read/write/session admissions with controlled503 and serialize activation, not just the health endpoint. Preserve same-physical-database failed-schema degraded state and rollback/disposal contracts.
- `recommendedSkillOrAgent`: profile/runtime activation fixer
- `fixerGroup`: configuration/database/Alembic preparation and focused activation/request tests
- `acceptanceCommands`: Failing concurrent GET and POST TestClient regressions during migration and vector preparation; rejected candidate receives no ordinary writes, active catalog stays usable or controlled503; test_profile_activation_transaction.py, pipeline_profile, migration_health, migration index tests, full canonical backend suite.
- `testsNeeded`: Runtime interleaving at migration and vector stages, successful/rejected candidate reads/writes, explicit migration URL/dimensions, same-db failure degradation and preserved old runtime/pools; CLI migration backward compatibility.
- `dependencies`: none
- `status`: fixed
- `fixedCommit`: 9c7a75983a9495dbcf2c98434675055ebb9db6ac

No additional evidence-backed protected-file/quality gate blocker was identified. Existing warning/platform/external-inference limits remain as recorded; no warning suppression or implied external acceptance.

### UI-R3-001: Viewport fit leaves Undo/Redo crop history in obsolete coordinates

- `round`: 3
- `reviewer`: UI/contracts
- `label`: issue
- `blockingStatus`: blocking
- `severity`: P1-high
- `doctrineBucket`: not-doctrine
- `sourceRefs`: `docs/qa/2026-10-24/plan.md` every-feature correctness and failing-test-first requirements; `docs/qa/2026-10-24/rounds.md` Round2 geometry closure; installed Filerobot4.9.1 `actions/undo.js`, `actions/redo.js`, `utils/extractCurrentDesignState.js`
- `affectedFiles`: `frontend/src/components/ImageEditorDialog.tsx:282`, `frontend/src/utils/editorOps.ts:119`, `frontend/tests/round2-ui.test.cjs`
- `evidence`: `/tmp/omoide-r3-editor-history.cjs` executes installed undo.js and useTransformedImgData through the existing geometry harness. Fit updates live full crop568.3×426.225 to503.4967805989583×377.6225854492187, but pastDesignStates still contains the old crop. Undo restores568.3×426.225 while shown dimensions remain503.4967805989583×377.6225854492187. refitEditorCrop returns undefined because dimensions no longer change and the crop differs. Provider canvas becomes722×542; serializer throws `The editor expects a 722×542 result but the replay would produce 640×480`. A subsequent Flip or finetune remains a real edit but cannot save. Future history has the same coordinate ownership problem.
- `impact`: Normal editor Undo/Redo after rotation-induced viewport fit can restore an oversized/wrong crop and block saving. Partial crops can also replay the wrong source region. The safe mismatch guard prevents an incorrect write but does not complete the workflow.
- `suggestedFix`: Whenever live shown coordinates are refitted, transform crop coordinates in pastDesignStates and futureDesignStates into that same space alongside the live crop. Preserve each history entry's rotation/flips/resize and all unrelated fields. Ensure automatic bookkeeping does not create undo entries. Retain the output guard, pure reducer callbacks and cleanup/current-snapshot checks.
- `recommendedSkillOrAgent`: frontend editor/provider integration fixer
- `fixerGroup`: frontend image-editor geometry adapter and focused tests
- `acceptanceCommands`: Permanent installed-provider Undo/Redo regression first; `node --test-isolation=none --test frontend/tests/*.test.cjs`; every hotkey test; strict build; canonical src ESLint; parent Chrome rotate→fit→Undo→Flip→Save copy with unchanged original
- `testsNeeded`: Live and past/future full and partial crop coordinates through repeated fits; actual installed Undo/Redo reducers and export geometry; explicit resize preservation; unchanged user edits; observer lifecycle and reducer replay checks
- `dependencies`: none
- `status`: fixed
- `fixedCommit`: 9c7a75983a9495dbcf2c98434675055ebb9db6ac

Review notes/limits: current observer cleanup cancels its scheduled frame, guards a pending asynchronous capture after cleanup, and uses pure conditional correction callbacks; no additional blocker reproduced in these paths. Caption ignored-abort and unmount regressions and dataset export identity regression substantiate their specific scenarios; source inspection found no new required issue in the reviewed caption/dataset guards. Their lightweight hook harness does not simulate concurrent React scheduling. R2 wording that StrictMode callback safety is regression-covered is broader than the existing tests (they exercise the pure geometry helper, not the effect lifecycle); add direct adapter lifecycle/replay evidence with this fix or narrow that claim. Canonical src lint choice matches AGENTS and plan. Broad CJS lint scope and no-embedding Similar wording remain documented tooling/UX backlog, not new blockers. Existing922backend/206frontend/76hotkey/9MCP gates were not redundantly rerun for read-only review.

### UI-R4-001: Rejected queued fit correction is permanently forgotten

- `round`: 4
- `reviewer`: UI/contracts
- `label`: issue
- `blockingStatus`: blocking
- `severity`: P1-high
- `doctrineBucket`: not-doctrine
- `sourceRefs`: `docs/qa/2026-10-24/plan.md` asynchronous boundaries and correctness; `frontend/node_modules/react-filerobot-image-editor/lib/actions/updateState.js` queued functional UPDATE_STATE reducer; installed React18 `dispatchReducerAction`; R3 observer acceptance contract
- `affectedFiles`: `frontend/src/utils/editorFitObserver.ts:18`, `frontend/tests/editorHistory.test.cjs:29`
- `evidence`: `/tmp/omoide-r4-observer-race.cjs` uses actual installed SET_SHOWN_IMAGE_DIMENSIONS, CHANGE_ROTATION and export hook. Observer reads640×480 source/display, then fitted400×300 and queues its correction. Before the correction reducer executes, user rotation changes the adjustments object; stale-snapshot guard correctly rejects the callback. However previous already advances to400×300 plus the uncommitted corrected crop. Next read has400×300 with old640×480 crop, so no correction is attempted. Provider renders768×1024; saving throws `editor expects768×1024...replay480×640`. Installed useReducer schedules callback evaluation, so apply need not synchronously commit. Existing test proves rejection but never continues another frame to check recovery.
- `impact`: A user edit overlapping an automatic viewport fit can leave permanent stale crop/history coordinates and a blocked save, even after the editor settles. The guard prevents an incorrect write but the retry contract is incomplete.
- `suggestedFix`: Retain/acknowledge pending fit ownership until a correction is accepted, or reconcile coordinate basis atomically with the current provider state. Re-read and retry rejected corrections while preserving newer user edits; do not double-scale history after a successful commit or reducer replay, and do not repeatedly correct an unchanged stable state. Keep cleanup guards and output mismatch protection.
- `recommendedSkillOrAgent`: frontend provider-adapter concurrency fixer
- `fixerGroup`: editor fit observer/geometry adapter and focused tests
- `acceptanceCommands`: Permanent queued-callback rejection→next-read regression first; full frontend/hotkeys, strict build, canonical src lint; targeted observer replay/no-spin/history tests; parent Chrome public viewport/editor actions if timing permits
- `testsNeeded`: Queued rotation/history/crop edit before correction; next reads retry or settle; accepted correction acknowledges exactly once; repeated callback evaluation does not double scale; real new crop wins; pending cleanup remains inert
- `dependencies`: none
- `status`: fixed
- `fixedCommit`: pending

Limits: This scheduling interleaving is deterministic dependency/adapter evidence, not claimed Chrome-reproduced. No hidden provider state or impossible crop was injected: display fit and rotation use installed reducers. Parent Chrome has already proved public persistent-viewport resize→Undo→Flip→Save copy with original intact. Direct scheduling may be difficult to force through public controls, so the regression provides the race proof while browser acceptance proves the composed settled workflow. Existing external-model/platform/load coverage limits remain accurately recorded. Similar no-embedding UX and broad CJS lint tooling remain nonblocking backlog. Protected annotation files remain unchanged in the actual76-file diff. Documentation's dated R1 count199 in the coverage row is historical evidence; current gate210 is explicit in rounds.md, not a claim that every scenario is tested. No warning suppression or blanket every-feature browser pass is asserted.

### R4-RUNTIME-01: Extension-changing conversion discards recovery despite a changed committed destination

- `round`: 4
- `reviewer`: Quality/runtime — independent conversion publication/commit/recovery review
- `label`: issue
- `blockingStatus`: blocking
- `severity`: P1-high
- `doctrineBucket`: not-doctrine
- `sourceRefs`: R2-RUNTIME-01 identity-conflict retention contract in inventory/rounds; docs/qa/2026-10-24/plan.md synthetic destructive safety; app/conversion.py:publish_replacement postcommit ownership check
- `affectedFiles`: app/conversion.py:postcommit expected/source identity check and obsolete-file cleanup
- `evidence`: publish AVI->MP4 with real SQLite. Commit callback durably commits catalog, then atomically replaces published MP4 with a newer unrelated inode. Helper returns success and preserves that newer destination, but deletes original AVI, independent original backup and encoded recovery. Reproducer prints published_returned=True, newer_output_preserved=True, original_source_exists=False, encoded_recovery_exists=False, original_recovery_backups=0. Postcommit code checks destination only for same-path MP4; extension-changing output checks old source only. This is missing verification across the complete publication-to-commit window, not a race between the final stat and unlink.
- `impact`: A destination identity conflict during the commit window is silently treated as fully owned success. Neither original source nor independently encoded result survives for recovery once the published destination was replaced. Existing source-change and pre-encoding tests do not exercise this distinct output-side interleaving.
- `suggestedFix`: Check the published destination against its bound publication identity after durable commit, as well as the source identity. On either conflict preserve the newer destination/source and independent original/encoded recovery, and report conflict consistently. Keep committed catalog/task semantics explicit rather than claiming database rollback after a durable commit.
- `recommendedSkillOrAgent`: conversion storage safety fixer
- `fixerGroup`: app/conversion.py and focused conversion regressions only
- `acceptanceCommands`: Failing postcommit destination-replacement/in-place-edit tests for extension-changing and same-path modes; verify recovery retention, newer bytes untouched, explicit durable catalog/task semantics; run test_runtime_transaction_safety.py, test_conversion_source_version.py, safety_conversion_lifecycle, safety_paths and full canonical backend suite.
- `testsNeeded`: Runtime callback interleaving over real SQLite commit; changed and unchanged destination controls; retained original/encoded versions and no foreign inode overwrite/unlink.
- `dependencies`: none
- `status`: fixed
- `fixedCommit`: pending

No other required runtime/quality issue reproduced in this bounded round. R3 source-binding regressions pass; task lifecycle and documented acceptance limits remain supported by permanent tests and ledger. This review does not claim power-loss atomicity, arbitrary external-write exclusion, external inference quality or desktop platform proof.
