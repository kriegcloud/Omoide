# Omoide — agent operating guide

Fork of kriegcloud/Omoide. FastAPI + SQLModel on SQLite with sqlite-vec; React 18 +
MUI 7 + Vite frontend. Runs on this workstation as the `omoide` container from
`docker-compose.workstation.yml`, serving `http://127.0.0.1:8123`. The library it
indexes is personal and partly explicit: prefer `read_page`/JS over screenshots
when driving the browser, and scale screenshots down (0.2–0.5) if you must take one.

## Hard rules

- Never modify `app/annotation_*.py`, `app/api/annotations.py`,
  `app/schemas/annotation.py`, `app/services/comfy_annotation.py`.
- Never push to `upstream` (EinAeffchen/Omoide). Push only to `origin`.
- Never print `HF_TOKEN`, `.env` contents, or any secret.
- ComfyUI restarts only via the comfy-mcp `restart_comfyui` tool.
- Do not restart or rebuild the container unless the task calls for a deploy.
  Other sessions share this checkout and the container. Check
  `GET /api/tasks/` for running tasks before any restart.
- Do not lower `face_recognition.existing_person_cosine_threshold` (0.62) or the
  other matching thresholds without Benjamin's explicit approval.
- Work in a linked worktree (`../Omoide-ux*`), stage by path, merge to `master`.
  `.venv` in a worktree is a symlink to the main checkout's; run `npm ci` in the
  worktree's `frontend/` if `node_modules` is missing.

## Verify before merging

```bash
.venv/bin/python -m unittest discover -s tests        # 582 OK as of 2026-09-10
cd frontend && npm run build                          # must be green
cd frontend && npx eslint src --ext .ts,.tsx          # baseline 54 problems; add none
.venv/bin/alembic heads                               # exactly one head (3d4e5f607182)
node --test-isolation=none --test frontend/tests/selection.test.cjs   # 30 node tests, no npm deps
for f in frontend/src/hotkeys/tests/*.test.cjs; do node "$f" || break; done   # 72 node tests
```

Audit-driven fixes follow "failing test first": reproduce the claim with a test,
then fix; a claim that does not reproduce is reported as such, never patched blind.

Tests, build and lint are necessary, not sufficient. UI and API changes get proven
in the real browser against the running container before they count as done.

## Deploy

Order matters: the container bind-mounts `${HOST_DATA_DIR}/static` read-only over
`/app/static`, which shadows the image's copy of the frontend.

```bash
scripts/deploy-frontend.sh                                    # build + rsync + live bundle check; no restart
docker compose -f docker-compose.workstation.yml up -d --build   # only for backend changes
```

Alembic migrations run at container boot. Confirm with
`docker exec omoide sh -c 'cd /app && alembic current'`.

Config edits: `POST /api/config/` writes `config.yaml` only; follow it with
`POST /api/config/reload` or the running process keeps the old values. Diff the
`GET /api/config/` payload before and after to prove only the intended key changed.

## Tasks

- `ProcessingTask.params` stores each task's inputs. Types registered in
  `app/tasks/resume.py` are resumable. Startup/shutdown mark running resumable
  tasks `interrupted` (others `cancelled`). `POST /api/tasks/{id}/resume` creates a
  successor linked through `params.resumed_from` and `resumed_by`.
- `scan.auto_resume_interrupted_tasks` is ON for this workstation: interrupted
  tasks restart after a rebuild; explicitly cancelled tasks do not. Tasks created
  before the `params` column exist cannot be resumed; start a fresh run instead.

## Audit trail

- Every Person deletion logs one line: `person deleted id= name= reason=
  appearance_count=` (see `app/utils.py::log_person_deleted`). Reasons include
  `delete`, `bulk-delete`, `merge-source`, `empty-after-<operation>`,
  `reset-clustering`, `reset-processing`.
- Every write to `Face.person_id` stamps `assigned_at` and `assignment_source`
  (`manual`, `suggestion`, `undo`, `auto_match`, `cluster`, `merge`, `detach`,
  `reset`) through `app/services/face_provenance.py`. New write paths must use the
  helper. Rows older than the migration are NULL.
- `GET /api/faces/assignments/recent?limit=&source=&person_id=` lists the latest
  changes. Use it to find and revert anything a QA run assigned.
- Undo in the UI is a single slot with an 8 s window (`UndoContext`). Run
  action → check → undo in one script when proving a flow.

## Conventions added in round 6

- **Missing files** are detect / hide / review: the "Detect Missing Files" task only
  flags (`missing_since`); every browse query goes through `exclude_missing()` in
  `app/api/_media_filters.py`; removal happens only from the Missing Files page or
  grace-based auto-cleanup. `delete_record` must clean every media FK without an
  `ON DELETE` (the FK sweep test enumerates them).
- **Folder scoping** uses the stored `media.folder` column and the shared predicate in
  `app/api/_media_filters.py` (LIKE wildcards escaped, separator boundary). Pages keep
  the folder in the `folder` URL param.
- **Hotkeys** are declared data registered through `frontend/src/hotkeys/useHotkey`
  (scopes global < page < dialog, shared typing guard). `?` opens the help overlay,
  which is generated from the registry, so a new action needs a binding, not docs.
  Destructive keys always go through `ConfirmDialog` (Confirm autofocused, Enter
  confirms, target id snapshotted at open).
- **Selection**: tiles render through `SelectableTileFrame`; checkbox clicks carry the
  real modifier keys; double-click, the hover Open button and Enter open a tile
  without leaving select mode; marquee auto-scroll is instant-scroll with eased
  velocity against the nearest scrollport. Keep those behaviours in the frame, not
  in pages.
- **Face ownership** is claimed, never overwritten: background matching and clustering
  update only rows whose `person_id IS NULL` and stamp provenance for claimed ids.
- **Presentation mode** is enforced by one app-level dependency on every mutating
  `/api` route; new routers inherit it.
- `/api/health` returns 503 until migrations and startup finish; unknown `/api/*`
  paths return JSON 404, never the SPA page.
- `scripts/deploy-frontend.sh` keeps previous hashed assets (`ASSET_RETENTION_DAYS`)
  so open tabs can still lazy-load; it verifies every asset the new index references.

## Reading the database

```bash
docker exec -i omoide python - <<'EOF'
import sqlite3
c = sqlite3.connect("file:/app/data/database/omoide.db?mode=ro", uri=True)
print(c.execute("select count(*) from face where person_id is null").fetchone())
EOF
```

Load `sqlite_vec` in that script when touching `face_embeddings`. The `face`
table has no creation timestamp; `assigned_at` is the only time on it.

## Delegating to Codex

Heavy implementation goes to Codex lanes, one per worktree branch:

```bash
codex exec --model gpt-6-astra -c 'model_reasoning_effort="xhigh"' \
  -s workspace-write --cd ../Omoide-ux "$(cat prompt.md)" </dev/null
```

- Launch detached (`setsid nohup … &`) and watch the log for an exit marker; a
  Bash tool timeout kills backgrounded lanes.
- The sandbox cannot bind ports, commit, or `npm install`, and Starlette's
  `TestClient` stalls in it. Have the lane write `LANE_REPORT.tmp.md`, then run
  the full verification yourself outside the sandbox and delete the report before
  committing.
- Give the lane a written contract (schema, endpoints, write paths to cover,
  tests to add, baselines) and tell it not to ask questions.

## Browser QA notes

- A hidden browser pane has zero layout: hover, click and `IntersectionObserver`
  do not fire. Dispatch `MouseEvent`s from JS and call `.click()` on elements.
- MUI popovers and snackbars render outside `<main>`; query
  `.MuiPopover-paper`, `.MuiSnackbar-root`, `[role=tooltip]` directly.
- `location.reload()` inside a JS eval aborts the eval. Navigate first, then run.
- Verify lazy-loaded lists through API cursors, not by scrolling the pane.

## Commits

```bash
git -c commit.gpgsign=false commit --no-gpg-sign -F msg.txt
```

Signing goes through the 1Password SSH agent, which needs GUI approval and hangs
agents. End every commit message with
`Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
