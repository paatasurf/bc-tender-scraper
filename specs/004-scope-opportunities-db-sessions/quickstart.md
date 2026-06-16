# Quickstart: Validate Scoped DB Sessions for Opportunities Discovery

**Feature**: `004-scope-opportunities-db-sessions` | **Date**: 2026-06-16

Prerequisites, validation commands, and expected outcomes. See [data-model.md](./data-model.md) and [contracts/opportunities-discovery-response.json](./contracts/opportunities-discovery-response.json).

## Prerequisites

- Python 3.11+ with project dependencies installed
- PostgreSQL with production-like data (local or Railway)
- Baseline JSON snapshots captured **before** refactor (see §2)

## 1. Run Unit Tests

```powershell
cd C:\Users\DAVIDSURF\Projects\bc-tender-scraper
pytest tests/unit/test_opportunities_session_phases.py tests/unit/test_opportunities_parity.py -v
```

**Expected**:

- Session phase tests: `session.close()` invoked between phases (mocked)
- Parity tests: match IDs, scores, and order identical to baseline fixtures

## 2. Capture Baseline (Before Refactor)

```powershell
curl -o baseline-construction-1921.json `
  "https://bc-tender-scraper-production.up.railway.app/api/companies/1921/opportunities?min_score=50&limit=15"

curl -o baseline-arch-19.json `
  "https://bc-tender-scraper-production.up.railway.app/api/arch-companies/19/opportunities?min_score=40&limit=15"
```

Store under `tests/fixtures/opportunities/` for parity tests.

## 3. Local Single-Request Smoke

```powershell
uvicorn api.main:app --host 127.0.0.1 --port 8000
```

```powershell
curl "http://127.0.0.1:8000/api/companies/1921/opportunities?min_score=50&limit=15"
curl "http://127.0.0.1:8000/api/health"
```

**Expected**:

- Opportunities returns 200 with same JSON shape as baseline
- Logs include `[OpportunityDiscovery] ... db_phases_total=` under 10 seconds

## 4. Concurrent Load Test (Pool Exhaustion Guard)

```powershell
python scripts/verify_opportunities_concurrent.py
```

(Script to add during implementation: 5 parallel discover requests + 10 health/permits probes.)

**Expected**:

- All 5 discover requests return 200
- Health and permits requests return 200 within 5 seconds during discover burst
- No `QueuePool limit` in API logs

## 5. Production Validation

After deploy to Railway:

```powershell
python scripts/verify_company_opportunities_deploy.py
```

**Expected**:

- `status=200` for company 1921
- `response_time` under 60 seconds (target: under 30 after fix)
- `total_candidates` and `final_matches` populated

Concurrent check:

```powershell
curl https://<domain>/api/permits?limit=10
```

Run while discover is in flight (manual or scripted). **Expected**: 200, not 30s timeout.

## 6. Parity Regression

```powershell
pytest tests/unit/test_opportunities_parity.py -v --baseline-dir tests/fixtures/opportunities
```

**Expected**: Zero diff in match `(type, id, score)` tuples and list order vs baseline files.

## 7. What NOT to Validate Here

- Pool size increases — out of scope
- Scoring algorithm changes — forbidden
- Frontend/Vercel proxy changes — out of scope
