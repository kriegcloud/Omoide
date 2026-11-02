# Dataset / curation Phase 3 inventory and receipt

Baseline: all dataset mutations awaited receipt before committed bus events; item/detail/triage metadata did not use the shared operation overlay. New/add/crop/frame/train/eval dialogs waited for transport; NewDataset had no visible catch. Triage blocked navigation during Keep. Dataset detail cleared loaded cards on every refresh; captions hid loaded rows behind a spinner. Curation already preserved revision/hash/passkey authority and snapshotted decision inputs, but kept confirmations open and locked unknown results until manual refresh.

Classification b (metadata), all now core-owned with immediate scoped patches or explicit derived intent where authoritative totals cannot be inferred:
- POST `/api/datasets` createDataset — NewDatasetDialog, DatasetsPage, AddToDatasetDialog. Provisional nonnavigable row; definitive rejection removes it; confirmed row replaces it. Reopened drafts survive late receipt.
- PATCH `/api/datasets/{id}` updateDataset — DatasetDetailPage. Dataset list fields overlay; editable detail draft remains immediate and preserves edits made during save.
- DELETE `/api/datasets/{id}` deleteDataset — export currently has no UI caller. Pending removal; prewrite denials roll back.
- POST/DELETE `/api/datasets/{id}/items` addDatasetItems/removeDatasetItems — AddToDatasetDialog, DatasetDetailPage, SelectionActionBar. Add uses explicit pending derived membership (no guessed added count); removal hides exact media membership and restores skipped receipt IDs. Existing Undo uses confirmed added IDs.
- PATCH `/api/datasets/{datasetId}/items/{itemId}` updateDatasetItem — DatasetDetailPage, DatasetTriagePage, DatasetItemMenu; immediate item fields across detail/triage, guarded receipt application.
- POST `/api/datasets/{datasetId}/items/{itemId}/review` reviewDatasetItem — DatasetTriagePage. Authority timestamp is never fabricated; Keep advances immediately, stamps via server review endpoint, retains acknowledged Undo history.
- PATCH `/api/datasets/{datasetId}/items/{itemId}/caption` updateDatasetCaption — DatasetCaptionsPanel. Immediate caption overlay; drafts retained.
- POST `/api/datasets/{datasetId}/items/{itemId}/caption/reviewed` markDatasetCaptionReviewed — DatasetCaptionsPanel. Pending authority marker, no fabricated review stamp.
- POST `/api/datasets/{id}/items/reinclude` reincludeDatasetItems; POST `/api/datasets/{id}/dedupe` dedupeDataset; POST `/api/datasets/{id}/auto-select` autoSelectDataset — DatasetDetailPage. Uncomputed bulk outcomes show pending derived intent; server determines membership totals. Auto-select confirmation dismisses immediately. Dry-run preview remains read-result presentation despite POST transport.
- POST `/api/datasets/{id}/fill-gaps` fillDatasetGaps — service export; DatasetDetailPage currently adds preview IDs through addDatasetItems. Pending derived intent, authoritative reread.
- POST `/api/datasets/{id}/items/batch-crop` batchCropDatasetItems — BatchCropDialog. Immediate dismiss; generated crop data remains server-authoritative.
- POST `/api/datasets/from-person/{personId}` createDatasetFromPerson — PersonDetailPage. Core lifetime/error ownership; no guessed generated membership.

Classification c (pipeline/task), all core-owned with job submission feedback and existing accepted/running pollers:
- POST `/api/datasets/{id}/export` createDatasetExport — DatasetDetailPage (DatasetExport history polls pending/running).
- POST `/api/datasets/{id}/train` createTrainingRun; POST `/api/datasets/runs/{runId}/cancel` cancelTrainingRun; POST `/api/datasets/runs/{runId}/rescore` rescoreTrainingRun — TrainingRunsPanel. Training modal dismisses before transport, accepted run history continues polling.
- POST `/api/datasets/runs/{runId}/eval` createEvalBatch; POST `/api/datasets/evals/{batchId}/cancel` cancelEvalBatch — TrainingRunsPanel. Eval confirmation dismisses immediately; batch status polling retained.
- POST `/api/datasets/{id}/pose-backfill` backfillDatasetPose — DatasetDetailPage.
- POST `/api/datasets/{id}/mine-frames` mineDatasetFrames — FrameMiningDialog; immediate dismiss and existing task-completion refresh.
- POST `/api/datasets/{id}/regularization` buildRegularizationDataset — DatasetDetailPage. Immediate dismiss, pending derived intent, navigate only after actual created dataset.
- POST `/api/datasets/{id}/captions/generate` generateDatasetCaptions — DatasetCaptionsPanel; accepted Task and existing status poller.

Curation inventory (createCurationClient; caller CurationReviewPage):
- POST `/api/curation/auth/registration/options`, `/auth/registration/verify`, `/datasets/{id}/review-options`: c/authority ceremonies; existing explicit passkey busy state and cancellation retained. These do not claim review acceptance and do not enter global operation payloads.
- POST `/api/curation/datasets/{id}/materialize`: c, pending prepare intent; original pixels remain authoritative.
- POST `/api/curation/datasets/{id}/captions`: b, typed proposal remains visible during submission; server revision/hash remains authoritative.
- POST `/api/curation/datasets/{id}/reviews`: b/authority, confirmation dismisses immediately; visible pending status; no fabricated human acceptance. Cancelled passkey restores captured decision for human retry.
- POST `/api/curation/datasets/{id}/exports`, `/api/curation/exports/{id}/resume`: c, core submitting/accepted feedback; actual receipt renders admitted/running/succeeded/blocked. No automatic resume/replay.

All 25 dataset mutating exports snapshot arguments before queued transport. Unknown outcomes read dataset+item scope (or collection for run-only/creation exports), publish targeted dataset events, and release uncertain intent only after read success. No destructive mutation replay. Multi-item prefix writes are conservatively treated as uncertain on errors even where an HTTP denial alone could appear definitive. Curation unknown page reads actually update rendered base and preserve drafts; failed rereads retain a visible refresh gate. Credentials, proof payloads, and request bodies are absent from operation store input.

Files: services/datasets.ts, services/curation.ts; pages/DatasetsPage.tsx, DatasetDetailPage.tsx, DatasetTriagePage.tsx, CurationReviewPage.tsx; components/NewDatasetDialog.tsx, AddToDatasetDialog.tsx, BatchCropDialog.tsx, FrameMiningDialog.tsx, TrainingRunsPanel.tsx, DatasetCaptionsPanel.tsx; tests/phase3-datasets.test.cjs, datasetRuntime.cjs, dataset-audit.test.cjs, curation-review.test.cjs.

Failing-first evidence: four initial fixtures failed on immediate item patch, unknown read, new/crop dismissal; additional baseline fixtures failed curation immediate dismissal/unknown reread and removal skip/job feedback. Reopened creation draft fixture failed before race correction. Green focused run: 85 dataset/curation tests (see /tmp/phase3-dataset-results.txt). Updated one legacy triage fixture to assert responsive navigation and no second advance on acknowledgement. Existing sort stale-response rejection fixture retained and green. tsc and targeted ESLint clean; root owns final full-suite/build validation. No Python edits/tests, protected files, commits, installs, deploy or live mutations.

Browser follow-ups: validate latency/focus/reopen on new/add/crop/frame/train/eval dialogs; dataset detail/triage/captions overlays under slow/rejected/lost responses; skipped-member removal; loaded-list retention and scroll; curation passkey cancellation and exact revision/hash authority; task accepted-to-running pollers. Node fixtures are synthetic proof, not live browser acceptance.
