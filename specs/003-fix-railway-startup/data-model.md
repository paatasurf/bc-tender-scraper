# Data Model: Non-Blocking API Startup

**Feature**: `003-fix-railway-startup` | **Date**: 2026-06-15

This feature introduces **in-process startup state** only. No database schema changes.

## DbInitStatus (in-memory)

Tracks background database initialization lifecycle for observability and health reporting.

| Value | Meaning |
|-------|---------|
| `pending` | Background init not yet started (brief transient at process boot) |
| `running` | Background thread executing `init_db()` / `_run_migrations()` |
| `complete` | Migrations finished successfully |
| `failed` | Migrations exhausted retries or non-transient error |

### State transitions

```text
pending → running → complete
                 ↘ failed
```

- **pending → running**: `start_init_db_background()` spawns daemon thread
- **running → complete**: `init_db()` returns `True`
- **running → failed**: `init_db()` returns `False` or uncaught exception (logged, status set failed)
- **complete** and **failed** are terminal for the process lifetime (no automatic retry loop unless process restarts)

### Fields exposed via `get_db_init_status()`

| Field | Type | Description |
|-------|------|-------------|
| `status` | string | One of `pending`, `running`, `complete`, `failed` |
| `error` | string \| null | Truncated error message when `failed` |
| `started_at` | ISO datetime \| null | When background init thread started |
| `completed_at` | ISO datetime \| null | When init finished (success or failure) |

Thread-safe access via lock; readers never block writers for more than microseconds.

## Health Response (API — additive fields)

Existing entity unchanged; two optional fields added per contract `contracts/health-api.json`:

| Field | Type | Description |
|-------|------|-------------|
| `db_init_status` | string | Mirrors `DbInitStatus.status` |
| `db_init_error` | string \| null | Present when init failed; omitted or null otherwise |

Existing fields retained:

| Field | Type | Description |
|-------|------|-------------|
| `status` | `"ok"` \| `"degraded"` | Overall health |
| `database_connected` | boolean | Live connectivity ping |
| `anthropic_api_key_configured` | boolean | Env present |
| `scheduler_enabled` | boolean | From scheduler config |
| `scheduler_running` | boolean | APScheduler state |

## Configuration (environment)

| Variable | Default | Purpose |
|----------|---------|---------|
| `DB_CONNECT_TIMEOUT` | `10` | Max seconds for TCP/SSL connect attempt |
| `DB_INIT_RETRIES` | `5` | Existing — retry count for init_db |
| `DB_INIT_RETRY_DELAY` | `2.0` | Existing — backoff base |
| `DB_INIT_RETRY_MAX_DELAY` | `5.0` | Existing — backoff cap |

No new database tables, columns, or migrations.
