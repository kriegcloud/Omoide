# Deferred annotation history pagination

The current `GET /api/annotations/media/{media_id}` response remains unchanged:
it returns the newest bounded attempt and revision summaries in the existing
`MediaAnnotationState` shape. That response contains two independently ordered
collections, so adding one cursor would be ambiguous, while adding cursor
metadata for both collections would change the wire shape for current clients.

When history navigation is needed, add two read-only endpoints instead:

- `GET /api/annotations/media/{media_id}/attempts?limit=25&before=<cursor>`
- `GET /api/annotations/media/{media_id}/revisions?limit=25&before=<cursor>`

Each endpoint returns `{ "items": [...], "next_cursor": string | null }` and
accepts `limit` from 1 through 100. Cursors are opaque, URL-safe base64 encodings
of versioned canonical JSON and are validated against the requested media ID and
collection before use.

Attempt pages use the total order `(created_at DESC, id DESC)`. Their cursor
contains `{ "v": 1, "media_id": int, "created_at": RFC3339 UTC, "id": UUID }`,
and the next-page predicate is `created_at < anchor_created_at OR
(created_at = anchor_created_at AND id < anchor_id)`.

Revision pages use `(revision DESC, id DESC)`. Their cursor contains
`{ "v": 1, "media_id": int, "revision": int, "id": UUID }`, and the
next-page predicate is `revision < anchor_revision OR
(revision = anchor_revision AND id < anchor_id)`.

Both queries fetch `limit + 1` rows to determine whether another page exists.
Attempt summaries continue to defer `raw_result` and `normalized_result`; exact
raw evidence remains available only through `GET /api/annotations/attempts/{id}`.
Newer inserts do not move or duplicate rows after an existing cursor because
both page orders are immutable and include a unique tie-breaker.

