# Deploying production still curation on the workstation container

This runbook covers the workstation Docker deployment
(`docker-compose.workstation.yml`, `Dockerfile.workstation`). It was rehearsed
end to end against a read-only copy of a real 374 MB library database in a
separate compose project before any live change. Nothing here lowers a
verification threshold; the container observes the same filesystem identity the
host does.

## What the deployment adds

| Change | Why |
| --- | --- |
| `Dockerfile.workstation` installs the pinned passkey verifier set (`webauthn` and its lockfile-pinned dependencies) with `--no-deps` and fails the build if the set is incomplete | The base image lacks the verifier; without this, migrations apply and every enrollment then fails with a server error. |
| Compose binds `/run/udev` read-only at `/run/udev` | `findmnt` resolves a bind-mounted source's filesystem UUID through the udev database. Without it the container sees `uuid: null` and registration refuses every source. No device nodes are exposed. |
| Compose passes `OMOIDE_CURATION_MODE`, `OMOIDE_CURATION_RP_ID`, `OMOIDE_CURATION_ORIGIN` from `.env`, defaulting to `disabled` | Production review stays off unless the operator opts in per deployment. |
| `make backup` calls `scripts/backup_workstation_database.py` | The previous target passed one quoted string as the database filename and never produced a backup. |
| Operator CLI gains `verify` and `reattest` | See *Runtime identity* below. |
| Operator CLI derives the expected Alembic head instead of hard-coding it | A later additive curation migration would otherwise leave the CLI refusing a correctly migrated database. It still never migrates. |

## Origin and relying party

WebAuthn only accepts `http` for `localhost`. With the shipped compose file the
service listens on `127.0.0.1:${PORT}`, so the reviewer opens
`http://localhost:${PORT}/curation` and `.env` sets:

```sh
OMOIDE_CURATION_MODE=production
OMOIDE_CURATION_RP_ID=localhost
OMOIDE_CURATION_ORIGIN=http://localhost:8123
```

`127.0.0.1` in the address bar is a different origin and is rejected by design.
A tailnet or LAN origin needs HTTPS and a reverse proxy in front of the
container; that is a separate change and is not part of this runbook.

## Runtime identity: register inside the runtime, re-attest after restarts

Every read of a registered source re-observes the volume and compares it with
the identity pinned at registration: filesystem UUID, type, root, mountpoint
(persistent) plus kernel mount id, device, source path and mount namespace
(runtime). The container's mount id and namespace differ from the host's, so
**registration must run inside the container**:

```sh
docker compose exec -e OMOIDE_CURATION_MODE=production omoide \
  python scripts/curation-authority.py --database /app/data/database/omoide.db \
  register --manifest /app/data/<manifest>.json
```

Paths in the manifest are container paths (`/app/data/...`, `/app/media/T7/...`).
Both `source_root` and `store_root` must already exist as directories the
container user can open (registration walks them with `O_NOFOLLOW` and fails
closed with `storage_unavailable` otherwise); create the store root on the data
volume as uid 1000 before registering.
`expected_filesystem_uuid` is the value `findmnt -n -o UUID --target <path>`
prints inside the container; `expected_mountpoint` is the bind target.

A container restart or a re-plugged drive gives a new mount id and namespace.
Reads then fail closed with `source_volume_changed`; nothing relocates
silently. The operator checks and, if only runtime fields drifted, re-pins them:

```sh
docker compose exec -e OMOIDE_CURATION_MODE=production omoide \
  python scripts/curation-authority.py --database /app/data/database/omoide.db \
  verify --dataset-id <id>
docker compose exec -e OMOIDE_CURATION_MODE=production omoide \
  python scripts/curation-authority.py --database /app/data/database/omoide.db \
  reattest --dataset-id <id> --operator-id <you> --statement "container restarted <date>"
```

`verify` writes nothing. `reattest` refuses a different UUID, filesystem type,
root or mountpoint, refuses replaced root directories, re-reads every registered
file through fenced descriptors and requires every hash to match, appends an
audit record to the dataset policy, and increments the dataset revision so
in-flight reviews, materializations and exports conflict instead of continuing
against the old runtime identity.

Store roots must be on a POSIX filesystem (the data volume). Registration probes
the store for exclusive creation, an enforced `0400` mode and hard links and
refuses `store_root_unsupported_filesystem` otherwise (exFAT media drives fail
this on purpose).

## Data directory ownership

The container runs as `1000:1000` and the compose file adds nested read-only
binds. Docker creates missing mount-point directories as root, which then
breaks startup with `Permission denied` on `/app/data/.omoide/thumbnails`.
Before the first `up` with a new data directory, create every nested mount
point as the container user.

## Backup, migration, restore, rollback

All three curation migrations are additive (fourteen new tables, plus five
nullable or defaulted columns on `curation_operation`; no existing column is
altered) and applied in well under a second on the rehearsal copy. SQLite DDL is
non-transactional, so the backup is the rollback:

1. Stop the container: `docker compose down` (in-process tasks end; the `stop_grace_period` is two minutes).
2. Back up: `make backup` (or `python3 scripts/backup_workstation_database.py --data-dir "$HOST_DATA_DIR"`). It opens the source read-only, uses the SQLite backup API (WAL-safe), runs `quick_check`, fsyncs and prints the backup path. Record its SHA-256.
3. Build and start the new image: `docker compose up -d --build`. The container's command runs `alembic upgrade head`, then the application lifespan re-runs it idempotently. `GET /api/health` reports `"migrations": "718293a4b5c6"` when done.
4. Verify: `PRAGMA quick_check` on the live file, `SELECT count(*) FROM media` unchanged, `alembic_version` = `718293a4b5c6` (confirm with `alembic heads`, which must print exactly one head).
5. Rollback: `docker compose down`, copy the backup over `database/omoide.db` (remove any stale `-wal`/`-shm` next to it), start the previous image tag. The previous image does not know the new revision and fails loudly on a migrated database, which is why the file restore is the rollback rather than `alembic downgrade` (the downgrade refuses populated tables).

The frontend is served from `$HOST_DATA_DIR/static`, which shadows the copy in
the image; run `scripts/deploy-frontend.sh` once the container is up so the
curation pages exist on disk.

## Watching and recovering curation jobs

Materialize and export execution is a shared `ProcessingTask`
(`curation_materialize` / `curation_export`, `params.operation_id`), so admitted
operations appear in `GET /api/tasks/` with progress, and
`POST /api/tasks/{id}/cancel` stops one that has not published yet. After an
unclean container stop, startup reconciles uncertain operations before the
generic stale-task cleanup: an export whose directory already verifies is marked
succeeded and never re-run, anything else becomes resumable. With
`scan.auto_resume_interrupted_tasks` on (it is, on this workstation) those resume
automatically; otherwise use `POST /api/tasks/{id}/resume`. Full behaviour:
[task-execution.md](task-execution.md).

## Legacy review stamping while production authority is active

The legacy dataset triage and caption review routes that stamp human review are
denied with `legacy_human_authority_unavailable` in production mode. The
triage page shows a readable explanation and points to Still review; the
curation page carries the same notice. Agents keep proposal-only access.
