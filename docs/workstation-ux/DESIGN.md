# Workstation UX programme — design contracts

Source request: `Omoide Feature Requests & Changes.md` (Obsidian vault). This
document fixes the data model and API contracts for every phase so that
implementation can proceed phase by phase, each on its own branch stacked on
the previous one.

Stack recap: FastAPI + SQLModel (SQLite, Alembic migrations run on startup by
`app/database.py::run_migrations`), React 18 + MUI 7 + Vite + zustand. The
workstation deployment is Docker (`docker-compose.workstation.yml`), so
`settings.general.is_docker` is true and native OS dialogs are unavailable.

Conventions for every phase:

- Backend mutations are refused with 403 when
  `settings.general.presentation_mode` is set (match the existing pattern).
- New tables and columns ship with an Alembic revision under `alembic/versions`
  chained on the current head. SQLModel models in `app/models.py` are updated
  in the same commit.
- New backend modules must reach the workstation image. Phase 1 widens
  `Dockerfile.workstation` to copy the whole `app/` and `alembic/` trees plus
  `alembic.ini` instead of a hand-picked file list.
- Frontend list pages that should survive browser back navigation use
  `useListStore` (zustand) with a stable `listKey`; `fetchInitial` is a no-op
  when the list is already populated, which is what restores scroll.
- Tests: backend uses `unittest` under `tests/` (run with
  `.venv/bin/python -m unittest discover -s tests`). Frontend has no test
  runner; verification is `npm run build` + `npm run lint` + browser QA.

---

## Phase 1 — people grid: scroll restore + bulk merge  (branch `feat/people-grid-ux`)

### Scroll restore

`PeoplePage` currently uses `useInfinite` (component-local state), so the list
refetches on every mount and the browser back gesture lands at the top. Port it
to `useListStore` with `listKey = "people-grid"`. Keep the existing
`useTaskCompletionVersion(["process_media","cluster_persons"])` refresh, but
turn it into the same "new items available" chip pattern used by
`MediaListPage` rather than silently clearing the cached list. `ScrollToTop`
already skips `POP` navigations, so no change is needed there.

Delete `hooks/useInfinite.ts` once nothing imports it.

### Bulk merge

Backend already exposes `POST /api/person/{target_id}/merge-multiple` with
body `{ source_ids: number[] }` returning `{ merged_ids, skipped_ids }`.

Frontend:

- In the people page selection toolbar add **Merge Selected** (enabled when
  2 or more selected).
- Opens `MergePeopleDialog`:
  - Lists the selected people as candidate targets (avatar, name,
    appearance count), default-selected = the one with the highest
    `appearance_count`.
  - A search field (uses `searchPersonsByName`) lets the user pick any other
    person as the target instead.
  - Confirm merges every other selected person into the target, then removes
    the sources from the cached list, patches the target's count in the list,
    and shows a snackbar.
- Extract the per-person selection state in `PeoplePage` into a small hook
  `usePeopleSelection` so Phase 2 and Phase 4 can reuse it.

### Dockerfile widening

Replace the per-file `COPY` lines in `Dockerfile.workstation` with:

```
COPY --chown=appuser:appgroup app/ /app/app/
COPY --chown=appuser:appgroup alembic/ /app/alembic/
COPY --chown=appuser:appgroup alembic.ini /app/alembic.ini
COPY --chown=appuser:appgroup scripts/ /app/scripts/
```

Keep the frontend builder stage as is. Make sure `.dockerignore` does not
exclude `alembic/`.

---

## Phase 2 — hide people  (branch `feat/hide-people`)

The request calls this "black list"; the UI label is **Hide** because a
`Blacklist` table for media paths already exists.

### Schema

`Person.hidden_at: datetime | None` (indexed). `NULL` = visible.
Migration `add_person_hidden_at`.

### API

| Method | Path | Body | Result |
| --- | --- | --- | --- |
| GET | `/api/person/?hidden=false` (default) | | existing cursor page, hidden excluded |
| GET | `/api/person/?hidden=true` | | cursor page of hidden people only |
| POST | `/api/person/{id}/hide` | | `PersonDetail` |
| POST | `/api/person/{id}/unhide` | | `PersonDetail` |
| POST | `/api/person/bulk-hide` | `{ person_ids: number[] }` | `{ hidden_ids, skipped_ids }` |
| POST | `/api/person/bulk-unhide` | `{ person_ids: number[] }` | `{ unhidden_ids, skipped_ids }` |

`PersonDetail`, `PersonRead`, `PersonReadSimple` gain `hidden_at`.

### Where hidden people are excluded

Add `Person.hidden_at.is_(None)` to: `list_persons`, `get_all_persons_simple`,
`search_people` (`app/api/search.py`), `get_similarities` (SQL: `AND p.hidden_at
IS NULL`), the relationship graph builder (hidden nodes and their edges are
dropped unless the root itself is hidden), the co-appearance graph endpoints,
and the person suggestions in `suggest-faces`. `stats` counts of people exclude
hidden. Clustering (`app/tasks/person_clustering.py`) must keep assigning new
faces to hidden people (that is the point), so it does NOT filter.

Hidden people still appear on a media item's People tab and on their own
`/person/:id` page (with a "Hidden" chip and an **Unhide** button).

### Frontend

- `PersonHero`: **Hide** button (outlined, warning colour) between Auto
  Profile and Delete; becomes **Unhide** when hidden.
- People page toolbar: **Hide Selected** next to Delete Selected.
- New route `/people/hidden` → `HiddenPeoplePage` reusing the people grid with
  `hidden=true`, toolbar offering **Unhide Selected** and **Delete Selected**.
- Sidebar: "Hidden People" entry under the People section with a
  `VisibilityOff` icon.

---

## Phase 3 — media card actions + bulk attach  (branch `feat/media-card-actions`)

### Backend

| Method | Path | Body | Notes |
| --- | --- | --- | --- |
| POST | `/api/media/{id}/move` | `{ destination_dir: string }` | destination must resolve inside a writable media root (`settings.general.resolved_media_dirs()` with `read_only` false). Uses `os.replace` when same device, else copy+verify+unlink. Updates `Media.path`; keeps `filename`, thumbnails, faces, links. 409 if target exists. |
| POST | `/api/media/{id}/rename` | `{ filename: string }` | basename only, no separators, keeps extension unless supplied. Updates `Media.path` and `Media.filename`. 409 on collision. |
| POST | `/api/media/bulk-move` | `{ media_ids: number[], destination_dir }` | returns `{ moved_ids, skipped: [{id, reason}] }` |
| POST | `/api/person/{id}/media/bulk` | `{ media_ids: number[] }` | manual attach (PersonMediaLink) for each; skips media where the person already has a detected face; returns `{ added_ids, skipped_ids }`; recalculates appearance counts once. |
| POST | `/api/person/{id}/media/bulk-detach` | `{ media_ids: number[] }` | mirrors `detach` per media. |
| POST | `/api/person/{id}/media/{media_id}/reassign` | `{ target_person_id }` | moves every face of `person_id` in that media to the target, or moves the manual link if no faces. Uses the same embedding update path as `assign_faces`. |

Move/rename refuse (409) when the media root is read-only, and refuse when
`presentation_mode` is on. They also refuse when the file is currently missing
(`missing_since` set).

The existing `/api/media/folders` listing powers the in-app folder picker.
Add `?include_empty=true` support if the current implementation hides folders
without media, so users can move into empty directories.

### Frontend

`MediaCard` gets a vertical-ellipsis `IconButton` top-right (replaces the heart
position). Menu items:

- Favorite / Unfavorite (heart icon). When favorited, a passive filled heart
  badge still renders at top-left of the overlay, non-interactive.
- Edit… (disabled with tooltip "Coming soon" until Phase 6 wires it)
- Rename…
- Move to folder…  → `FolderPickerDialog` (tree browser over `/api/media/folders`, breadcrumbs, "New folder" text field)
- Open in default application (disabled in Docker with tooltip "Not available in the Docker deployment")
- Assign to person… (only when `personContext` prop is passed; opens person search; calls `reassign`)
- Delete record… / Delete file… (confirm dialogs; same copy as detail page)

The menu never opens the lightbox; stop propagation on the button.

Media detail page gets the same menu in its header.

Select-mode action bar (`SelectionActionBar`) gains **Attach to person**
(person search dialog → `bulk`) and **Move to folder**. On `/person/:id` the
action bar additionally shows **Detach from this person**.

---

## Phase 4 — drag (marquee) selection on every grid  (branch `feat/drag-select`)

Generic hook `useMarqueeSelection<TId>({ containerRef, itemSelector,
getId, enabled, onSelectionChange })`:

- Pointer-down on the container background (or on an item while `enabled`)
  starts a rubber-band rectangle; items whose bounding box intersects the
  rectangle become selected (additive with Ctrl/Cmd, replacing otherwise).
- Shift-click selects the range between the anchor item and the clicked item
  in DOM order.
- `enabled` is true only while the relevant select mode is on, so the existing
  HTML5 file drag on media cards keeps working outside select mode.
- Auto-scrolls the window when the pointer nears the viewport edge.

Selection stores:

- Media grids keep using `SelectionContext` (global).
- People, albums, events, tags get a page-local `useEntitySelection` (same
  shape as the media one, generic over id).

Bulk actions added where none exist: albums (delete), events (delete: add
`DELETE /api/events/{id}` + `POST /api/events/bulk-delete`), tags (delete via
existing `DELETE /api/tags/{id}` looped, plus `POST /api/tags/bulk-delete`).

---

## Phase 5 — gender (and age) prediction  (branch `feat/gender-tags`)

`Person.gender` and `Person.age` columns already exist (nullable, unused).

### Schema

- `Face.sex: str | None` (`"F"`/`"M"`), `Face.sex_score: float | None`
  (0..1 confidence of the predicted class), `Face.age: int | None`.
- `Person.gender_confidence: float | None`, `Person.gender_manual: bool`
  (default false; when true the aggregate never overwrites `gender`).
- Migration `add_face_and_person_demographics`.

### Processing

- `app/processors/faces.py`: add `"genderage"` to `allowed_modules` for the
  insightface `FaceAnalysis`. Store `face.sex`, `face.age`, and a confidence
  derived from the genderage logits when available (otherwise `None`).
- The AdaFace socket backend only replaces embeddings; genderage keeps
  running on the CPU insightface path.
- Aggregation `update_person_demographics(session, person_ids)`: majority vote
  over faces weighted by `det_score`, confidence = weighted share of the
  winning class; median age. Called from every place that calls
  `recalculate_person_appearance_counts`.
- Backfill task `backfill_demographics` (Tasks & Processing panel entry) runs
  genderage over stored originals using each face's `bbox` for faces where
  `sex IS NULL`.
- Tag mirror: system tags `Female` / `Male` attached via `PersonTagLink`
  when confidence ≥ 0.65; removed when the aggregate changes.

### API / UI

- `PATCH /api/person/{id}` accepts `gender` (sets `gender_manual=true`) and
  `gender: null` to clear the manual override.
- `PersonHero` shows a chip "Female · 92%" with a menu: Female / Male /
  Clear override. Age shown as a secondary caption when present.
- People page filter: All / Female / Male (query param `gender`).

---

## Phase 6 — image editor  (branch `feat/image-editor`)

Library choice follows the Grok research lane (`grok-editor-research.md`).
Working default: `react-filerobot-image-editor`.

### Contract

The browser never uploads pixels. It sends an operation list:

```ts
type EditOp =
  | { op: "rotate"; degrees: 90 | 180 | 270 }
  | { op: "flip"; axis: "horizontal" | "vertical" }
  | { op: "crop"; x: number; y: number; width: number; height: number }   // in source pixels, after prior ops
  | { op: "resize"; width: number; height: number }
  | { op: "adjust"; brightness?: number; contrast?: number; saturation?: number } // -100..100
```

`POST /api/media/{id}/edit` body `{ ops: EditOp[], mode: "copy" | "overwrite" }`.

- `copy` (default): writes `<stem>_edited[-N].<ext>` beside the original,
  preserving EXIF (via `piexif` for JPEG), registers a new `Media` row and
  queues processing for it. Returns the new `MediaDetail`.
- `overwrite`: writes to a temp file, atomically replaces the original,
  regenerates thumbnail and phash, deletes existing faces/embeddings for the
  media, and queues the processors again. Returns the updated `MediaDetail`.
  Faces re-detected afterwards are re-matched through the existing
  suggestion flow; manual `PersonMediaLink`s are kept.

Pillow applies ops in order; rotate uses lossless `Image.transpose`.
Videos are rejected with 400.

### UI

`/medium/:id` gets an **Edit** action opening a full-screen dialog hosting the
editor with: crop presets (Free, Current image, Square, 16:9, 4:3, 3:2, 7:5,
5:4, 9:16, 3:4, 2:3, 5:7, 4:5), rotate left/right, flip H/V, resize, adjust,
undo, reset, zoom fit/fill/percent. Save button offers "Save copy" and
"Overwrite original" (the latter with a warning about face re-detection).

---

## Phase 7 — social links  (branch `feat/social-links`)

### Schema

```
PersonSocialLink(id, person_id FK, platform str, handle str, url str, created_at)
UNIQUE(person_id, platform, handle)
```

`platform` from a fixed set: instagram, tiktok, x, youtube, onlyfans, threads,
facebook, snapchat, other.

### API

| Method | Path |
| --- | --- |
| GET | `/api/person/{id}/social-links` |
| POST | `/api/person/{id}/social-links` `{ platform, handle, url? }` (url derived from platform+handle when omitted) |
| DELETE | `/api/person/{id}/social-links/{link_id}` |
| GET | `/api/person/{id}/social-links/suggestions` → handles derived from the folder names of the person's media (e.g. a folder named after an account) |

### UI

`PersonHero` shows platform icons with the handle; clicking opens the profile
in a new tab. An "Add link" popover with platform select and handle field.
Suggestions render as chips the user can accept.

---
---

# Part II — LoRA dataset workbench

Goal: turn a curated person library into training-ready character LoRA
datasets without leaving Omoide. Captioning itself is being built in a
parallel effort on top of `MediaAnnotation` (kind `caption`/`tags`); the phases
below only *consume* captions and must not modify the annotation modules,
the ComfyUI bridge protocol, or `integrations/comfyui/*` except where Phase 11
says so.

Shared conventions (in addition to Part I):

- Everything a dataset needs is stored relative to media ids plus op lists.
  Files are only materialised on export.
- Exports and training runs live under `settings.general.datasets_dir`
  (default `<data dir>/datasets`, i.e. `/app/data/datasets` in the workstation
  container). This directory is outside every media root so scans never index
  exported copies. `OMOIDE_GENERAL__DATASETS_HOST_ROOT` (optional) is the host
  path shown in the UI and written into generated training configs.
- Face `bbox` is `[x, y, w, h]` in the detector's working space, which is the
  image downscaled so its longest side is at most 1280 px (`MAX_DET_DIM` in
  `app/processors/faces.py`). Convert to source pixels with
  `scale = max(width, height) / min(max(width, height), 1280)`.
- Long-running work uses the existing `ProcessingTask` machinery
  (`app/tasks/common.py`, `set_task_progress`, cancellation).

## Phase 8 — Training datasets and export  (branch `feat/training-datasets`)

### Schema

```
TrainingDataset
  id, name, slug (unique, derived from name, editable), description,
  kind: "subject" | "regularization"        (default subject)
  person_id FK person nullable              (subject datasets usually have one)
  trigger_word str                          (e.g. "sydsch woman" → trigger "sydsch")
  class_token str                           (e.g. "woman")
  caption_source: "annotation" | "template" | "none"   (default annotation)
  caption_template str  default "{trigger} {class}, {caption}"
  target_resolution int default 1024
  buckets json list[int] default [512, 768, 1024]
  repeats int default 10
  export_layout: "kohya" | "ai_toolkit" | "onetrainer"  default ai_toolkit
  cover_media_id FK media nullable
  created_at, updated_at

DatasetItem
  id, dataset_id FK (cascade), media_id FK, position int,
  edit_ops json nullable        (EditOp[] from Phase 6, a *virtual* crop/rotate)
  edit_design_state json nullable
  caption_override str nullable
  weight float default 1.0
  excluded bool default false
  created_at
  UNIQUE(dataset_id, media_id)

DatasetExport
  id, dataset_id FK, layout, status: pending|running|completed|failed|cancelled,
  task_id str nullable, output_dir str, item_count int, manifest json nullable,
  error str nullable, created_at, finished_at
```

Migration `add_training_datasets` chained on `f26ceee70778`.

### Effective caption

`resolve_caption(dataset, item, media, person)`:

1. `item.caption_override` if set.
2. If `caption_source == "annotation"`: latest `MediaAnnotation` with
   `kind == caption`, preferring `review_status` approved over pending,
   highest `revision`. Its text has every occurrence of the person's display
   name (case-insensitive, also the slug form) replaced by the trigger word.
3. Apply `caption_template` with `{trigger}`, `{class}`, `{caption}`
   (empty caption collapses the trailing separator).
4. `caption_source == "none"` → no `.txt` written.

### Export layouts

Output root: `<datasets_dir>/<slug>/<YYYYmmdd-HHMMSS>/`.

- **ai_toolkit**: `dataset/<index:04d>_<media id>.<ext>` + `.txt`, plus
  `config.yaml` generated from `app/templates/ai_toolkit_lora.yaml` (a trimmed
  copy of ai-toolkit's `train_lora_flux_24gb.yaml` with folder_path,
  trigger_word, resolution buckets and sample prompts filled in) and a
  `README.md` with the launch command.
- **kohya**: `img/<repeats>_<trigger> <class>/` + `.txt`; `reg/` when a
  regularization dataset is linked (Phase 9).
- **onetrainer**: flat `images/` + `.txt`; masks in `masks/` when present
  (Phase 11).

For each non-excluded item, in `position` order: open original, apply
`edit_ops` with `apply_edit_ops`, downscale so the longest side equals the
largest bucket not exceeding the source (never upscale), convert to RGB JPEG
quality 95 unless the source is PNG, strip EXIF, write. `manifest.json` lists
`{ index, media_id, source_path, source_sha256, output_file, output_sha256,
width, height, ops, caption }` plus dataset settings and the git revision of
the app. The export runs as a task with progress; the `DatasetExport` row
mirrors its status.

### API

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/api/datasets` | list with counts and cover thumbnail |
| POST | `/api/datasets` | create; body = editable fields; `person_id` prefill copies name → trigger slug and gender tag → class token |
| GET/PATCH/DELETE | `/api/datasets/{id}` | |
| POST | `/api/datasets/{id}/items` | `{ media_ids }` → `{ added_ids, skipped_ids }` (skips duplicates and videos) |
| DELETE | `/api/datasets/{id}/items` | `{ media_ids }` |
| PATCH | `/api/datasets/{id}/items/{item_id}` | `caption_override`, `edit_ops`, `edit_design_state`, `weight`, `excluded`, `position` |
| GET | `/api/datasets/{id}/items?cursor&limit&include_excluded` | items joined with `MediaPreview`, `effective_caption`, `has_ops`, and the person's face summary for that media (`det_score`, `frontality`, `face_count`) |
| POST | `/api/datasets/{id}/export` | `{ layout? }` → `DatasetExport` (task started) |
| GET | `/api/datasets/{id}/exports` | history |
| GET | `/api/datasets/exports/{export_id}/manifest` | manifest JSON |
| POST | `/api/datasets/from-person/{person_id}` | create dataset and add every appearance (faces + manual links) |

All mutations 403 in presentation mode.

### Frontend

- Sidebar: new **Training** section with **Datasets** (`/datasets`).
- `DatasetsPage`: card grid (cover, name, item count, trigger word, last
  export), New dataset dialog.
- `DatasetDetailPage` (`/dataset/:id`): header with editable settings
  (name, trigger, class, caption source/template, resolution, buckets,
  repeats, layout), tabs **Items** (masonry of `MediaCard` with a dataset
  context: caption preview line, quality chips, excluded dimming, an item
  menu with Exclude/Include, Edit caption, Edit crop (opens the Phase 6
  editor in *virtual* mode: saves ops to the item instead of writing a file),
  Remove) and **Exports** (list with status, item count, output path,
  manifest download, and the launch command for ai_toolkit).
- Select mode on the dataset grid reuses the marquee hook; bulk Exclude /
  Include / Remove.
- `SelectionActionBar` (any media grid): **Add to dataset** (picker with
  "New dataset…"). `MediaCardMenu`: **Add to dataset…**.
- `PersonHero`: **Create dataset** button (calls from-person).

## Phase 9 — Curation assistant  (branch `feat/dataset-curation`)

### Metrics

`app/services/curation.py` computes per (dataset, item):

- `face_ratio`: subject face area / image area (subject = dataset person's
  face in that media; falls back to the largest face).
- `framing`: closeup (≥ 0.12), portrait (0.04–0.12), half_body (0.012–0.04),
  full_body (< 0.012), none.
- `other_people`: faces in the media not assigned to the subject.
- `frontality`, `det_score`, `sharpness` (`Media.laplacian_score`),
  `brightness_mean` (computed from the thumbnail, cached in a new
  `MediaCurationStats(media_id PK, brightness_mean, contrast_std, computed_at)`).
- `aspect`: portrait / square / landscape.
- `identity_distance`: cosine distance between the subject face embedding
  and the person centroid (`get_person_embedding`), via `face_embeddings`.
- `duplicate_group`: phash Hamming distance ≤ 6 within the dataset, refined
  by face-embedding distance < 0.25; each group gets a `best_item_id`
  (highest sharpness × det_score × resolution).

### API

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/api/datasets/{id}/analysis` | `{ items: [...metrics], summary: { framing, aspect, sharpness_hist, frontality_hist, brightness_hist, other_people_hist }, outliers: [item ids with identity_distance > 0.55], duplicates: [{ item_ids, best_item_id }] }` |
| POST | `/api/datasets/{id}/auto-select` | `{ target_count, min_frontality?, min_sharpness?, max_other_people?, drop_duplicates: true, dry_run }` → farthest-point sampling over (face embedding ⊕ framing ⊕ aspect ⊕ brightness) starting from the best-quality item; non-selected items become `excluded` unless `dry_run` |
| POST | `/api/datasets/{id}/regularization` | `{ target_count, gender?, exclude_person_ids? }` → creates a `regularization` dataset from other visible people with the matching gender tag, one face only, deduped, and links it as `regularization_dataset_id` on the subject dataset (new nullable FK) |

### Frontend

Dataset detail gains **Analysis**: summary bars, an outliers strip with one-click
exclude, duplicate groups with "keep best", and the Auto-select dialog with a
preview of what would be excluded. The item grid shows metric chips and lets
you sort by any metric.

## Phase 10 — Face-anchored crops  (branch `feat/face-crops`)

`app/services/face_crops.py::suggest_crop(bbox_px, image_w, image_h, framing,
aspect)` where framing ∈ {closeup, portrait, half_body, full_body} maps to a
box of `k × face height` above/below the face (1.3/2.2, 2.2/4.5, 3/8, 4/14)
centred on the face, clamped to the image and then expanded/shrunk to the
requested aspect (`1:1`, `2:3`, `3:4`, `4:5`, `9:16`, `free`). Returns a
Phase 6 `crop` op in source pixels plus the resulting bucket resolution.

| Method | Path |
| --- | --- |
| GET | `/api/media/{id}/face-crops?person_id&framing&aspect` → suggestions per face |
| POST | `/api/datasets/{id}/items/batch-crop` `{ item_ids?, framing, aspect, overwrite_existing_ops }` → sets `edit_ops` on items |
| POST | `/api/media/batch-edit` `{ media_ids, ops, mode }` → runs Phase 6 edits for many media as a task (copies) |

Frontend: **Batch crop** dialog on the dataset detail (framing + aspect,
preview of the first 12 with the crop rectangle drawn over the thumbnail,
Apply). In the editor: a **Face guides** toggle drawing face boxes, and
framing preset buttons that set Filerobot's crop through `updateStateFnRef`
(`{ adjustments: { crop: {...shown-space box} } }`).

## Phase 11 — Image repair through the ComfyUI bridge  (branch `feat/image-repair`)

Additive bridge change, coordinated with the captioning work: a profile
`result_kind: "image"` whose `output_node_class` is `SaveImage`; the bridge
returns `{ image_path }` of the produced file inside its staging directory and
the app copies it out. Client method `ComfyAnnotationClient.repair(...)`
shares the request plumbing with `annotate`. Profiles expected on the host
(workflow JSONs are authored in ComfyUI, not in this repo; ship
`integrations/comfyui/profiles/README.md` describing each contract):

- `omoide-remove-text-v1`: OCR text regions → mask → inpaint (LaMa).
- `omoide-upscale-v1`: 2× restoration (SUPIR or CodeFormer + ESRGAN).
- `omoide-remove-people-v1`: input image + subject face box (as a JSON input
  node) → segment non-subject people → inpaint; also returns the mask.

App: `ImageRepairJob(id, media_id, profile, params json, status, attempt
tracking like annotations, result_media_id, mask_path, error)`. Results are
written next to the original as `<stem>_repaired-<profile>.<ext>` via the
Phase 6 writer, registered as media, processing queued. Endpoints
`POST /api/media/{id}/repair`, `POST /api/media/bulk-repair`, `GET /api/repairs`.
UI: card menu **Repair ▸ Remove overlays / Upscale / Remove other people**,
bulk action on datasets and selections, and a before/after slider on the
media detail for repaired copies. Tests use a fake bridge.

## Phase 12 — Editor polish  (branch `feat/editor-polish`)

- Shortcuts inside the dialog: `R`/`Shift+R` rotate, `H`/`V` flip, `C` crop
  tool, `0` fit, `Esc` cancel, `Ctrl/Cmd+S` save copy, `Ctrl/Cmd+Shift+S`
  overwrite (still confirms).
- Hold `\`` (backtick) to compare with the original (Filerobot has the
  toggle; bind it).
- **Apply last edit to selection**: the last saved op list is kept in a
  zustand store; `SelectionActionBar` offers it and calls `/api/media/batch-edit`.
- Face guides and framing presets from Phase 10 live here in the editor UI.
- Aspect-bucket guide overlay (512/768/1024 boxes) toggle.

## Phase 13 — Training runs  (branch `feat/training-runs`)

The container cannot run training; the host does, via a path-activated
systemd user unit that watches `<datasets host root>/**/runs/*/REQUESTED`.

```
TrainingRun
  id, dataset_id FK, export_id FK, backend "ai_toolkit",
  status: requested|running|completed|failed|cancelled,
  run_dir, config_yaml text, steps int, started_at, finished_at,
  last_sample_step int, error
```

- `POST /api/datasets/{id}/train` `{ export_id?, steps, lr, rank, sample_prompts[] }`
  writes `<export>/runs/<timestamp>/config.yaml` (ai-toolkit format),
  `REQUESTED`, and returns the run.
- `packaging/omoide-train-launcher` + `omoide-train@.path/.service` (user
  units): on `REQUESTED`, runs ai-toolkit (`python run.py config.yaml`) in the
  user's ai-toolkit checkout with ROCm env, streaming `status.json`
  (`{ status, step, total, loss, updated_at }`) and copying sample grids into
  `samples/`. Writes `DONE` or `FAILED`.
- A periodic app task reconciles `status.json` into `TrainingRun` and lists
  `samples/` as `TrainingSample(run_id, step, path)` served from a new static
  route under the data dir.
- UI: **Runs** tab on the dataset detail with progress, loss sparkline, sample
  gallery per step, and the resulting `.safetensors` path.
