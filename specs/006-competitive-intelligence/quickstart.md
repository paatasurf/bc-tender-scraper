# Quickstart: Validate Competitive Intelligence (Phase 1)

**Feature**: `006-competitive-intelligence` | **Date**: 2026-06-17

Prerequisites, validation commands, and expected outcomes. See [data-model.md](./data-model.md) and [contracts/competitive-intelligence.json](./contracts/competitive-intelligence.json).

## Prerequisites

- Python 3.11+ with project dependencies installed
- PostgreSQL with `companies`, `arch_companies`, `permits`, `contract_awards` populated
- At least one construction `companies.id` with `total_projects >= 2` and `dominant_sector` set (CIP built or buildable)
- Local API: `uvicorn api.main:app --reload`
- Optional: frontend dev server for UI verification

## 1. Run Unit Tests

```powershell
cd C:\Users\DAVIDSURF\Projects\bc-tender-scraper
pytest tests/unit/test_competitive_overlap.py -v
pytest tests/unit/test_competitive_threat_score.py -v
pytest tests/unit/test_competitive_peers.py -v
pytest tests/unit/test_competitive_benchmark.py -v
```

**Expected**: All tests pass. Threat score tests assert `score == sum(breakdown.points)` on ≥20 fixture pairs. Geo detail tests assert no street-suffix tokens.

## 2. Construction API — Happy Path

```powershell
curl "http://localhost:8000/api/companies/1735/competitive-intelligence?peer_limit=5"
```

Replace `1735` with a valid `companies.id` in a populated sector.

**Expected**:
- HTTP 200
- `"engine_version": "competitive_intel_v1"`
- `benchmark.metrics` has 5 rows with `company` and `market_median` populated
- `top_competitors` length 3–5 when cohort ≥ 3
- Each peer: `threat_score == sum(threat_breakdown.breakdown[].points)`

## 3. Architecture API — Degraded Awards

```powershell
curl "http://localhost:8000/api/arch-companies/42/competitive-intelligence?peer_limit=5"
```

Replace `42` with a valid `arch_companies.id`.

**Expected**:
- HTTP 200, `"kind": "architecture"`
- Benchmark row `award_count` has `not_applicable: true` or `company: null`
- Threat breakdown `award_activity` points = 0, detail contains N/A

## 4. Sparse Market — Empty Peers

```powershell
curl "http://localhost:8000/api/companies/{sparse_id}/competitive-intelligence"
```

Use a company in a thin sector/city with < 3 qualifying peers.

**Expected**:
- HTTP 200 (not error)
- `top_competitors: []`
- `warnings` contains `insufficient_market_data`

## 5. Determinism Check

```powershell
python -c "
import json, urllib.request
url = 'http://localhost:8000/api/companies/1735/competitive-intelligence?peer_limit=5'
r1 = json.load(urllib.request.urlopen(url))
r2 = json.load(urllib.request.urlopen(url))
assert r1['benchmark'] == r2['benchmark']
for a, b in zip(r1['top_competitors'], r2['top_competitors']):
    assert a['threat_score'] == b['threat_score']
    assert a['threat_breakdown'] == b['threat_breakdown']
print('OK: deterministic competitive intelligence')
"
```

**Expected**: `OK: deterministic competitive intelligence`

## 6. Threat Score Sum Invariant (all peers)

```powershell
python -c "
import json, urllib.request
url = 'http://localhost:8000/api/companies/1735/competitive-intelligence?peer_limit=5'
data = json.load(urllib.request.urlopen(url))
for peer in data.get('top_competitors', []):
    tb = peer['threat_breakdown']
    s = sum(c['points'] for c in tb['breakdown'])
    assert s == tb['score'] == peer['threat_score'], peer['name']
print('OK: threat breakdown sums validated')
"
```

**Expected**: `OK: threat breakdown sums validated`

## 7. Frontend Verification

1. Open construction Company Intelligence profile for the same `company_id`.
2. Confirm **Competitive Intelligence** section loads below profile header.
3. Benchmark strip shows You / Market Median / Top-Rival Median for five metrics.
4. Expand a competitor threat breakdown — total matches component sum.
5. Architecture tab: awards row shows N/A.

**Expected**: Single API call in network tab (`competitive-intelligence`); no watchlist UI.

## 8. Regression — BD Section Unchanged

```powershell
curl "http://localhost:8000/api/companies/1735/bd-intelligence?kind=construction"
```

**Expected**: `competitive_intelligence` section still returns award intelligence items (pursuit-linked), unchanged from pre-deploy.
