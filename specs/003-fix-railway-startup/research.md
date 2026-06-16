# Research: Non-Blocking API Startup for Reliable Deploys

**Feature**: `003-fix-railway-startup` | **Date**: 2026-06-15

## R1 — Root Cause of Railway Hang

**Decision**: Startup blocks in FastAPI lifespan **before** `yield` because `init_db()` is synchronous.

**Rationale**:

- `api/main.py` lines 46–50: `init_db()` runs before `start_scheduler()` and before `yield`.
- Uvicorn only binds the HTTP port after lifespan startup completes (`Application startup complete`).
- `init_db()` → `run_with_db_retry(_run_migrations)` → `Base.metadata.create_all(bind=engine)` opens first DB connection.
- `_engine_connect_args()` (connection.py lines 106–113) sets only `sslmode=require` — **no `connect_timeout`**.
- psycopg2 default: TCP connect can block indefinitely on unreachable/slow hosts.
- `run_with_db_retry` only retries on **raised exceptions**; a hung socket never raises → infinite block, no logs.

**Alternatives considered**:
- Lazy-import pipeline modules — reduces import time but does not fix init_db block (rejected as primary fix).
- Remove init_db entirely — migrations would not run (rejected).

---

## R2 — Connection Timeout Mechanism

**Decision**: Add `connect_timeout` (seconds) to SQLAlchemy `create_engine(connect_args=...)` for all environments; default **10** via `DB_CONNECT_TIMEOUT` env var.

**Rationale**:
- psycopg2/libpq supports `connect_timeout` in connection parameters.
- SQLAlchemy passes unknown keys in `connect_args` to the driver.
- 10s per attempt × 5 retries (existing `DB_INIT_RETRIES`) ≈ bounded worst case ~50s+backoff, under Railway 300s healthcheck.
- Runtime `get_session()` and `check_db_connection()` also benefit — no infinite hangs on health pings.

**Alternatives considered**:
- `statement_timeout` only — does not help TCP connect hang (rejected as sole fix).
- Global socket default — less explicit than engine connect_args (rejected).

---

## R3 — Non-Blocking Lifespan Pattern

**Decision**: Start `init_db()` in a **daemon background thread** from lifespan; call `start_scheduler()` synchronously; **yield immediately**.

**Rationale**:
- FastAPI lifespan must return from startup phase quickly for Uvicorn to bind port.
- `start_scheduler()` is non-blocking (APScheduler background thread) — safe to keep synchronous.
- Daemon thread: if process exits, thread does not prevent shutdown.
- Existing `init_db(raise_on_failure=False)` logic reused unchanged inside thread — minimal diff, same migration behavior.
- Thread-safe status flag for health observability.

**Alternatives considered**:
- `asyncio.to_thread(init_db)` before yield — still blocks lifespan until init_db completes (rejected).
- Run migrations on first request — delays first API call latency, complicates error handling (rejected).
- Separate init container/job — operational overhead (rejected for this fix).

---

## R4 — Health Endpoint Contract

**Decision**: Keep existing health JSON shape; add optional **`db_init_status`** and **`db_init_error`** fields.

**Rationale**:
- Railway healthcheck only needs HTTP 200 from `/api/health` — port must be open.
- Operators need to distinguish "app up, DB migrating" vs "app up, DB down" vs "app not started".
- Spec FR-004/CC-004 require backward compatibility.
- `database_connected` continues to reflect live `SELECT 1` via `check_db_connection()` (with new connect timeout).

**Values for `db_init_status`**:
- `pending` — thread not yet started (transient)
- `running` — migrations in progress
- `complete` — init_db succeeded
- `failed` — init_db exhausted retries; see `db_init_error`

**Alternatives considered**:
- Separate `/api/ready` endpoint — extra scope (deferred; not needed if health responds degraded).

---

## R5 — Retry and Recovery Behavior

**Decision**: Preserve existing `run_with_db_retry` and `db_init_retry_settings()` inside background `init_db()`; no change to retry counts.

**Rationale**:
- Transient Postgres recovery mode already handled.
- With connect_timeout, retries now actually fire instead of hanging.
- After background init fails, `check_db_connection()` on subsequent health requests can succeed when DB comes online — `get_session()` already retries at request time.

**Alternatives considered**:
- Infinite retry loop in background — could mask permanent misconfiguration (rejected).

---

## R6 — Testing Strategy

**Decision**: Unit test connect_args; integration-style test for status transitions with mocked `init_db`; manual Railway deploy per quickstart.

**Rationale**:
- Full migration test requires live Postgres — existing CI/local DB setup.
- Mock thread + init_db for fast unit coverage of state machine.

**Alternatives considered**:
- Only manual Railway test — insufficient regression guard (rejected as sole strategy).
