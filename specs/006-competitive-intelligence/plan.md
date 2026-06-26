# Implementation Plan: Competitive Intelligence (Phase 1)

**Branch**: `006-competitive-intelligence` | **Date**: 2026-06-17 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/006-competitive-intelligence/spec.md`

**User constraints**: Phase 1 only — Benchmark Strip, Top Competitors, Threat Score. **No** watchlists, email digests, missed opportunities, market dashboard, new tables, new data sources, or migrations. Compute-on-read from `companies`, `arch_companies`, `permits`, `contract_awards`, `cip_json`.

## Summary

Add a deterministic competitive-intelligence layer on company profiles: users see how they benchmark against their market, who their top 3–5 rivals are (auto-selected), and an explainable 0–100 Threat Score per rival. One API call powers the dashboard section.

**Approach**: New `pipeline/competitive_intel/` package with pure overlap/activity functions, cohort filtering, peer ranking, and benchmark medians. Orchestrated by `get_competitive_intelligence()` and exposed via two FastAPI routes. Dashboard adds `CompetitiveIntelligencePanel` to `company-intelligence-dashboard.tsx`. Reuses `get_cip()`, `weighted_fit()`, `BreakdownFactor`, `normalize_vendor_name()`, and city parsing — no AI calls.

## Technical Context

**Language/Version**: Python 3.11+ (existing repo standard)

**Primary Dependencies**: FastAPI, SQLAlchemy, existing `pipeline/cip_builder`, `pipeline/scoring/explain`, `pipeline/company_matching`

**Storage**: PostgreSQL on Railway — read-only access to existing tables; optional `cip_json` write via existing `persist_cip()` on first `get_cip()` call

**Testing**: pytest — `tests/unit/test_competitive_*.py`; manual validation via [quickstart.md](./quickstart.md)

**Target Platform**: Railway API; Vercel frontend (`v0-construction-dashboard/`)

**Performance Goals**: p95 < 2s for companies with warm `cip_json`, cohort ≤ 200, permit scan on ≤ 5 peers (SC-006)

**Constraints**:
- No new tables, migrations, or external data feeds
- City-level geo only in threat explanations (constitution III)
- Threat total = sum of five breakdown components (constitution I)
- Architecture: awards N/A; four-component threat scoring
- BD `competitive_intelligence` award feed unchanged

**Scale/Scope**: Company profile page only; 3 features; 2 API routes; 1 dashboard panel

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Reference: `.specify/memory/constitution.md` (TenderScope v1.0.0)

| Principle | Gate | Pass? |
|-----------|------|-------|
| I. Transparent AI Scoring | Threat score = sum of 5 component points; breakdown in API/UI | ✅ |
| II. Claude API Scope | Zero LLM calls in competitive_intel pipeline | ✅ |
| III. Location Matching | City/region via CIP service_cities + concentration_map only | ✅ |
| IV. Consistent API JSON | snake_case fields; matches sibling company endpoint patterns | ✅ |
| V. Python-Native Scoring | All logic in `pipeline/competitive_intel/` | ✅ |

No constitution violations — Complexity Tracking not required.

## Project Structure

### Documentation (this feature)

```text
specs/006-competitive-intelligence/
├── plan.md              # This file
├── research.md          # Phase 0
├── data-model.md        # Phase 1
├── quickstart.md        # Phase 1
├── contracts/
│   └── competitive-intelligence.json
├── tasks.md             # Task breakdown
└── checklists/
    └── requirements.md
```

### Source Code (repository root)

```text
pipeline/competitive_intel/
├── __init__.py
├── overlap.py           # geo, category, value overlap (pure)
├── activity.py          # award_90d, permit_90d, recency
├── threat_score.py      # compute_threat_score(), ThreatScoreResult
├── cohort.py            # build_market_cohort(), filter_peer_candidates()
├── peers.py             # similarity pre-score, select_top_competitors()
├── benchmark.py         # compute_benchmark_strip(), median helpers
└── service.py           # get_competitive_intelligence() orchestrator

api/
└── main.py              # ADD: GET .../competitive-intelligence (×2 routes)

v0-construction-dashboard/
├── lib/competitive-intelligence.ts   # types + fetch helper
├── components/competitive-intelligence-panel.tsx
└── components/company-intelligence-dashboard.tsx  # INSERT panel

tests/unit/
├── test_competitive_overlap.py
├── test_competitive_threat_score.py
├── test_competitive_peers.py
├── test_competitive_benchmark.py
└── test_competitive_service.py
```

**Structure Decision**: Monorepo — FastAPI at repo root; frontend in `v0-construction-dashboard/` submodule. Competitive logic isolated under `pipeline/competitive_intel/` per spec module layout.

## Implementation Order

Strict sequence minimizes rework. Backend is API-complete before UI.

| Step | Deliverable | Depends on | Est. |
|------|-------------|------------|------|
| **1** | `overlap.py` + unit tests | — | 1.0d |
| **2** | `activity.py` + unit tests | overlap utils | 0.75d |
| **3** | `threat_score.py` + unit tests | overlap, activity, explain.py | 1.0d |
| **4** | `cohort.py` + unit tests | get_cip | 0.75d |
| **5** | `peers.py` + unit tests | cohort, overlap, threat_score | 1.0d |
| **6** | `benchmark.py` + unit tests | cohort, peers output | 0.5d |
| **7** | `service.py` + integration test | all above | 0.5d |
| **8** | API routes in `main.py` | service | 0.25d |
| **9** | Dashboard panel + wire-up | API | 2.0d |
| **10** | quickstart validation + polish | all | 0.5d |

**Total estimated effort: 8.25 dev-days** (round to **8–10 days** with review buffer).

### Parallelization opportunities

- Steps 1–2 can overlap (different files) once overlap signatures are agreed.
- Steps 4 and 3 can run in parallel after step 1 completes.
- Dashboard step 9 can start with mocked JSON after step 7 merges.

## Implementation Phases (detail)

### Phase A — Overlap & Activity Primitives

**`overlap.py`**
- `city_set(cip, company_row) -> set[str]` — service_cities ∪ concentration_map geos ∪ primary_city
- `geographic_overlap_raw(subject, peer) -> tuple[float, str]` — 60/40 Jaccard + bonus
- `category_overlap_raw(subject_cip, peer_cip) -> tuple[float, str]` — Bhattacharyya + fallback
- `value_overlap_raw(subject_cip, peer_cip, subject_row, peer_row) -> tuple[float, str]` — log-distance + band bonus
- `similarity_pre_score(geo, cat, val) -> float` — 0.35/0.35/0.30 weighted

**`activity.py`**
- `award_count_90d(session, company_id) -> int`
- `permit_count_90d(session, normalized_name, cap=500) -> int`
- `recency_score(last_project_date: str) -> float` — linear decay 365d
- `cohort_p90(values: list[int]) -> float`
- `buyer_overlap_bonus(clients_a, clients_b) -> float` — Jaccard

### Phase B — Threat Score

**`threat_score.py`**
- `compute_threat_score(subject, peer, *, kind, session, cohort_stats) -> ThreatScoreResult`
- Map raw 0–100 → max_points via `weighted_fit()`:
  - (geographic_overlap, 25), (category_overlap, 25), (value_overlap, 20), (award_activity, 15), (permit_activity, 15)
- `confidence_label(raw_components) -> str`
- `assert sum(points) == score` before return
- Architecture branch: skip award raw computation

### Phase C — Cohort & Peers

**`cohort.py`**
- `build_market_cohort(session, subject, cip, kind) -> MarketCohort`
- SQL filter on `dominant_sector` / `primary_trade` / `primary_city`
- Widen if `cohort_size < 8`
- `filter_peer_candidates(cohort, subject_id, limit=200)`

**`peers.py`**
- `rank_by_similarity(candidates, subject_cip) -> top 20`
- `select_top_competitors(session, subject, cip, cohort, peer_limit) -> list[TopCompetitor]`
- Full threat score on top 20; permit scan only when computing final top 5
- Tie-break `total_value DESC`
- Empty + warning if `< 3` peers

### Phase D — Benchmark

**`benchmark.py`**
- `compute_benchmark_strip(subject, cohort, peers, kind) -> dict`
- Five metrics per spec; `statistics.median` with null filtering for reliability
- `top_competitor_median` from returned peers only

### Phase E — Service & API

**`service.py`**
```python
def get_competitive_intelligence(
    session, *, company_id: int, kind: Kind, peer_limit: int = 5
) -> dict[str, Any]:
```

**`api/main.py`**
```python
@app.get("/api/companies/{company_id}/competitive-intelligence")
@app.get("/api/arch-companies/{company_id}/competitive-intelligence")
```
- Clamp `peer_limit` to [3, 5]
- 404 on missing company
- 200 with warnings on sparse market

### Phase F — Dashboard UI

**`competitive-intelligence-panel.tsx`**
- Benchmark table (3 columns + delta indicators)
- Competitor cards sorted by `threat_score`
- Expandable breakdown (reuse BD/tender breakdown bar pattern)
- Empty state for `insufficient_market_data`
- Footnote: deterministic scoring sources

**Wire into** `company-intelligence-dashboard.tsx` after header section (~line 1200).

## Effort Summary

| Area | Days | % |
|------|------|---|
| Backend primitives (overlap, activity, threat) | 2.75 | 33% |
| Cohort, peers, benchmark, service | 2.75 | 33% |
| API + tests | 1.0 | 12% |
| Dashboard UI | 2.0 | 24% |
| Validation / polish | 0.5 | 6% |
| **Total** | **9.0** | |

Single developer, sequential: **~2 calendar weeks**. With parallel backend/UI: **~1.5 weeks**.

## Risk Register

| Risk | Mitigation |
|------|------------|
| Permit scan latency | Cap 500 rows; scan top-5 peers only |
| Cold CIP build slow | Accept first-hit delay; document in API `computed_at` |
| Sparse arch cohort | Widen + empty state; awards N/A |
| Submodule deploy | Bump `v0-construction-dashboard` in parent repo after UI merge |

## Out of Scope (reconfirmed)

Watchlists, alerts, email digests, missed opportunities, market positioning dashboard, `tender_matches` collision, bid tabulations, caching tables, Claude explanations.

## Next Commands

- `/speckit-implement` — execute [tasks.md](./tasks.md)
- `/speckit-analyze` — cross-artifact consistency check after tasks complete
