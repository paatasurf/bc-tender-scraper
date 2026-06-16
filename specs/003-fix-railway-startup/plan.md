# Implementation Plan: Non-Blocking API Startup for Reliable Deploys

**Branch**: `003-fix-railway-startup` | **Date**: 2026-06-15 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/003-fix-railway-startup/spec.md`

**User constraints**: Fix Railway deploy healthcheck failure caused by synchronous `init_db()` blocking FastAPI lifespan before Uvicorn binds the port. Scope **only** `api/main.py` lifespan and `db/connection.py` init/connect path. **Do not** change scoring, scrapers, frontend, or feature 001/002 behavior.

## Summary

Railway deploys fail because `api/main.py` lifespan calls `init_db()` synchronously before `yield`. When the first PostgreSQL connection in `_run_migrations()` / `create_all()` has **no connect timeout**, the call can block indefinitely — Uvicorn never logs "Application startup complete", the port never opens, and `/api/health` never responds.

**Approach**:

1. **Bounded connections** — Add `connect_timeout` (configurable, default 10s) to SQLAlchemy `connect_args` in `get_engine()` so stalled TCP/SSL handshakes fail fast and existing `run_with_db_retry` can retry or exit.
2. **Non-blocking startup** — Move `init_db()` to a **daemon background thread** started from lifespan; **yield immediately** after `start_scheduler()` so Uvicorn binds the port within seconds.
3. **Observable init state** — Track `DbInitStatus` (pending → running → complete | failed) in `db/connection.py`; expose optional `db_init_status` on `/api/health` (backward-compatible addition).
4. **Preserve behavior** — Same `_run_migrations()` content and retry policy when DB is healthy; `check_db_connection()` and `get_session()` unchanged except benefiting from connect timeout.

## Technical Context

**Language/Version**: Python 3.11+ (existing repo standard)

**Primary Dependencies**: FastAPI, Uvicorn, SQLAlchemy 2.x, psycopg2 (PostgreSQL driver via SQLAlchemy)

**Storage**: PostgreSQL on Railway (unchanged schema/migrations)

**Testing**: pytest — unit tests for connect timeout args and background init state machine; manual deploy validation via quickstart

**Target Platform**: Railway (Nixpacks, `uvicorn api.main:app`, healthcheck `/api/health`, 300s timeout)

**Performance Goals**: HTTP port reachable within **≤10 seconds** of container start under normal conditions; health endpoint responds within **≤60 seconds** even when DB is unreachable

**Constraints**:
- No changes to scoring modules, opportunity discovery, AI matching, or frontend
- Health JSON backward-compatible (additive fields only)
- Scheduler starts synchronously in lifespan (unchanged, fast)
- Background init must be idempotent-safe (single flight)

**Scale/Scope**: Two files primary (`api/main.py`, `db/connection.py`); optional unit test file

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Reference: `.specify/memory/constitution.md` (TenderScope v1.0.0)

| Principle | Gate | Pass? |
|-----------|------|-------|
| I. Transparent AI Scoring | No scoring changes | ✅ N/A |
| II. Claude API Scope | No LLM changes | ✅ N/A |
| III. Location Matching | No location changes | ✅ N/A |
| IV. Consistent API JSON | Health endpoint keeps existing keys; optional additive `db_init_status` | ✅ |
| V. Python-Native Scoring | No scoring changes | ✅ N/A |

No constitution violations — Complexity Tracking not required.

## Project Structure

### Documentation (this feature)

```text
specs/003-fix-railway-startup/
├── plan.md              # This file
├── research.md          # Phase 0
├── data-model.md        # Phase 1
├── quickstart.md        # Phase 1
├── contracts/
│   └── health-api.json
└── tasks.md             # Phase 2 (/speckit-tasks — not yet created)
```

### Source Code (repository root)

```text
api/
└── main.py              # MODIFY: lifespan — background init_db, yield before migrations finish

db/
└── connection.py        # MODIFY: connect_timeout, DbInitStatus, start_init_db_background()

tests/
└── unit/
    └── test_db_startup.py   # NEW: connect args + init status (optional but recommended)

railway.toml             # unchanged (healthcheckPath already /api/health)
```

**Structure Decision**: Minimal surgical change — no new packages, no frontend, no pipeline imports.

## Implementation Phases

### Phase A — Connection Timeout (`db/connection.py`)

1. Extend `_engine_connect_args(url)`:
   - Read `DB_CONNECT_TIMEOUT` env (default `10` seconds, minimum 1)
   - Pass `connect_timeout` to psycopg2 via SQLAlchemy `connect_args`
   - Keep existing `sslmode=require` for Railway hosts

2. Ensure `is_transient_db_error()` treats timeout-related messages as transient (verify `"timeout expired"` already in `TRANSIENT_DB_ERROR_MARKERS` — yes at line 32).

3. Add unit test asserting connect_args include timeout for Railway URLs.

### Phase B — Background Init State (`db/connection.py`)

1. Add module-level thread-safe state:

   ```text
   DbInitStatus: "pending" | "running" | "complete" | "failed"
   _db_init_status: DbInitStatus
   _db_init_error: str | None
   _db_init_lock: threading.Lock
   ```

2. Add public helpers:
   - `get_db_init_status() -> dict` — status, optional error snippet, started_at/completed_at timestamps
   - `start_init_db_background() -> None` — starts daemon thread if not already running/complete; thread calls existing `init_db(raise_on_failure=False)` and updates status

3. Keep `init_db()` synchronous implementation unchanged (same `_run_migrations`, same retries) — background thread invokes it.

4. Optional: `ensure_db_initialized()` hook for future use — **not required** for this feature; endpoints continue using existing `get_session()` + 503 handler.

### Phase C — Lifespan Non-Blocking (`api/main.py`)

1. Replace lifespan body:

   ```text
   BEFORE:
     init_db(raise_on_failure=False)   # blocks
     start_scheduler()
     yield

   AFTER:
     start_scheduler()
     start_init_db_background()
     yield
   ```

2. Extend `/api/health` response with optional additive fields:
   - `db_init_status`: string enum from `get_db_init_status()`
   - `db_init_error`: string | null (only when failed, truncated)

   Existing fields unchanged: `status`, `database_connected`, scheduler flags, anthropic key flag.

3. Health logic when init still running:
   - `database_connected`: result of `check_db_connection()` (fast ping with timeout)
   - `status`: `"ok"` if db_ok else `"degraded"` (unchanged semantics)
   - `db_init_status`: `"running"` until thread completes

### Phase D — Validation

1. Unit tests: connect_timeout present; background init transitions pending→running→complete.
2. Local simulation: invalid `DATABASE_URL` host — port binds, health returns degraded within 60s.
3. Railway deploy: healthcheck passes; logs show `[DB] init_db complete` after startup or clear failure message.

## Explicitly Out of Scope

- Scoring engines, opportunity discovery, AI matching, frontend dashboards
- Migration content changes or new tables
- Lazy-import refactors of `pipeline/` modules
- Railway.toml healthcheck path/timeout changes (unless deploy still fails after fix)
- Multi-worker Uvicorn (`--workers N`) coordination — single worker assumed per Railway service

## Complexity Tracking

> Not applicable — all constitution gates pass without exceptions.
