# Mission Control M1 — Read-only Ops API

**Status:** implemented, not yet wired to any frontend. Code + tests only —
no migration, no deploy, no Railway/n8n action, no production write of any
kind is part of this PR.

## Purpose

M1 adds five read-only endpoints under `/api/ops/*` that expose real
operational data (pipeline run history, coordinator state, source
freshness) for the future TenderScope Mission Control dashboard (see the
isolated `concept/mission-control/` UI prototype from the earlier design
phase). This replaces mock data with real data for the pieces that already
have a reliable source of truth in the database, and returns an honest
`unavailable`/`not_connected` status for everything that doesn't yet.

## Files

| File | Role |
|---|---|
| `pipeline/ops_read_model.py` | Pure data-shaping logic: `pipeline_runs` normalization, coordinator-lease lookup, source freshness computation. No FastAPI imports — every function takes plain values or a `Session` and returns a plain dict, so it's unit-testable without routes. Never writes, never calls `init_db()`. |
| `api/ops.py` | Thin FastAPI router (`ops_router`, mounted at `/api/ops`). Five `GET` routes, each backed by one or two `pipeline/ops_read_model.py` calls. Gated by the existing `X-Internal-Key` guard. |
| `api/main.py` | +2 lines: import `ops_router`, `app.include_router(ops_router)`. |
| `tests/unit/test_ops_read_model.py` | Pure-logic unit tests (no DB) + local-Postgres-gated integration tests (skipped if unavailable). |
| `tests/unit/test_ops_api.py` | Route-level tests: auth guard, GET-only structure, graceful degradation under a down/mocked database. |

## Endpoint contracts

All five endpoints require the `X-Internal-Key` header (see
[Authorization](#authorization) below). All return `200` with a
best-effort/degraded body rather than `500` whenever the underlying data
source is unavailable — the only non-`200` responses are `403` (bad/missing
key), `404` (`/runs/{run_id}` for a run_id that exists nowhere), and `503`
(`/runs/{run_id}` when the database itself is unreachable).

### `GET /api/ops/summary`

```json
{
  "generated_at": "2026-08-06T03:28:53.265157+00:00",
  "system": {
    "api_status": "healthy",
    "database_connected": true,
    "scheduler": {
      "enabled": true,
      "running": false,
      "job_id": "daily_scrape_import",
      "timezone": "America/Vancouver",
      "schedule": "06:00",
      "next_run_at": null,
      "surrey_identity_scheduler_enabled": false,
      "surrey_identity_schedule": "05:30",
      "surrey_identity_next_run_at": null
    },
    "coordinator": {
      "backend": "legacy",
      "schema_available": true,
      "active_run": null,
      "expired_lease_run": null
    }
  },
  "integrations": [
    {"name": "Railway", "status": "not_connected"},
    {"name": "n8n", "status": "not_connected"},
    {"name": "Clerk", "status": "not_connected"},
    {"name": "Vercel", "status": "not_connected"},
    {"name": "Resend", "status": "not_connected"},
    {"name": "AI Assistant", "status": "not_connected"}
  ],
  "capabilities": {
    "incidents_persisted": false,
    "scraper_heartbeats": false,
    "ai_chat_telemetry": false
  }
}
```

`system.coordinator.active_run`, when present (lease is currently valid):

```json
{
  "run_id": "8f2c1a90-...",
  "phase": "import",
  "lease_valid": true,
  "lease_expires_at": "2026-08-06T05:41:02+00:00",
  "started_at": "2026-08-06T02:41:02+00:00"
}
```

`system.coordinator.expired_lease_run`, when present — same shape, but
`lease_valid: false`. **`active_run` and `expired_lease_run` are never
both non-null at the same time** (there is at most one `status='active'`
row per scope, per the R1 partial-unique-index guarantee, and it is either
lease-valid or it isn't). See
[Coordinator active-run semantics](#coordinator-active-run-semantics)
below for why this distinction exists and is not optional.

`database_connected` reuses `db.connection.check_db_connection()` — the
exact function `/api/health` already uses. `scheduler` is
`pipeline.scheduler.scheduler_status()` verbatim. `coordinator.backend`
reads `PIPELINE_COORDINATOR_BACKEND` (informational only — this endpoint
never calls into `pipeline.run_coordinator`'s dispatcher, so an
unrecognized value reports `"unknown"` instead of raising, unlike the
dispatcher itself which fails closed because it's about to route a real
call). `coordinator.schema_available` is `false` whenever migration 032
hasn't been applied to the target database yet (or the DB is unreachable)
— this is a **distinct** state from "database works, schema exists, but
there is simply no active run right now," which also reports
`active_run: null`. A dashboard must not render these two situations
identically; see below.

### `GET /api/ops/runs?limit=50&status=&job_type=`

```json
{
  "generated_at": "2026-08-06T03:28:53.309689+00:00",
  "runs": [
    {
      "id": 4821,
      "run_id": "n8n-5636",
      "job_type": "scrape-commercial",
      "status": "success",
      "normalized_status": "success",
      "started_at": "2026-08-05T13:00:21+00:00",
      "finished_at": "2026-08-05T13:00:45+00:00",
      "counts": {"found": 135, "new": 18, "updated": 117, "rejected": 0},
      "error_present": false,
      "error_summary": null
    }
  ],
  "count": 1,
  "database_connected": true
}
```

- `limit` is clamped to `[1, 200]`.
- `status` filters on **`normalized_status`**, not the raw `pipeline_runs.status` column (see [Run normalization](#run-normalization) — the two are not the same thing by design).
- `job_type` filters on `pipeline_runs.step` — there is no separate `job_type` column in `pipeline_runs`; `step` values (`"scrape-federal"`, `"ai-scoring"`, `"company-intelligence"`, ...) are what the rest of the codebase already calls "job type" informally.
- Internally overfetches up to 500 rows (ordered `started_at DESC`) before applying the `normalized_status` filter in Python, then truncates to `limit`. A `status` filter combined with a large `limit` can return fewer rows than requested if stale/superseded rows dominate the most recent 500 — a documented, bounded-cost tradeoff, not a bug. No pagination in M1.
- **There is no `error` field.** See [Error handling](#error-handling) — `pipeline_runs.error` is never returned verbatim.

### `GET /api/ops/runs/{run_id}`

```json
{
  "generated_at": "...",
  "run_id": "8f2c1a90-...",
  "steps": [ /* same shape as the runs[] entries above, one per pipeline_runs row for this run_id */ ],
  "coordinator": {
    "status": "active",
    "phase": "import",
    "lease_valid": true,
    "lease_expires_at": "...",
    "tender_scrape_finished_at": "...",
    "import_started_at": "...",
    "import_finished_at": null,
    "finished_at": null,
    "success": null,
    "stale_reclaimed": false
  }
}
```

`coordinator` is `null` when this `run_id` has no matching
`pipeline_coordinator_runs` row (true for every run_id that predates R1, or
that never went through the postgres-backed coordinator). `404` only when
**neither** `pipeline_runs` nor `pipeline_coordinator_runs` has anything for
this `run_id`.

### `GET /api/ops/sources`

**This endpoint reports *data freshness* (when the newest row in a table
was written), not *scraper-run health*.** A `MAX(scraped_at)` proves a row
exists with that timestamp; it proves nothing about whether the run that
wrote it succeeded, partially failed, or how many rows it was supposed to
write. A scraper that silently drops 90% of its results but successfully
writes the other 10% looks identical to a fully healthy run under this
endpoint. Real scraper-run health requires a job-event/heartbeat model,
which is M2 (see below) — M1 does not have that signal and does not
pretend to.

```json
{
  "generated_at": "2026-08-06T03:28:53.359444+00:00",
  "sources": [
    {
      "name": "Federal",
      "status": "stale",
      "latest_record_at": "2026-07-15T16:15:38.390765-07:00",
      "freshness_hours": 508.22,
      "reason": null,
      "source_of_truth": "tenders.scraped_at WHERE source='buyandsell.gc.ca'"
    },
    {
      "name": "MERX Open",
      "status": "stale",
      "latest_record_at": "2026-07-15T16:15:38.806843-07:00",
      "freshness_hours": 508.46,
      "reason": null,
      "source_of_truth": "tenders.scraped_at WHERE source='merx.com'"
    },
    {
      "name": "MERX Architecture",
      "status": "unknown",
      "latest_record_at": null,
      "freshness_hours": null,
      "reason": "telemetry_not_available",
      "source_of_truth": "arch_tenders.scraped_at"
    }
  ],
  "database_connected": true
}
```

All three rows above are real output from a local dev database: "Federal"
and "MERX Open" share the `tenders` table (same scrape step, distinguished
by `source`, both genuinely stale at ~508h); "MERX Architecture" has no
rows in this local DB at all, hence `unknown`/`telemetry_not_available`
rather than a fabricated status.

`status` thresholds (M1 defaults, defined as named constants in
`pipeline/ops_read_model.py`, not hidden magic numbers):
`freshness_hours <= 24` → `healthy`; `<= 72` → `degraded`; `> 72` → `stale`;
no data ever recorded → `unknown` with `reason: "telemetry_not_available"`.

## Source-of-truth matrix

| `name` | Table | Timestamp column | Filter | Notes |
|---|---|---|---|---|
| Federal | `tenders` | `scraped_at` | `source = 'buyandsell.gc.ca'` | |
| MERX Open | `tenders` | `scraped_at` | `source = 'merx.com'` | Same table as Federal, different `source` value — written by the same scrape step (`run_federal_scraper` merges both), so its `scraped_at` will track Federal's closely in practice, but it is a genuinely distinct BC-provincial data source and is reported separately so the dashboard never hides it inside "Federal." |
| MERX Architecture | `arch_tenders` | `scraped_at` | none | Own scrape step (`scrape-merx-arch`), independent freshness signal from Federal/MERX Open. |
| Commercial | `commercial_tenders` | `scraped_at` | none | Own scrape step (`scrape-commercial`). |
| Surrey Permits | `permits` | `scraped_at` | `source = 'surrey'` | `source` is an indexed column on `permits`. |
| Burnaby Permits | `permits` | `scraped_at` | `source = 'burnaby'` | |
| Vancouver Permits | `permits` | `scraped_at` | `source = 'vancouver'` | |
| Reddit Signals | `reddit` | `scraped_at` | none | |
| News Signals | `news` | `scraped_at` | none | |
| LinkedIn Signals | `linkedin_signals` | `scraped_at` | none | |
| Early Signal Events | `early_signal_events` | `scraped_at` | none | Rezoning/development-permit pre-tender signals. |

**Deliberately no row count.** Every query above is a single `MAX()`
aggregate — no `COUNT(*)`, no joins, no per-row scan. `permits` alone is a
large table (tens of megabytes of CSV-equivalent data); a `COUNT(*)` on
every poll of this endpoint was judged not worth the cost for M1. Row
counts can be added in M2 if a dashboard need justifies it (candidate:
maintain them incrementally rather than a full-table scan).

## Run normalization

`pipeline_runs` is not touched or migrated — it keeps its existing
append-only, no-TTL shape exactly as-is. `normalized_status` is a
*computed* field, added on read, never written back:

| raw `status` | condition | `normalized_status` |
|---|---|---|
| `success` / `failed` / `skipped` | — | passed through unchanged |
| `running` | `run_id` has a currently-valid lease in `pipeline_coordinator_runs` (`status='active'` and `lease_expires_at > now()`, scope-independent) | `active` |
| `running` | no coordinator backing, `finished_at IS NULL` | `stale_candidate` |
| `running` | no coordinator backing, `finished_at` is set (contradictory raw data) | `unknown` |
| anything else | — | `unknown` |

This is the direct implementation of task rule 5: **a bare
`pipeline_runs.status = 'running'` row is never treated as proof of an
active process.** `pipeline_runs` has no lease/TTL — a crashed worker's row
sits at `status='running'` forever with no self-cleanup (this is visible
in production today: rows from mid-June 2026 are still `running`). Only a
matching, currently-valid `pipeline_coordinator_runs` lease is trusted as
"this really is active right now" — and that check takes priority over
everything else (verified directly by
`test_coordinator_active_lease_takes_priority_over_stale_candidate` and
`test_coordinator_lease_only_applies_to_matching_run_id`).

`counts_json` is parsed with `json.loads` inside a `try/except`; anything
that isn't valid JSON, or valid JSON that isn't a `dict` (e.g. a bare list
or number), becomes `{}` rather than raising or being echoed as-is.

## Error handling

**`pipeline_runs.error` is never returned, in any form, by any endpoint.**
It is free-text `str(exc)` written by arbitrary code across the codebase
(`pipeline/runs.py` and its callers) — it can and does contain connection
strings, bearer tokens, API keys, file paths, or other operational detail
that has no business appearing in a dashboard response. Truncating it (the
original M1 implementation) is not sufficient: a truncated secret is still
a leaked secret.

Instead, every run payload carries:

- **`error_present`** (`bool`) — was there any error at all.
- **`error_summary`** (`string | null`) — one of exactly six fixed labels:
  `timeout`, `http_4xx`, `http_5xx`, `database`, `validation`, `unknown`.
  Never anything else, and never a substring of the original text.

`classify_run_error()` (`pipeline/ops_read_model.py`) does keyword
matching against a *lowercased copy* of the raw error purely to pick one
of those six labels; that lowercased copy is discarded immediately and is
never part of the return value or logged. Checked in this order (first
match wins): `timeout` → `http_5xx` (`\b5\d{2}\b`) → `http_4xx`
(`\b4\d{2}\b`) → `database` (postgres/psycopg/sqlalchemy/connection-
refused-style markers) → `validation` (valueerror/keyerror/typeerror-style
markers) → `unknown`.

Verified directly by
`test_classify_run_error_never_leaks_secret_fragments` (parametrized over
`postgresql://user:password@host/db`, `Authorization: Bearer secret-value`,
`api_key=secret-value`, and two more realistic variants) and
`test_build_run_payload_never_includes_raw_error_field`, which
`json.dumps()`-serializes a full run payload built from a secret-bearing
raw error and asserts none of the secret fragments appear anywhere in the
serialized output.

Full error text stays exactly where it already lives —
`pipeline_runs.error` in the database — and will only ever be exposed
through **protected Railway/n8n logs** once M2 wires those up (see
[Limitations](#limitations--what-m1-does-not-do)), never through this API.

## Coordinator active-run semantics

`system.coordinator` in `/api/ops/summary` (and the underlying
`get_coordinator_summary()`) distinguishes three states that a naive
implementation could otherwise collapse into "is there an active run":

1. **`schema_available: false`** — migration 032 hasn't been applied to
   this database (or it's unreachable). We cannot know whether anything is
   running. This is not the same as "nothing is running."
2. **`schema_available: true`, `active_run: null`, `expired_lease_run: null`**
   — the schema works and there is genuinely no `status='active'` row for
   the `tender_data` scope right now.
3. **`schema_available: true`, `expired_lease_run: {...}`, `active_run: null`**
   — there **is** a `status='active'` row, but its lease has expired.
   Nobody has run the reclaim step (that happens lazily, the next time
   `begin_run`/`begin_or_resume_run` or a locked mutation is called for
   that run_id — see `pipeline/run_coordinator_postgres.py`). This is a
   **hung process that looks alive in the raw data** — exactly the
   `pipeline_runs.status='running'`-forever problem R1/R2 already solved
   for the coordinator table itself, and M1 must not reintroduce the same
   mistake one layer up by calling an expired lease "active."

`active_run` and `expired_lease_run` are mutually exclusive by
construction (verified by
`test_expired_lease_active_row_is_not_reported_as_active_run` and
`test_valid_lease_active_row_is_reported_as_active_run_not_expired`): a row
is placed in exactly one of the two, never both, and `active_run` is
populated **only** when `lease_expires_at > now()` at read time.

## Graceful degradation under a transient database failure

`check_db_connection()` succeeding does not guarantee that a `Session`
acquired moments later, or a query run against it, will also succeed —
connection pools exhaust, connections drop, queries time out. `/summary`,
`/runs`, `/runs/{run_id}`, and `/sources` all route their database access
through a single helper, `api/ops.py::_call_with_session()`:

```python
def _call_with_session(fn):
    session = None
    try:
        session = get_session()
        return True, fn(session)
    except Exception:
        return False, None
    finally:
        if session is not None:
            try:
                session.close()
            except Exception:
                pass
```

`session.close()` is only attempted if `get_session()` actually returned
one — closing a session that failed to acquire would itself raise. Any
failure (acquisition or query) reports `ok=False`, and every route maps
that to the same degraded body it already uses for
`check_db_connection() == False` — `/summary` reports an empty
`coordinator` block, `/runs` and `/sources` report empty/`unknown` with
`database_connected: false`, `/runs/{run_id}` returns `503`. None of these
paths can produce an unhandled `500`.

Verified by `test_ops_*_never_500s_when_get_session_raises` (four routes)
and `test_ops_summary_never_500s_when_query_raises_after_session_acquired`
(the acquisition succeeds, the query inside it doesn't — the session must
still be closed and the response must still degrade), plus three
unit-level tests on `_call_with_session()` itself covering: acquisition
failure (nothing to close), query failure after successful acquisition
(session closed anyway), and the success path (session closed too, not
leaked).

## Authorization

**Finding:** this codebase has a working Clerk JWT verification
implementation (`api/clerk_plan.py`), but it is scoped specifically to
*paid-customer Company Intelligence feature access* — `assert_
company_intelligence_access()` verifies a Clerk session token and checks
`public_metadata.plan` against `PAID_PLANS` (`basic`/`pro`/`admin`). There
is no existing "internal ops staff" role concept distinct from "paying
customer" — the closest thing is `role: admin` in a user's Clerk
`public_metadata`, which happens to also satisfy the paid-plan check
(`PAID_PLANS` includes `"admin"`). Using that as-is to gate Mission Control
would work mechanically, but it conflates two different concerns (billing
plan access vs. internal-staff access) and requires a live network call to
Clerk's API in some code paths — a real decision for whoever owns the
Mission Control frontend, not something to decide unilaterally inside a
read-only-API PR.

**What M1 actually does:** `/api/ops/*` is gated by the *already-existing*
`X-Internal-Key` mechanism (`api/main.py::verify_internal_key`,
independently duplicated as `api/ops.py::_require_internal_key` for the
same reason `api/internal.py` already duplicates it — to avoid a
`api.main` ↔ `api.ops` circular import). This is not a new auth scheme: it
is the exact same pre-shared-secret header already gating ~30 existing
`/internal/*` endpoints (including the ones n8n calls today). Per the task
instruction ("если нет уже готового internal read-only guard, оставь
endpoints отключёнными... не делай endpoints публичными"), this qualifies
as the ready-made internal guard — so the endpoints are implemented and
gated, not left disabled.

**What this is *not* appropriate for:** `X-Internal-Key` is a static
shared secret. It is safe for a trusted server-side caller (a
backend-for-frontend proxy — e.g. Vercel server-side code holding the key
and never shipping it to the browser) but **not safe to embed in
browser-delivered JavaScript**. If the real Mission Control frontend is
going to call these endpoints directly from end-user browsers, it needs a
proper per-user session check (a Clerk role gate, or a dedicated ops-scoped
token) — not this key. **This PR does not decide that** — it is flagged
here explicitly as the next concrete decision for whoever owns the
frontend integration.

## Limitations / what M1 does not do

- No incidents. `GET /api/ops/incidents` always returns an empty list plus
  `capability.available: false` — there is no incidents table. Building
  one (schema, migration, writer) is explicitly out of scope for M1.
- No scraper heartbeats beyond what `pipeline_coordinator_runs`'s lease
  already provides for the single tender-data pipeline. Individual scraper
  steps (Reddit, News, LinkedIn, etc.) have no heartbeat signal at all —
  only their last `pipeline_runs` row, subject to the same normalization
  rules above.
- No AI Assistant / chat telemetry. The M1 contract's `AI Assistant`
  integration entry is `not_connected` — there is no chat session/latency/
  tool-failure data source to read from yet.
- No Railway/n8n/Clerk/Vercel/Resend status. All five report
  `not_connected` — no HTTP calls to any of these are made by M1 code, by
  design (task rule 8).
- Row counts are intentionally excluded from `/api/ops/sources` (see
  above).
- `/api/ops/runs` has no real pagination — a bounded overfetch-then-filter,
  documented above.

## M2 plan (not started, not scoped here)

1. **Persisted ops events / incidents.** A real `ops_incidents` (or
   similarly named) table + a minimal writer path (likely triggered from
   coordinator stale-reclaim events and/or scrape-step failures), so
   `GET /api/ops/incidents` can return real data instead of an empty
   capability placeholder. Needs its own migration, its own Class D review,
   and explicit rules about who/what is allowed to write to it (read-only
   API boundary must not become a write surface by accident).
2. **Scraper heartbeats.** Extend either `pipeline_runs` (add a lease-style
   `heartbeat_at` + TTL, mirroring what R1 already built for
   `pipeline_coordinator_runs`) or introduce a dedicated heartbeat table for
   the auxiliary/signal scrapers that currently have no liveness signal
   between "started" and "finished."
3. **AI chat telemetry.** Requires the AI Assistant/chat subsystem to emit
   session/latency/tool-call events somewhere queryable — not designed yet.
4. **Freshness at lower cost.** If `/api/ops/sources` needs to be polled
   frequently, consider an incrementally-maintained freshness summary
   table instead of per-request `MAX()` scans, and revisit whether row
   counts are worth adding.
5. **Real dashboard-facing authorization.** Replace/augment the
   `X-Internal-Key` gate with whatever the Mission Control frontend's
   actual deployment topology needs (BFF-proxy-only vs. direct
   browser-to-API), per the [Authorization](#authorization) findings above
   — a decision for the frontend owner, informed by this document, not
   made in M1.
