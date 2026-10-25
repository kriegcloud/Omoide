# Unindexed media and scan accounting repair

This repair follows the upstream v0.7.6 integration at `462d2c4e97227b1cde5033da1127065893ae43b9`, which was pushed to `origin/master` before repair work began.

## Findings

The workstation's face and person tables, both face/person vector tables, and model fingerprint table were empty. AdaFace initialization nevertheless required a completed migration marker. There were no existing vectors to migrate, so Process Unindexed Media failed before processing any media.

The historical scan discovered 335,790 candidates. Read-only database inspection found 260,241 rows inserted during that scan's interval: 259,088 with thumbnails and 1,153 with thumbnail errors. Those thumbnail errors belong to indexed rows. The difference between candidates and indexed rows is 75,549, but the historical task retained only its latest 500 failures and incorrectly reported that sample count as the total. All 500 retained examples were file-not-found metadata errors; their files and parent directories were still absent during investigation. Both configured external volumes were mounted. The retained samples cannot determine the cause of every historical omission or establish who removed any source file.

No original task results, source files, face assignments, matching thresholds, or production database records were changed during diagnosis and isolated verification.

## Behavior after repair

- A missing AdaFace marker may be initialized only when both entity tables and both vector tables exist and are empty, and there is no migration journal state. Existing mismatched, failed, or migrating markers remain blocking. Service identity, health, and demographics initialization must succeed before the final atomic empty-state check and marker insertion. Failed setup leaves the processor uninitialized so retries cannot bypass the guard.
- Scans distinguish discovered candidates, indexed rows, skipped candidates, thumbnail failures, and unreadable directories. A completed scan reports `processed = total = discovered = indexed + skipped`. Interrupted scans retain admitted outcomes and report remaining candidates.
- Missing-source labels require a confirming filesystem check. Decoder errors are not automatically classified as missing files. Indexed rows with thumbnail errors remain indexed and retain their processing error.
- Exact scan issue totals persist independently of the latest 500 examples. Examples remain available after restart through the failures endpoint, while ordinary list and event payloads omit detailed path arrays.
- The task UI describes issues separately from skipped files. New tasks show retained sample count and true total; legacy tasks without a reliable total say the total is unavailable. Active totals account for newer live counts between checkpoints.

## Verification

Regression tests reproduced the migration gate, initialization retry, capped-count, progress, mixed thumbnail/metadata, directory-error, cancellation, insertion-race, payload-size and UI count defects before their fixes.

The real Process Unindexed Media endpoint completed two synthetic images using the workstation's AdaFace service in an isolated container, with a separate database and read-only model/service mounts. Task `2ba8b5c5-bbf5-434c-8e1e-4f73c95845e8` completed 2/2. This is bounded processor proof, not a claim that the production library was processed.

A real isolated scan indexed two fixture files, including one invalid-image fixture with a thumbnail error. It completed 2/2 and reported `2 indexed, 0 skipped (2 discovered), 1 thumbnail failure`. Browser verification showed that summary and the correctly labeled thumbnail issue. A separate synthetic task with 1,207 issues and 500 stored examples displayed `Showing latest 500 of 1,207 issues` in the browser.

The workstation image built successfully, booted with the merged migration head, and served the bundled v0.7.6 frontend in the browser. Its ONNX CLIP implementation downloaded the configured `xlm-roberta-base-ViT-B-32` export into an isolated cache and produced a finite 512-dimensional embedding for a synthetic image.

The same built image then completed Process Unindexed Media with face detection, image embeddings, EXIF and blur processors all enabled. Task `d2bfddca-9746-4cd2-bb77-e0640e0bdc4a` completed 2/2 in 5.8 seconds. Both synthetic rows had `faces_extracted` and `embeddings_created` set, neither had a processing error, two image vectors existed, and the pinned face marker was ready. The browser displayed the completed task. The face service/model mounts were read-only, and only the isolated database received writes.

The full backend suite passed 879 tests in 433.979 seconds. The final payload/accounting changes also passed their focused reruns. Strict TypeScript/Vite build, 44 selection tests and 76 hotkey tests passed. ESLint remained at the integrated baseline of nine errors and two warnings outside the changed task component; targeted task-component lint passed. Alembic reports exactly one head, `e0f1a2b3c4d5`. All isolated verification containers were stopped and removed after proof.

Production deployment is a separate step: the backend container must be recreated and the frontend bind mount updated using the documented deployment sequence. The repair does not restore absent source files or reconstruct uncaptured historical error categories.
