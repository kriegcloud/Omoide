# Curation MCP adapter

A local stdio MCP server that lets an agent host drive the curation API. It
lives in `tools/curation-mcp/` as its own pinned project (official `mcp` Python
SDK `2.2.0`, `httpx 0.28.1`, its own `uv.lock`) and is deliberately **not** part
of Omoide's `pyproject.toml`.

The adapter owns protocol serialization only. It does not import the
application, open its database, start a scheduler or execute anything locally:
every tool is one bounded HTTP call to the same `/api/curation` routes the
browser UI uses, under the same grant, policy, revision checks and append-only
records. It cannot widen authority, because it has none of its own.

## What it cannot do

- **No review.** There is no accept, reject or defer tool. Production review
  needs a fresh passkey assertion bound to the exact artifact, caption, policy,
  decision and rationale ([production-authority.md](production-authority.md));
  that ceremony belongs to the human UI. An agent recommendation is not a human
  approval, and nothing here can turn one into the other.
- **No enrollment.** The one-time authenticator registration is a human
  operation on a grant carrying `enroll`. The adapter exposes no such tool.
- **No source registration.** `register_source_manifest` is a trusted local
  operator capability with no HTTP equivalent
  ([source-registration.md](source-registration.md)), so it has no adapter tool
  either. Neither has grant issuance or revocation.
- **No filesystem access.** Tools take server-minted IDs, never paths, globs or
  `file://` URIs. The adapter reads exactly one local file: the credential.
- **No egress of its own.** It talks only to the configured Omoide origin.

A grant that lacks an operation simply fails with the application's own
`operation_forbidden`; the adapter never decides.

## Configuration

| Variable | Meaning |
| --- | --- |
| `OMOIDE_URL` | Omoide origin, default `http://127.0.0.1:8123`. Plaintext HTTP is accepted only for loopback hosts; anything else is refused at startup so a bearer is never written to a cleartext remote socket. |
| `OMOIDE_CURATION_CREDENTIAL_FILE` | Absolute path to a mode-0600 regular file owned by the running user containing the bearer. Group- or world-readable files, symlinks, foreign owners and oversized files are refused. |
| `OMOIDE_MCP_TIMEOUT_SECONDS` | HTTP timeout, default 120. |
| `OMOIDE_MCP_MAX_RESULT_BYTES` | Serialized result bound, default 256 KiB. |
| `OMOIDE_MCP_MAX_PREVIEW_BYTES` | Preview byte ceiling, default 2 MiB. |

The credential never appears in a tool argument, a tool result, a log line or a
URL. It is read from the file on first authenticated use and kept in memory.
The unauthenticated `curation_status` tool keeps working when the credential is
missing or refused, so a misconfiguration is diagnosable without one.

Issue the credential with the trusted local helper
(`curation_auth.issue_production_grant`, or the fixture bootstrap in fixture
mode), which writes the 0600 file itself and stores only a hash of the token.
Give the adapter a grant with the operations it actually needs: `read`,
`materialize`, `caption`, `export`, and `preview` only when disclosing image
bytes to that client's model host is intended.

## Tools

| Tool | Input | Notes |
| --- | --- | --- |
| `curation_status` | none | Unauthenticated. Feature state and mode. |
| `datasets_list` | `cursor?`, `limit?` | Paginated; grant-scoped. |
| `dataset_get` | `dataset_id`, `cursor?`, `limit?` | Sources, a page of items with captions, review state, eligibility, blockers, and export receipts. Items are paginated. |
| `materialize` | `dataset_id`, `source_id`, `expected_revision`, `idempotency_key` | Derives the deterministic still artifact for one registered source. |
| `caption_propose` | `dataset_id`, `artifact_id`, `text`, `expected_revision`, `idempotency_key` | Appends an immutable caption revision. A proposal, never an approval. |
| `export_admit` | `dataset_id`, `expected_revision`, `idempotency_key` | Admits and runs a frozen export of the already eligible members. |
| `export_get` | `export_id` | Immutable receipt. The recovery read. |
| `export_resume` | `export_id` | Retries the same admitted operation. |
| `preview` | `dataset_id`, `target`, `target_id`, `include_bytes?`, `max_bytes?` | Metadata unless the grant carries `preview` and the caller opts in. |

Input schemas are closed (`additionalProperties: false`) with bounded strings,
enums and integer ranges. Actor identity, policy, paths and roots are not
inputs, exactly as in the HTTP contract.

### Semantics

- **Idempotency keys** are required on every mutating tool, 8–128 characters of
  `[A-Za-z0-9_-]`. `materialize` and `export_admit` forward the key to Omoide,
  where admission is durable: the same key with the same payload replays the
  same operation and the same receipt, and the same key with a different
  payload is `idempotency_conflict`. The caption route takes no key, so
  `caption_propose` enforces replay **inside the adapter process** and says so
  in its receipt (`idempotency.durable: false`); after an adapter restart a
  duplicate submission is refused by the application as `revision_conflict`
  instead. Closing that gap properly means an idempotency key on the caption
  route, which is an application change, not an adapter change.
- **Cancellation** at the protocol level cancels the in-flight HTTP call only.
  Omoide operations are durable and outlive the adapter: a cancelled
  `materialize` or `export_admit` may still have committed. Recovery is
  `dataset_get`, `export_get` and then `export_resume` — never a second
  admission with a new key. Each mutating tool's description says this.
- **Pagination** uses opaque cursors. An item cursor is bound to the dataset id
  and the revision it was minted at, so a moved draft returns `snapshot_stale`
  rather than silently skipping or repeating membership.
- **Bounded responses.** Serialized results are capped and pages shrink until
  they fit. Preview bytes are capped by both the request and the configured
  ceiling; an oversized body is `response_too_large` rather than a truncated
  image.
- **Errors** relay the application's `{detail: {code}}` code verbatim, with the
  HTTP status and an `origin` of `omoide` or `adapter`. No upstream message
  text is copied into a result. Adapter-originated codes cover configuration
  and credential problems, unreachable endpoints, bounds and cursors.
- **Disclosure.** `preview` with `include_bytes: true` returns a leading text
  block stating the byte count, media type and SHA-256 before the image block,
  and repeats it in the structured `disclosure` field. Bytes that leave the
  application reach that client's model host and cannot be recalled; the
  adapter states this rather than implying loopback transport prevents it.
- **Stdout carries protocol only.** All logging goes to stderr; the SDK's stdio
  transport additionally points the server's own stdout at stderr while serving.
- **Untrusted content.** Labels, captions, blockers and error codes returned by
  these tools are library data. They are never interpolated into tool
  descriptions, headers, paths or authorization.

## Running it

```bash
uv run --directory tools/curation-mcp omoide-curation-mcp
```

## Tests

```bash
cd tools/curation-mcp
uv sync
OMOIDE_TEST_PYTHON=<omoide interpreter> OMOIDE_TEST_PYTHONPATH=<extra deps> \
  uv run python -m unittest discover -s tests -t tests -v
```

`tests/fixture_app.py` assembles the repository's own curation router, guard and
services over a temporary SQLite database — the way
`tests/test_curation_still_slice.py` does — and serves it with uvicorn on a
pre-bound loopback socket with the application lifespan off. Its images are
geometry generated in the harness. It touches no live database, no library
media and no running container, and it is not the full `app.main` middleware
stack.

`tests/test_adapter_stdio.py` spawns the adapter as a subprocess and drives it
with the SDK's stdio client over real pipes: exact tool surface and closed
schemas, the whole materialize/caption/export flow, idempotent replay and
conflict, pagination and `snapshot_stale`, the application's own
`review_required` until the separate human lane accepts out of band, preview
disclosure and byte bounds, verbatim error codes, and the absence of the
credential from every result and from stderr.

## Registering it with a client

This is **prepared, not applied**. Registering the adapter with a client gives
that client's model host whatever the grant allows, so the owner runs it.

Project-scoped (a `.mcp.json` next to the checkout, or `--mcp-config <file>`
for a single headless run):

```json
{
  "mcpServers": {
    "omoide-curation": {
      "command": "uv",
      "args": ["run", "--directory", "<absolute path to tools/curation-mcp>", "omoide-curation-mcp"],
      "env": {
        "OMOIDE_URL": "http://127.0.0.1:8123",
        "OMOIDE_CURATION_CREDENTIAL_FILE": "<absolute path to the 0600 credential file>"
      }
    }
  }
}
```

Global (user-scoped) install, for the owner to run:

```bash
claude mcp add omoide-curation --scope user \
  --env OMOIDE_URL=http://127.0.0.1:8123 \
  --env OMOIDE_CURATION_CREDENTIAL_FILE=<absolute path to the 0600 credential file> \
  -- uv run --directory <absolute path to tools/curation-mcp> omoide-curation-mcp
```

Before running either, decide which grant the credential file carries. A
`read`-only grant is the metadata lane; adding `preview` means image bytes may
leave for that client's model host. Revocation is
`revoke_production_authority` plus deleting the credential file, not an edit to
the client config.

## Verified clients

Claude Code 2.1.274 over stdio: the server connects, all nine tools are
discovered under `mcp__omoide-curation__*`, and `curation_status` returns its
JSON. The SDK is pinned at `mcp==2.2.0`, whose latest protocol revision is
`2026-07-28` with the earlier handshake revisions still supported; other clients
are untested.
