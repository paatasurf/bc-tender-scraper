# Tasks: Competitive Intelligence (Phase 1)

**Input**: [spec.md](./spec.md) | [plan.md](./plan.md) | [data-model.md](./data-model.md) | [contracts/](./contracts/)

**Scope**: Benchmark Strip, Top Competitors, Threat Score only. No watchlists, digests, new tables, or Phase 2 features.

**Estimated effort**: 8–10 dev-days (see [plan.md](./plan.md#effort-summary))

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: US1 = Benchmark, US2 = Top Competitors, US3 = Threat Score, US4 = Architecture degradation

---

## Phase 1: Setup (0.25d)

**Purpose**: Module scaffold and shared types

- [ ] T001 Create `pipeline/competitive_intel/__init__.py` exporting public API
- [ ] T002 [P] Add `ThreatScoreResult`, `MarketCohort`, `TopCompetitor` dataclasses in `pipeline/competitive_intel/types.py` (or inline in respective modules per plan)

---

## Phase 2: Foundational — Overlap & Activity (1.75d)

**Purpose**: Pure scoring primitives — blocks threat score and peer selection

**⚠️ CRITICAL**: No user story work until T003–T008 pass unit tests

- [ ] T003 [P] Implement `overlap.py`: `city_set`, `geographic_overlap_raw`, `category_overlap_raw`, `value_overlap_raw`, `similarity_pre_score` in `pipeline/competitive_intel/overlap.py`
- [ ] T004 [P] Implement `activity.py`: `award_count_90d`, `permit_count_90d`, `recency_score`, `cohort_p90`, `buyer_overlap_bonus` in `pipeline/competitive_intel/activity.py`
- [ ] T005 [P] Unit tests `tests/unit/test_competitive_overlap.py` — geo city-only details, Bhattacharyya, value log-distance, similarity weights
- [ ] T006 [P] Unit tests `tests/unit/test_competitive_activity.py` — 90d counts, 500-row cap, recency decay
- [ ] T007 Wire reuse imports: `normalize_vendor_name` from `pipeline/company_matching.py`, `_parse_city_from_address` from `pipeline/scoring/construction_match_scoring.py`, tokenize pattern from `pipeline/opportunity_discovery.py`

**Checkpoint**: Overlap and activity functions tested in isolation

---

## Phase 3: User Story 3 — Threat Score (Priority: P2, built before peers) (1.25d)

**Goal**: Deterministic 0–100 score with five-component breakdown summing to total

**Independent Test**: `compute_threat_score(fixture_subject, fixture_peer)` → `score == sum(breakdown.points)`; deterministic across calls

### Implementation

- [ ] T008 [US3] Implement `compute_threat_score()` in `pipeline/competitive_intel/threat_score.py` using `weighted_fit()` from `pipeline/scoring/explain.py`
- [ ] T009 [US3] Add architecture branch: award_activity = 0, N/A detail, confidence on 4 components
- [ ] T010 [US3] Add `confidence_label()` per FR-026 (high ≥4 raw>0, medium 2–3, low otherwise)
- [ ] T011 [US3] Unit tests `tests/unit/test_competitive_threat_score.py` — ≥20 fixture pairs, sum invariant, no street tokens in geo detail, arch N/A

**Checkpoint**: Threat score engine complete and constitution-compliant

---

## Phase 4: User Story 2 — Top Competitors (Priority: P1) (1.75d)

**Goal**: Auto-select 3–5 peers via cohort → similarity → threat ranking

**Independent Test**: API returns 3–5 peers for populated Vancouver GC; empty + warning when cohort < 3

### Implementation

- [ ] T012 [US2] Implement `build_market_cohort()` and `filter_peer_candidates()` in `pipeline/competitive_intel/cohort.py` — sector/trade, city gate, quality gate, widen if < 8
- [ ] T013 [US2] Implement `rank_by_similarity()` and `select_top_competitors()` in `pipeline/competitive_intel/peers.py` — top 20 pre-score, threat on 20, permit scan final 5 only
- [ ] T014 [US2] Emit `insufficient_market_data` warning when peers < 3
- [ ] T015 [US2] Unit tests `tests/unit/test_competitive_peers.py` — subject excluded, tie-break total_value, peer_limit clamp
- [ ] T016 [P] [US2] Unit tests `tests/unit/test_competitive_cohort.py` — widening rule, quality gate

**Checkpoint**: Peer selection returns ranked competitors with embedded threat scores

---

## Phase 5: User Story 1 — Benchmark Strip (Priority: P1) (0.75d)

**Goal**: You vs Market Median vs Top-Rival Median for five metrics

**Independent Test**: Benchmark response has 5 metrics; medians match hand-computed cohort values

### Implementation

- [ ] T017 [US1] Implement `compute_benchmark_strip()` in `pipeline/competitive_intel/benchmark.py`
- [ ] T018 [US1] Handle null `ai_reliability_score` in median calculation
- [ ] T019 [US1] Unit tests `tests/unit/test_competitive_benchmark.py` — five keys, arch award N/A

**Checkpoint**: Benchmark strip computable given cohort + peers

---

## Phase 6: Orchestration & API (1.0d)

**Goal**: Single endpoint serves all three features in one response

- [ ] T020 Implement `get_competitive_intelligence()` in `pipeline/competitive_intel/service.py` — orchestrate cohort → peers → benchmark
- [ ] T021 [P] Integration test `tests/unit/test_competitive_service.py` — full pipeline with mocked session fixtures
- [ ] T022 Add `GET /api/companies/{company_id}/competitive-intelligence` in `api/main.py` — `peer_limit` clamp [3,5]
- [ ] T023 Add `GET /api/arch-companies/{company_id}/competitive-intelligence` in `api/main.py`
- [ ] T024 Wire `get_cip()` from `pipeline/cip_builder.py` with optional `refresh` query param

**Checkpoint**: curl returns valid JSON matching [contracts/competitive-intelligence.json](./contracts/competitive-intelligence.json)

---

## Phase 7: User Story 4 — Architecture Degradation (Priority: P3) (0.25d)

**Goal**: Architecture profiles work with awards N/A

- [ ] T025 [US4] Verify arch route uses `arch_companies` table and `kind=architecture` throughout service
- [ ] T026 [US4] Add arch-specific test cases in `test_competitive_benchmark.py` and `test_competitive_threat_score.py`

**Checkpoint**: Arch API passes quickstart §3

---

## Phase 8: Dashboard UI (2.0d)

**Goal**: Profile shows benchmark strip + competitor cards from single API call

- [ ] T027 [P] Add `lib/competitive-intelligence.ts` — types mirroring API contract, `fetchCompetitiveIntelligence(companyId, kind)`
- [ ] T028 Create `components/competitive-intelligence-panel.tsx` — benchmark table, competitor cards, threat breakdown accordion
- [ ] T029 Insert panel in `components/company-intelligence-dashboard.tsx` after profile header (~line 1200)
- [ ] T030 Add loading skeleton, empty state (`insufficient_market_data`), deterministic footnote
- [ ] T031 Add above/below median visual indicators on benchmark rows
- [ ] T032 Manual UI pass: construction + architecture tabs per [quickstart.md](./quickstart.md) §7

**Checkpoint**: Profile loads competitive section in one network request

---

## Phase 9: Polish & Validation (0.5d)

- [ ] T033 [P] Run full [quickstart.md](./quickstart.md) validation script
- [ ] T034 [P] Constitution review: no LLM imports in `pipeline/competitive_intel/`
- [ ] T035 [P] Verify BD `/bd-intelligence` `competitive_intelligence` section unchanged (regression)
- [ ] T036 Document `engine_version` in API response; bump `v0-construction-dashboard` submodule in parent repo

---

## Dependencies & Execution Order

```text
T001–T002 (setup)
    ↓
T003–T007 (overlap + activity) — BLOCKS ALL STORIES
    ↓
T008–T011 (threat score)
    ↓
T012–T016 (peers) ──┐
T017–T019 (benchmark) ──┤ parallel after threat score
    ↓
T020–T024 (service + API)
    ↓
T025–T026 (arch verification)
    ↓
T027–T032 (UI)
    ↓
T033–T036 (polish)
```

### User Story → Task Mapping

| Story | Tasks | MVP alone? |
|-------|-------|------------|
| US1 Benchmark | T017–T019, T020, T022–T024, T027–T032 | Needs API + peers for top-rival median |
| US2 Top Competitors | T012–T016, T008–T011, T020–T024, T027–T032 | Needs threat score embedded |
| US3 Threat Score | T008–T011 | Testable via unit tests before API |
| US4 Arch degrade | T009, T025–T026 | Cross-cutting |

### MVP recommendation

**Minimum shippable increment**: Phases 1–6 (API complete) — testable via curl/quickstart without UI.  
**Full Phase 1**: Through Phase 8 (profile UI).

---

## Parallel Example

After T007 completes:

```bash
# Backend track A
T008 → T012 → T017 → T020 → T022

# Backend track B (tests in parallel)
T005, T006, T011, T015, T016, T019

# Frontend (after T024)
T027, T028 → T029
```

---

## Implementation Strategy

1. **Week 1 (days 1–5)**: T001–T024 — backend complete, quickstart §1–6 pass
2. **Week 2 (days 6–9)**: T027–T036 — dashboard + polish + submodule bump

Stop after T024 to validate API in staging before UI merge.

---

## Notes

- Do not add `competitor_watchlist`, `competitive_alerts`, or migration files
- Do not modify `bd_recommendations.competitive_intelligence` behavior
- Permit scan: enforce `cap=500` in `activity.py` — hard requirement
- All threat scores must pass sum invariant in CI
