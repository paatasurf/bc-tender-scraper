# Quickstart: Validate Deterministic Architecture AI Matching

**Feature**: `001-deterministic-ai-match` | **Date**: 2026-06-14

Prerequisites, validation commands, and expected outcomes. See [data-model.md](./data-model.md) and [contracts/ai-matching-architecture-sync.json](./contracts/ai-matching-architecture-sync.json) for schema details.

## Prerequisites

- Python 3.11+ with project dependencies installed
- PostgreSQL with `arch_companies`, `arch_tenders`, `tender_matches` populated
- Local API running: `uvicorn api.main:app --reload` (or Railway staging URL)
- At least one `arch_companies` row with non-empty `project_types` and `neighborhoods`

## 1. Run Unit Tests

```powershell
cd C:\Users\DAVIDSURF\Projects\bc-tender-scraper
pytest tests/unit/test_arch_match_scoring.py -v
```

**Expected**: All tests pass, including invariant `total == sum(components)`.

## 2. Trigger Architecture Sync Match

```powershell
curl -X POST http://localhost:8000/api/ai-matching `
  -H "Content-Type: application/json" `
  -d '{"company_id": 1, "kind": "architecture", "sync": true, "min_score": 0, "limit": 5, "max_tenders": 20}'
```

Replace `company_id` with a valid `arch_companies.id`.

**Expected**:
- HTTP 200, `"status": "complete"`, `"kind": "architecture"`
- Each match includes `breakdown` with seven keys
- For every match: `score == sum(breakdown.*.points)`

## 3. Verify Breakdown Integrity (Python one-liner)

```powershell
python -c "
import json, sys, urllib.request
req = urllib.request.Request('http://localhost:8000/api/ai-matching',
  data=json.dumps({'company_id': 1, 'kind': 'architecture', 'sync': True, 'min_score': 0, 'limit': 10, 'max_tenders': 20}).encode(),
  headers={'Content-Type': 'application/json'}, method='POST')
resp = json.load(urllib.request.urlopen(req))
for m in resp.get('matches', []):
    b = m['breakdown']
    s = sum(b[k]['points'] for k in b)
    assert s == m['score'], f'Mismatch tender {m[\"tender_id\"]}: score={m[\"score\"]} sum={s}'
print('OK:', len(resp['matches']), 'matches validated')
"
```

**Expected**: Prints `OK: N matches validated` with no assertion errors.

## 4. Verify Cache Persists Breakdown

Run the same curl twice within 168 hours.

**Expected**:
- Second response returns identical `score` and `breakdown` per tender
- Database row has `breakdown_json` populated:

```sql
SELECT company_id, tender_id, score, breakdown_json
FROM tender_matches
WHERE company_kind = 'architecture'
ORDER BY created_at DESC
LIMIT 5;
```

## 5. Verify Without Anthropic Key (Optional)

Unset `ANTHROPIC_API_KEY`, restart API, repeat step 2.

**Expected**:
- HTTP 200 (not 503) for architecture sync
- Matches returned with numeric breakdown
- `reasoning` uses deterministic fallback text (not empty)

## 6. Dashboard Smoke (Vercel / Local Frontend)

Open architecture dashboard, trigger AI match for a company.

**Expected**:
- Match tooltip shows non-zero component values when total is high
- "Sum" line in tooltip equals displayed total score
- No case where total > 0 and all breakdown rows show 0

## Failure Indicators

| Symptom | Likely cause |
|---------|--------------|
| score ≠ breakdown sum | Scorer invariant not enforced — check `arch_match_scoring.py` |
| All breakdown zeros, high score | Legacy Claude path still active — verify architecture branch |
| 503 without API key | API gate not relaxed for architecture sync |
| Empty matches | `min_score` too high or no `arch_tenders` in catalog |
