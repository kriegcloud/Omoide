# Current implementation context

The contract below records the original fixture policy. The subsequent disabled
production-still extension is specified in [production-authority.md](production-authority.md)
and [source-registration.md](source-registration.md). Production authority never
inherits fixture acceptance. See the AI hub's implementation handoff for current
verification and deployment limits.

Execution of admitted operations is now a shared `ProcessingTask` job with a
worker lease, cancellation checkpoints and startup reconciliation; that
supersedes the "no generic ProcessingTask resume factories" sentence in *Durable
execution and frozen export* below and is specified in
[task-execution.md](task-execution.md). Nothing else in this contract changes:
admission, authority, review semantics, export membership and published bytes are
untouched.

---

# Fixture still curation contract — v1

This branch implements stages 0–1 and a bounded still-image path through stages 2–5 from the approved AI hub packet. It is **fixture-only**, disabled unless the server environment has `OMOIDE_CURATION_FIXTURES=1`. It does not confer production readiness or real human presence. The source baseline is `717761a87e978dcaadc0eef0d7bb986363e5ee52`; the verified pre-change Alembic head was `4e5f60718293`, and the additive child is `5f60718293a4`.

## Authority and boundaries

Trusted local Python setup registers at most 20 rights-clear files under one source root and one disjoint derivative/export store. It issues random, hashed-at-rest credentials for one dataset, fixed subject, source/store roots, operation set, eight-hour expiry, and disclosure class. Source/root paths and actor claims are absent from API inputs. HTTP cannot create a dataset, grant, source, root, subject assertion, or credential. `agent` can read metadata, prepare stills, propose captions, and export already eligible members; it cannot review or fetch preview bytes. `fixture_human` additionally has review and local fixture preview permission. This credential **simulates the reviewer authority lane**; it is not a person-presence assertion. No route accepts generative ancestry. Real identity, human-presence challenges, production authorizations and MCP remain later integrations.

The common guard freezes every legacy `/api` mutation while fixture mode is enabled. This deliberately closes all legacy HUMAN-stamping, approval, caption-review, triage, mutable configuration and header-omission bypasses without editing protected annotation files. It is an isolated-demo mode, unsuitable for enabling on the shared production application. New service functions reauthorize credentials, revision, policy and presentation mode even when called directly. Feature-off preserves legacy behavior and exposes only curation status; it does not retroactively authenticate any old review.

Source/artifact/caption/review records are append-only through the API. Mutable controls are dataset revisions, grant revocation and operation status. DB administration remains a trusted boundary; this is not protection against an administrator rewriting the SQLite file. Generative ancestry is computed transitively over artifact parents and sources; unknown/cyclic/cross-dataset ancestry blocks export. Every exact-byte approval is bound to artifact hash, immutable caption ID/hash, policy version, server actor and grant. Caption changes create revisions and require a new approval. Fixture review revocation blocks unpublished admission/recovery. Existing published bytes are retained.

## Input and storage policy

Inputs are at most 16 MiB compressed and 16 million decoded pixels. EXIF orientation applies once; metadata is stripped; the final derivative is PNG (`compress_level=9`, `optimize=False`), bounded by the raw RGB size of the pixel limit plus 4 MiB (about 52 MB, `artifact_too_large` beyond it); no crop or resize occurs. The transform is `rgb-png-v2`, whose cache/provenance key carries the Pillow/codec versions, the materializer and media-module implementation SHA-256 digests, the requested frame and, for video, the decoder identity. Export manifests separately pin the loaded authority/materialization/media/planning/export source-module SHA-256 digests. Code/profile version changes must bump the transform/policy version; exact file identity is not promised across decoder/encoder versions.

`rgb-png-v2` widened the accepted input set beyond v1's untagged single-frame RGB/grayscale. It did **not** change how an already accepted v1 input is encoded: the same JPEG/PNG/WebP bytes still produce the same artifact bytes (a regression test compares against the v1 encode). Only the transform version string changed, so re-materializing an already supported source creates a new cache entry whose artifact bytes are identical; existing artifacts and their published files remain valid. Admitted-but-unexecuted exports from before the bump fail `policy_stale` by the existing implementation-digest pin and must be re-admitted.

### Supported inputs

| Input | Handling | Provenance recorded | Refusal when unsupported |
| --- | --- | --- | --- |
| JPEG / PNG / WebP, single frame, untagged RGB or grayscale | Unchanged from v1: EXIF transpose once, convert to RGB | `color.method=untagged-assumed-srgb` | — |
| ICC-tagged (any accepted format) | Converted to sRGB through `PIL.ImageCms` with the perceptual intent, no black-point compensation | Embedded profile SHA-256 and byte length, profile description, intent name/code, littleCMS version | `unsupported_icc` (unreadable or inapplicable profile), `icc_runtime_unavailable` (ImageCms absent — never assumed) |
| 16-bit integer (`I;16`, `I;16B`, `I;16L`) | Reduced to 8 bits by `uint16-linear-255-div-65535-round-half-up` | `color.bit_depth` with input/output bit counts and the exact method name | — |
| Palette (`P`) | Expanded to RGB | `color.palette_expanded` | — |
| Alpha (`RGBA`, `LA`, `PA`, PNG/GIF `transparency`) | Accepted **only if every pixel is fully opaque**, then the opaque channel is dropped without compositing any background | `color.alpha` with `fully_opaque`, method, and `composited_background: null` | `unsupported_color_or_alpha` for any non-opaque pixel |
| CMYK | Naive decoder conversion to RGB, explicitly not colour-managed unless an ICC profile is embedded | `color.cmyk_converted`, `color.method=untagged-cmyk-naive-rgb` | — |
| HEIC/HEIF | Decoded through `pillow-heif` when importable | `runtimes.pillow_heif`, `runtimes.libheif`, `frame.source_format` | `heif_runtime_unavailable` (decoder absent — refused by magic bytes, never guessed) |
| Multi-frame stills: animated GIF/WebP/APNG, multi-page TIFF | Refused unless the request names one frame index; GIF and TIFF are reachable **only** with an explicit index | `frame.frame_count`, `frame.selected_index`, `frame.explicitly_requested` | `unsupported_animation` (WebP/PNG), `unsupported_format` (GIF/TIFF without an index), `frame_index_out_of_range` |
| Video (`.avi .m4v .mkv .mov .mp4 .webm`) | One explicitly requested presentation timestamp, decoded by `ffmpeg`/`ffprobe`; see below | `decoder.*` lineage block | `video_materialization_disabled`, `video_frame_selector_required`, `ffmpeg_runtime_unavailable`, `video_probe_failed`, `video_stream_not_found`, `video_timestamp_out_of_range`, `video_frame_count_exceeded`, `video_decode_failed`, `video_decode_timeout`, `unsupported_video_extension` |
| 32-bit integer/float (`I`, `F`), bilevel (`1`) | Refused | — | `unsupported_bit_depth` |
| AVIF, BMP, SVG, anything else | Refused | — | `unsupported_format`, `corrupt_or_unsupported_image` |
| Repair, mask, inpaint, generative upscale | **Schema stub only** — the evidence record is defined and validated, never produced | — | `generative_disabled` (403), refused before any operation is admitted |

The local preview route for **originals** (`GET /sources/{id}/content`) is unchanged and still serves only PNG, JPEG and WebP bytes; a HEIF or video original returns `unsupported_format` there. Artifact preview (`GET /artifacts/{id}/content`) is unaffected because every artifact is PNG.

Every accepted case is deterministic (each is decoded twice in tests and compared byte for byte) and carries a `color` provenance entry plus an `uncertainties` entry wherever colour accuracy is not guaranteed: assumed-sRGB inputs, ICC conversion, palette expansion, tonal loss from 16-bit reduction, dropped opaque alpha, naive CMYK conversion, and decoded video frames. This still does not claim a colour-managed general image pipeline: it claims that whatever was done is written down.

### Video timestamps and decoder lineage

Video materialization is off unless the operator set `media_policy.video_frame_materialization` in the registration manifest; the flag is pinned into `dataset.policy.media` at registration and no HTTP route can raise it. Registering a video-extension file without the flag is refused outright. With the flag off, a frame request returns `video_materialization_disabled` (403).

The registered original is never handed to `ffmpeg`. Its already hash-verified bytes are copied into a private 0700 temporary directory, decoded there with no shell, a bounded timeout and a minimal environment, and the directory is removed; the artifact then goes through the existing content-addressed no-clobber publication path. Nothing is written beside the source. Frame selection is `ffprobe`-listed presentation timestamps compared as exact rationals (`pts * time_base`), choosing the first frame at or after the requested timestamp — the request itself is an exact decimal string, never a binary float — and the extraction pins that frame by index. Lineage records container format and duration, codec name, stream index, rational timebase, average/real frame rates, decoded frame count, requested versus actual PTS (as an integer, an exact rational and the probe's decimal), frame index, key-frame flag, coded dimensions, pixel format, colour range/primaries/transfer/space where the container reports them, rotation, the `ffmpeg` and `ffprobe` version strings, and the source digest. The decoder identity is part of the artifact cache key, so a decoder upgrade cannot reuse another decoder's cache entry. Audio, subtitles and every other stream are dropped; nothing is transcoded back to the source volume.

### Repair, mask and generative comparison evidence

`RepairSelector` in `app/schemas/curation.py` documents what a repair/inpaint/restoration/generative-upscale record would have to carry: the mask artifact and mask pixel digests, the coordinate space of its region boxes with that space's dimensions (a mask drawn against pre-orientation source pixels cannot be reapplied to the artifact), the workflow and weights digests, prompt and negative-prompt digests, seed and strength, and the before/after/unmasked comparison triple with protected-detail regions. It is a validated shape and nothing else: `human_acceptance_of_exact_output_sha256` is typed as `None`, so no client can assert the exact-byte human acceptance that generative output would require, and materializing this kind always fails `generative_disabled` (403) before an operation is admitted. No mask, repair, comparison or generative artifact is produced, stored or accepted anywhere in this slice.

Every filesystem read walks directory descriptors from `/` with no symlink components, then opens a bounded regular file with `O_NOFOLLOW`. It hashes the captured descriptor bytes that the decoder receives, compares before/after stat and current directory entry, rewalks source root identity, and verifies the expected SHA-256. Roots are pinned by device/inode; on `exfat`/`vfat`/`msdos` source media, where the kernel does not keep inode numbers, registration records `root_identity.source_inode_persistent = false` and only the device, volume identity, pinned paths and file hashes fence the source root (the store root is never on such media). Production removable-volume identity is the UUID/mount/namespace pin described in `source-registration.md`. Root and destination directory substitutions are fenced before successful references/publication. Writes are exclusive, fsynced, restricted to task-owned roots, and never overwrite sources. Export copies create independent inodes; no source/cache hardlinks or writable shared exports are created. Artifact failures may retain an unreferenced immutable file; no garbage collection occurs.

## API

All routes are under `/api/curation`. Authentication is `Authorization: Bearer <fixture credential>`; callers retain it only in memory. Responses containing media use `Cache-Control: no-store`. Preview byte delivery is an explicit local fixture grant, not a guarantee that a client cannot copy bytes. No URL fetching, model call, processor startup, GPU work, cloud egress, trainer handoff, or shell execution occurs.

| Method and route | Request | Response |
| --- | --- | --- |
| GET `/status` | None; unauthenticated | `{enabled,fixture_only:true,generative_enabled:false,human_presence_verified:false}` |
| GET `/datasets` | Bearer | Array `{id,name,revision}` scoped to the credential |
| GET `/datasets/{id}` | Bearer | `DatasetDetail` below |
| POST `/datasets/{id}/materialize` | `{source_id,expected_revision,idempotency_key}` | `DatasetDetail` |
| POST `/datasets/{id}/captions` | `{artifact_id,text,expected_revision}` | `DatasetDetail` |
| POST `/datasets/{id}/reviews` | `{artifact_id,caption_id,asset_sha256,caption_sha256,decision,rationale?,expected_revision}` | `DatasetDetail` |
| POST `/datasets/{id}/exports` | `{expected_revision,idempotency_key}` | `ExportReceipt` |
| GET `/exports/{id}` | Bearer | `ExportReceipt` |
| POST `/exports/{id}/resume` | Empty | `ExportReceipt` |
| GET `/artifacts/{id}/content` | Preview grant | Exact final PNG bytes |
| GET `/sources/{id}/content` | Preview grant | Bounded original supported raster bytes |

`DatasetDetail` contains `{id,name,revision,policy_version,actor,sources,items,exports,remaining_count,blockers}`. Actor is `{id,kind,human_presence_verified:false}`. Sources contain `{id,label,sha256,status}`. Items contain `{artifact_id,source_id,sha256,width,height,generative_ancestry,transform_summary,uncertainties,caption,review,eligible,blockers}`. Caption is null or `{id,text,sha256}`. Review is null or `{id,decision,actor_kind}`. A review decision is `accept`, `reject` or `defer`; no implicit acceptance exists. A caption proposal may carry an optional `idempotency_key` (8–128 safe ASCII characters); keyed proposals record a `caption` operation, replay the unchanged draft for the same key and payload, refuse a different payload with `idempotency_conflict`, and add a `replayed` boolean to the returned detail.

`ExportReceipt` is `{id,dataset_id,status,snapshot_revision,manifest_sha256,item_count,error_code,attempts}`; status is `admitted`, `running`, `succeeded` or `blocked`. Inputs reject unknown fields including actor/policy/root claims. Captions are 1–8192 characters, rationale at most 2048, idempotency keys 8–128 safe ASCII characters. Request bodies are at most 256 KiB, including chunked requests. Errors are `{detail:{code}}`, using 401 invalid/expired/revoked credential, 403 authority/presentation denial, 404 unavailable/inaccessible ID, 409 stale revision/hash/policy/storage conflict, and 422 closed-schema validation. IDs convey no authority. GET detail uses DB metadata only and causes no writes or source census.

## Durable execution and frozen export

SQLite `BEGIN IMMEDIATE` serializes admission and draft mutations. Materialization first commits one idempotent operation, then does bounded CPU work; an interrupted admission can be retried with its identical key/payload. A changed draft rejects stale work. Cache identity includes source bytes and exact transform configuration. Materialization failure remains a blocked operation receipt and source status. This slice does not register generic ProcessingTask resume factories or external outboxes: there are no external jobs to reconcile.

Export admission snapshots exact membership, source/artifact IDs/hashes, caption text/UTF-8 hash/revision, review grant, split/group, subject and policy into immutable operation data. No text normalization/newline is added to sidecars. All training sources must be materialized and accepted. Known capture groups, exact source duplicates and normalized-pixel duplicates may not cross train/holdout. Registered holdouts with unsupported/unreadable pixels conservatively block freezing. Late known conflicts block publication. This is not a learned near-duplicate/session discovery feature and does not establish complete real-world split independence.

Workers read **only admitted caption bytes**, never latest caption rows. New caption drafts can proceed while an admitted version executes; they neither rewrite it nor reuse its approval for a new freeze. A later rejection of the admitted exact caption, grant revocation, changed source bytes, changed policy or late holdout conflict blocks publication/recovery. Review and source dependencies are rechecked before publication.

Cross-process advisory locks serialize one operation. Each retry uses a new attempt staging directory; prior crash debris stays private. Image/sidecar pairs, manifest and success marker are verified, fsynced, and atomically directory-published with Linux `renameat2(RENAME_NOREPLACE)`. This is byte-copy-only publication: no decode or encoding after review. The receipt commits separately. A crash after directory publication but before DB success reopens and verifies that same directory on resume; a missing/corrupt completed inventory never becomes success. The journal records admission and each attempt/publication event. Resume retries the same immutable logical operation, **not** a new user plan/successor job; there is no generic background worker lease or cancel API in this bounded synchronous slice. Old staging directories are retained for separately authorized cleanup. Repeated identical idempotency keys return one logical version; different payloads conflict.

A returned successful receipt proves exact fixture inventory/hash verification. It does not prove training suitability or model quality. [Trainer preprocessing proof](trainer-preprocessing.md) separately specifies the pinned downstream geometry behavior; ordinary training tensor preprocessing is not a byte-preservation promise. No `REQUESTED` training marker is written.

## Migration, rollback and acceptance

Eight new tables are additive and have no FK to legacy Media/Person/Annotation rows. No live database or disk is scanned/backfilled. Alembic upgrade/downgrade testing uses a temporary SQLite database; downgrade refuses any populated curation tables to prevent silent provenance loss. Rollback is disabling the environment flag and retaining records/output. Production deployment/migration and private-volume registration remain unauthorized.

The fixture helper snapshots a **test configuration** preset and resolved assignment threshold/margin, rejects either below 0.62/0.05 including indirect `loose` resolution, and records matching/video/outlier computation disabled. No live thresholds are read or changed. No face inference or automatic subject matching runs.

Acceptance requires focused authority/path/hash/recovery tests, all repository gates reported against baseline, and an isolated real-browser fixture demonstration. Neither fixture reviewer success nor isolated loopback browser proof is a production-container release proof. Remaining future scope: still-unsupported formats (AVIF and everything outside the table above), automatic or unattended frame selection, frame mining, audio, identity/duplicate models, repairs and every other generative derivative, external workers, MCP, operational retention and production deployment. The `rgb-png-v2` media slice adds explicitly requested frames and disclosed colour handling; it does not add any automatic discovery, any model, or any generative processing.
