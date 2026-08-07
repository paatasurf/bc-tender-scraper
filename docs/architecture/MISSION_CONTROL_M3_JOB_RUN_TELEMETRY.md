# Mission Control M3 — job run telemetry (schema + Surrey instrumentation)

**Status:** M3B (schema) merged and applied in production; M3C (Surrey
identity scheduler instrumentation) implemented here, flag default
`false`, not yet enabled in any environment.

## M3B recap — `ops_job_runs` / `ops_job_run_events`

Migration 033 (Class D) added two new, general-purpose tables for
persisted job run history/heartbeats — `pipeline/job_run.py`'s writer API
(`start_job_run`/`heartbeat_job_run`/`record_job_step`/`finish_job_run`).
Already applied in production (dry-run + manual `--apply`, both verified
read-only afterward: `FULLY_APPLIED`, 0 rows in both tables). See the M3A
architecture audit and M3B PR (#124) for the full schema design —
`lease_expires_at` TTL, `status` in
`running`/`success`/`failed`/`partial_failure` (never `stale` — that is a
read-model interpretation, not a stored value), fixed `error_summary`
labels via `pipeline/error_classification.py`, and DB-level CHECK
constraints on `status`/`trigger`/`event_type`.

## M3C — Surrey identity scheduler instrumentation

**Scope:** only `pipeline/scheduler.py::_scheduled_surrey_identity_run()`
and the optional `on_phase` progress-boundary hook added to
`pipeline/surrey_identity_scheduler.py::run_surrey_identity_import_once()`.
Nothing else is touched — not the generic `/api/scrape/surrey-permits`
endpoint, not the daily tender_data pipeline, not AI scoring / company
intelligence / arch-company-intelligence, not n8n, not Mission Control's
UI or `/api/ops/*` read model.

**Feature flag:** `ENABLE_SURREY_JOB_RUN_TELEMETRY`, default `false`
(same `config.env.env_flag` "1"/"true"/"yes" convention as every other
flag in this repo).

- **`false` (default):** `_scheduled_surrey_identity_run()` calls
  `run_surrey_identity_import_once(session, rows=rows)` — the exact same
  call, same signature, as before M3C. No `db.connection.get_session()`
  call beyond the one the real Surrey work already made, no
  `ops_job_runs`/`ops_job_run_events` read or write of any kind.
- **`true`:** a `ops_job_runs` row is created (`job_type=
  "surrey_identity_scheduler"`, `trigger="scheduler"`, no
  `idempotency_key` — M3C is pure observation, not a change to the
  scheduler's own retry/dedup semantics), milestone `step_completed`
  events are recorded at the real `plan`/`validate`/`apply` boundaries
  inside `run_surrey_identity_import_once()` (never per-row, never a
  heartbeat event), the lease is heartbeat-extended at each boundary, and
  the run is finished as `success` (errors == 0) or `partial_failure`
  (errors > 0) via a pure mapping function
  (`map_surrey_result_to_job_run_outcome`). An exception escaping the
  scheduler wrapper itself (e.g. the scrape call failing before the
  identity-aware import ever runs) finishes the run as `failed`, with the
  raw exception text handed **only** to `finish_job_run()`'s own safe
  classifier (`pipeline/error_classification.py`) — never logged or
  stored verbatim anywhere, and the original exception still propagates
  to the caller exactly as it did before M3C (telemetry never changes
  the real job's own success/failure).

**`counts` recorded:** `source_rows`, `inserted`, `updated`,
`error_count` — flat integers only. Never `plan_digest`, `result_digest`,
error text, API response bodies, or permit-level strings.

**Fail-open:** every telemetry call (`start_job_run`, the phase
callback's `record_job_step`+`heartbeat_job_run`, `finish_job_run`) runs
on its own database session, independent of the Surrey import's own
session/transaction, and is wrapped so a failure there is swallowed and
logged as a fixed, non-parameterized warning (never the underlying
exception's text). If `start_job_run()` itself fails, no further
telemetry is attempted for that run at all — the Surrey worker always
still runs exactly once, unaffected either way.

## Limitation: no UI yet

Mission Control's `/api/ops/*` read model and its frontend do **not**
show `ops_job_runs`/`ops_job_run_events` rows — that is M3E (dashboard/
alerts), not scoped here. Until M3E ships, the only way to verify M3C
telemetry is real-and-correct is **read-only, direct**: querying
`ops_job_runs`/`ops_job_run_events` in the database (or Railway logs for
the fixed warning messages on a telemetry failure) after a real scheduled
Surrey run. There is nothing to click through in any dashboard yet.

## Rollout (do not skip a step)

1. Merge this PR. `ENABLE_SURREY_JOB_RUN_TELEMETRY` is unset everywhere
   → no behavior change, no new writes, still fully inert on the
   telemetry side.
2. Deploy with the flag still unset/`false`.
3. Read-only health check (`/api/health`, Railway logs — same checks as
   the M3B post-apply verification).
4. Separate, explicit dry-run-style verification that the flag, once
   flipped, would behave as designed (e.g. a scoped manual review of this
   PR's tests, or a local/staging trial run with the flag on) — decided
   and executed separately, not bundled into this deploy.
5. Separate, explicit authorization to set
   `ENABLE_SURREY_JOB_RUN_TELEMETRY=true` on the target environment (a
   Railway variable change — its own deliberate step, never bundled with
   a code deploy).
6. Let exactly one real scheduled Surrey run occur (05:30 local, per the
   existing `SURREY_SCHEDULE_HOUR`/`SURREY_SCHEDULE_MINUTE` cron) — do
   not trigger it manually.
7. Read-only postcondition: query `ops_job_runs`/`ops_job_run_events` for
   that run_id, confirm `status` matches the real outcome, `counts` are
   sane integers, no digest/text fields present, and Railway logs show no
   unexpected telemetry warnings.
