# Upstream integration, 2026-10-24

Integrated EinAeffchen/Omoide master `3466099` (v0.7.6) into the workstation fork, preserving both histories. The pre-sync fork head was `7ab2b4b`; local launcher settings were saved separately before the merge. Private environment backups, IDE state and nested worktrees stay outside version control and Docker build contexts.

## Behavior retained

- Upstream ONNX CLIP inference, batched/prefetched processing, modular APIs and clustering, opaque pagination, people search/sorting/favorites/thumbnail sizing, async person export, map folder/sort/writeability controls and database-only manual geolocation.
- Upstream keep-original conversion, explicit original replacement, streaming playback fallback and opt-in automatic conversion. Conversion retains the fork's exclusive temporary files, conditional task publication, cancellation, child-process cleanup and recovery behavior.
- Fork selection/marquee/hotkeys/dialogs/undo, task resumption, missing-file review and folder boundaries, annotation and curation integrations, datasets/training/repairs, people visibility/demographics/social links, provenance, ownership claims, presentation mode, loopback/same-origin checks and readiness health.
- Fork face-matching thresholds remain unchanged. The new matching module uses the fork's pose-aware, margin/rejection-aware conditional claims; upstream's lower video-track acceptance threshold was not adopted.

## Integration decisions

The upstream media/person concern packages are active. Workstation-only routes live in their extensions modules. The configuration package retains workstation fields and guarded reload semantics. Historical utility imports remain compatible with protected annotation consumers.

The fork's richer configuration and review pages remain routed; new upstream controls and helpers are integrated into them. TypeScript checking is part of the production build. Modal media viewers have their own selection context so their Similar grid cannot clear background gallery selections.

Upstream and the fork independently used migration ID `c5d6e7f8a9b0`. The fork's historical annotation revision is unchanged; the incoming reverse-index revision is `c5d6e7f8a9b1`. Its geolocation successor and fork head join at `e0f1a2b3c4d5`, the sole head. Overlapping index removal is guarded for full downgrades.

The workstation image clears the inherited dependency image's version override, allowing the checked-out app/VERSION to identify the actual application.

## Validation environment

Fresh schema creation and an online-backup copy of the existing workstation database both upgraded to the merge head. Browser QA uses a separate loopback container on port 18123, synthetic images and people, and isolated data. Production is not changed by this preview.

Curation source-registration tests require a filesystem with an observable UUID; use the normal temporary directory on this workstation. `/tmp` is tmpfs and correctly fails the production volume-identity guard. Do not weaken the guard to make such a test environment pass.

The premerge lint baseline was 50 errors and one warning. The integrated frontend has nine inherited errors and two warnings on unchanged ref-attachment hooks exposed by upstream's new exhaustive-deps rule. No rule was suppressed; warnings in touched components were repaired.

## Acceptance

The full backend discovery run passed 858 tests on the normal UUID-bearing filesystem. The subsequently restored processor-list compatibility endpoint passed its additional focused HTTP regression. The strict TypeScript/Vite build, 44 selection tests and 72 hotkey tests passed. All 254 existing HTTP operations remain present, with five upstream additions. Browser checks confirmed modal selection retention, people filtering, the hotkey overlay, geotagging folder/writeability controls, and video settings. The online-backup migration retained all 260,241 media rows.
