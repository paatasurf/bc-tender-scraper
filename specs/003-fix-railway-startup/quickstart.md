# Quickstart: Validate Non-Blocking API Startup

**Feature**: `003-fix-railway-startup` | **Date**: 2026-06-15

Prerequisites, validation commands, and expected outcomes. See [data-model.md](./data-model.md) and [contracts/health-api.json](./contracts/health-api.json).

## Prerequisites

- Python 3.11+ with project dependencies installed
- Local PostgreSQL optional (tests cover timeout args without live DB)
- For full validation: Railway project with linked Postgres

## 1. Run Unit Tests

```powershell
cd C:\Users\DAVIDSURF\Projects\bc-tender-scraper
pytest tests/unit/test_db_startup.py -v
```

**Expected**: All tests pass — connect_timeout present in engine args; background init status transitions correctly (mocked).

## 2. Local Startup — Healthy Database

```powershell
uvicorn api.main:app --host 127.0.0.1 --port 8000
```

**Expected** (within ~10 seconds of start):

- Log line: `Application startup complete`
- Log line: `Uvicorn running on http://127.0.0.1:8000`
- Background log (may appear shortly after): `[DB] init_db complete`

```powershell
curl http://127.0.0.1:8000/api/health
```

**Expected JSON** (fields per contract):

- `status`: `"ok"`
- `database_connected`: `true`
- `db_init_status`: `"complete"` (after migrations finish)

## 3. Local Startup — Unreachable Database (Fast-Fail)

Temporarily point to invalid host:

```powershell
$env:DATABASE_URL = "postgresql://user:pass@192.0.2.1:5432/nonexistent"
uvicorn api.main:app --host 127.0.0.1 --port 8000
```

**Expected**:

- Uvicorn binds port within **≤60 seconds** (not infinite hang)
- `curl http://127.0.0.1:8000/api/health` returns JSON with:
  - `status`: `"degraded"`
  - `database_connected`: `false`
  - `db_init_status`: `"failed"` or `"running"` then `"failed"`
- Logs show connection timeout or retry messages — not silent freeze

## 4. Railway Deploy Validation

Push branch with fix; trigger deploy.

**Expected deploy logs**:

```text
INFO:     Started server process
INFO:     Waiting for application startup.
INFO:     Application startup complete
INFO:     Uvicorn running on http://0.0.0.0:$PORT
[DB] init_db complete   (may appear after startup complete — OK)
```

**Expected Railway status**: Deploy **healthy** (healthcheck `/api/health` returns 200).

```powershell
curl https://<your-railway-api-domain>/api/health
```

## 5. Regression Smoke (Healthy DB)

After deploy with working database:

```powershell
curl "https://<domain>/api/health"
curl "https://<domain>/api/companies/id/1735/opportunities?kind=construction&min_score=0&limit=3"
```

**Expected**: Health ok; opportunities endpoint returns 200 with same JSON shape as pre-fix deploy. Scoring totals unchanged (feature 001/002 regression guard).

## 6. Connect Timeout Env Override (Optional)

```powershell
$env:DB_CONNECT_TIMEOUT = "5"
uvicorn api.main:app --host 127.0.0.1 --port 8000
```

**Expected**: Failed connections abort faster; startup still completes and health responds.
