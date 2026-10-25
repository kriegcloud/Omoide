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
- `fixedCommit`: round1 commit (see rounds.md)

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
- `fixedCommit`: round1 commit (see rounds.md)

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
- `fixedCommit`: round1 commit (see rounds.md)

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
- `fixedCommit`: round1 commit (see rounds.md)

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
- `fixedCommit`: round1 commit (see rounds.md)


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
- `fixedCommit`: round1 commit (see rounds.md)

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
- `fixedCommit`: round1 commit (see rounds.md)

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
- `fixedCommit`: round1 commit (see rounds.md)


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
- `fixedCommit`: round1 commit (see rounds.md)

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
- `fixedCommit`: round1 commit (see rounds.md)

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
- `fixedCommit`: round1 commit (see rounds.md)

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
- `fixedCommit`: round1 commit (see rounds.md)


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
- `fixedCommit`: round1 commit (see rounds.md)
