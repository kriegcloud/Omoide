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
