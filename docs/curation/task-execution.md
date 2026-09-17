# Curation operations as shared application jobs

This phase makes curation **materialize** and **export** execution a first-class
job in the same `ProcessingTask` system as scans and clustering. It changes only
*execution*: who may run an already-admitted operation, how far they got, how it
is stopped, and how an uncertain outcome is resolved after a crash. It does not
change admission, authority, review semantics, export membership or output bytes.

It supersedes the closing sentence of the durable-execution section in
[contracts.md](contracts.md) ("this slice does not register generic
ProcessingTask resume factories"). Everything else in
[contracts.md](contracts.md) and [production-authority.md](production-authority.md)
still holds — in particular a rejected still still blocks the export, and no
legacy HUMAN/approved/reviewed marker ever becomes acceptance.

## Immutable plans

Execution reads only `curation_operation.snapshot`, the membership, caption bytes,
hashes, split and policy captured at admission. It never reads the latest caption,
review or source rows to decide *what* to publish; it re-reads them only to decide
*whether* publication is still allowed (`_revalidate`). Recovery replays the same
logical operation, never a successor plan.

## One task row per admitted operation

Admission commits the operation and its `ProcessingTask` row in the same
`BEGIN IMMEDIATE` transaction:

| Operation kind | `task_type` | `params` |
| --- | --- | --- |
| `materialize` | `curation_materialize` | `{"operation_id": "<id>"}` |
| `export` | `curation_export` | `{"operation_id": "<id>"}` |

So an admitted operation is visible in `GET /api/tasks/` before any worker starts
it, and the two statuses stay consistent:

| Operation status | Task status |
| --- | --- |
| `admitted` (never started) | `pending`, or `interrupted` after reconciliation |
| `running` | `running` |
| `succeeded` | `completed` |
| `blocked` | `failed`, with the failure code in `result.error` |
| `cancelled` | `cancelled` |

The link is a plain `task_id` column with no foreign key: the generic task
lifecycle prunes and rewrites task rows, and it must never be able to delete or
block curation provenance.

## Checkpoints and progress

The export worker records `members written / item_count` on the task after every
image+caption pair and again once the staged attempt verifies, so `/api/tasks/`
shows real progress instead of a spinner. The same checkpoint renews the lease
and stores `progress_done` on the operation, so a resumed attempt reports where
the previous one stopped.

Publication itself is already no-clobber and content addressed: each attempt
stages into its own private directory and the final `renameat2(RENAME_NOREPLACE)`
refuses to overwrite an existing export. A resumed attempt therefore re-stages
its files rather than appending to a foreign staging directory, and a crash after
publication is resolved by verifying the published directory, not by re-copying.

## Cancellation

`POST /api/tasks/{id}/cancel` cancels the task; the same request marks a
not-yet-published operation `cancelled`. Execution then checks cancellation at two
explicit points:

1. before any source read, inside the transaction that claims the attempt;
2. immediately before publication, inside the transaction that renames the staged
   directory into `exports/`.

After the rename there is no cancellation. Published export bytes are immutable
and content addressed; a cancel that races publication loses, and the receipt
records `succeeded`. Cancelling an already-succeeded operation is refused by the
task API (`400`) and ignored by the operation hook.

Note that fixture mode (`OMOIDE_CURATION_FIXTURES=1`) freezes *every* legacy
`/api` mutation, which includes `/api/tasks/{id}/cancel`. That blanket denial is
the documented isolated-demo behaviour and is unchanged here; production mode
(`OMOIDE_CURATION_MODE=production`) denies only the listed human-stamping routes,
so the task API stays reachable.

## Worker fencing

`curation_operation` carries a lease: `lease_worker` (`host/pid/nonce`),
`lease_attempt` (one id per execution attempt) and `lease_expires_at`. It is
taken under SQLite `BEGIN IMMEDIATE`, so two processes cannot both believe they
hold it. A worker that finds a live lease held by someone else refuses with
`operation_lease_held` and leaves the operation's status and journal untouched —
being fenced out says nothing about the operation.

Takeover happens only after the lease expires, with one deliberate exception: if
the holder is a process on *this* host whose pid no longer exists, it is provably
dead and the lease is taken immediately, so a restart recovers without idling for
the lease window. Anything we cannot prove (another host, a live pid, a
permission error) falls back to expiry.

The publisher re-checks `require_lease` inside the transaction that performs the
rename. A worker whose lease was taken over fails with `operation_lease_lost`
before publishing anything. Attempt counts and the `curation_event` journal are
unchanged, so external attempt identity is preserved.

The cross-process advisory `flock` on the store root is retained: it orders peers
on the same store, while the database lease is the durable fence that survives a
crashed or partitioned worker.

## Restart recovery

At startup, before the generic stale-task cleanup, `reconcile_curation_operations`
walks every `admitted` or `running` operation:

* a live lease held by a worker we cannot prove dead — **skipped**, untouched;
* a task row already `cancelled` — the operation becomes `cancelled`;
* a `running` export whose `exports/<operation_id>` directory verifies against its
  own admitted snapshot (every image, caption, manifest, inventory entry and
  success marker) — marked **`succeeded`** with that manifest hash and never
  re-run. The attempt count is not incremented: no new attempt occurred;
* anything else — returned to **`admitted`**, lease cleared, with exactly one
  resumable task row left `interrupted`.

Reconciliation never starts a worker. Execution comes from the ordinary resume
path (`POST /api/tasks/{id}/resume`, or auto-resume when
`scan.auto_resume_interrupted_tasks` is on), which creates one successor task for
the same `operation_id`. `resume_task` serializes per operation rather than per
task type for these types, so two independent admitted operations can each be
resumed while one operation can never get two live executions.

## Authority

A background worker has no bearer: credentials are held only in memory and only
their hashes are stored. The worker re-authorizes with the admitted operation's
own grant, which re-runs every condition except possession — mode, dataset policy
version, revocation, expiry, operation set, reviewer kind, disclosure and
presentation mode — against freshly loaded rows. Revoking or expiring the grant
still stops execution and publication. This path can only *execute* an operation
that a bearer already admitted; it can never admit new work, create a grant, or
turn an agent recommendation into human acceptance.

## Migration

`718293a4b5c6`, descending from `60718293a4b5`, is additive: five nullable or
defaulted columns on `curation_operation` (`task_id`, `lease_worker`,
`lease_attempt`, `lease_expires_at`, `progress_done`). No existing column is
altered and nothing is backfilled. The downgrade refuses a database that already
carries job state, like the other curation migrations.

`scripts/curation-authority.py` no longer hard-codes the expected head; it derives
the single head from the shipped Alembic script directory, so a later additive
curation migration cannot leave the operator CLI refusing a correctly migrated
database.

## Not included

No UI. No change to export membership semantics. No distributed worker pool,
queue broker or cross-host coordination: the lease fences a second worker, it does
not schedule one. A long export still holds one lease for its whole run; the
lease window bounds unattended recovery on a host we cannot probe, not liveness
in general.
