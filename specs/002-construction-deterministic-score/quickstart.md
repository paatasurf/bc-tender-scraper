# Quickstart: Validate Construction Deterministic Internal Match Scoring

**Feature**: `002-construction-deterministic-score` | **Date**: 2026-06-15

Prerequisites, validation commands, and expected outcomes. See [data-model.md](./data-model.md) and [contracts/](./contracts/) for schema details.

## Prerequisites

- Python 3.11+ with project dependencies installed
- PostgreSQL with `companies`, `tenders`, `commercial_tenders`, `tender_matches` populated
- Local API: `uvicorn api.main:app --reload` (or Railway staging URL)
- At least one `companies` row with `total_projects > 0` or permit/award history
- Frontend dev server optional for UI verification

## 1. Run Unit Tests

```powershell
cd C:\Users\DAVIDSURF\Projects\bc-tender-scraper
pytest tests/unit/test_construction_match_scoring.py -v
pytest tests/unit/test_arch_match_scoring.py -v
```

**Expected**: All tests pass. Construction tests assert `total == sum(components)` on every fixture. Architecture tests unchanged (regression guard).

## 2. Validate Discover Opportunities (Construction Dashboard Path)

```powershell
curl "http://localhost:8000/api/companies/id/1735/opportunities?kind=construction&min_score=0&limit=15"
```

Replace `1735` with a valid `companies.id`.

**Expected**:
- HTTP 200, `"kind": "construction"`
- Each `type: "tender"` match includes `breakdown` with seven keys
- For every tender match: `score == sum(breakdown.*.points)`

## 3. Validate Construction AI Matching Sync

```powershell
curl -X POST http://localhost:8000/api/ai-matching `
  -H "Content-Type: application/json" `
  -d '{"company_id": 1735, "kind": "construction", "sync": true, "min_score": 0, "limit": 5, "max_tenders": 20}'
```

**Expected**:
- HTTP 200 without `ANTHROPIC_API_KEY` (deterministic scoring)
- `"kind": "construction"`, each match has `breakdown`
- Score/breakdown sum invariant on all matches

## 4. Integrity Check (Python one-liner)

```powershell
python -c "
import json, urllib.request
url = 'http://localhost:8000/api/companies/id/1735/opportunities?kind=construction&min_score=0&limit=15'
data = json.load(urllib.request.urlopen(url))
for m in data.get('matches', []):
    if m.get('type') != 'tender' or 'breakdown' not in m:
        continue
    b = m['breakdown']
    s = sum(b[k]['points'] for k in b)
    assert s == m['score'], f'Mismatch id={m[\"id\"]}: score={m[\"score\"]} sum={s}'
print('OK: construction opportunity matches validated')
"
```

**Expected**: `OK: construction opportunity matches validated` with no assertion errors.

## 5. Frontend Verification (Construction Tab Only)

1. Open construction Company Intelligence for the same company.
2. Expand **Internal match score** tooltip on a tender opportunity.
3. Confirm headline total equals **Sum** row at bottom of breakdown.
4. Switch to Architecture tab — confirm behavior unchanged from pre-deploy.

**Expected**: Construction totals match sums on every tender card; architecture unaffected.

## 6. Regression — Architecture Unchanged

```powershell
curl -X POST http://localhost:8000/api/ai-matching `
  -H "Content-Type: application/json" `
  -d '{"company_id": 1, "kind": "architecture", "sync": true, "min_score": 0, "limit": 3, "max_tenders": 10}'
```

**Expected**: Same score/breakdown invariant as feature 001; no construction-specific keys or behavior changes.
