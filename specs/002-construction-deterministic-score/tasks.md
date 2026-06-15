# Tasks: Deterministic Internal Match Scoring (Construction Dashboard)

**Input**: Design documents from `specs/002-construction-deterministic-score/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: Included per spec FR-018 and plan.md (unit tests for construction sum invariant)

**Organization**: Tasks grouped by user story for independent implementation and testing.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: User story label (US1, US2, US3)

## Path Conventions

- Backend root: repo root (`api/`, `pipeline/`, `db/`, `tests/`)
- Frontend: `v0-construction-dashboard/` (construction paths only)

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm feature context and scope boundaries before code changes

- [x] T001 Review `specs/002-construction-deterministic-score/spec.md`, `plan.md`, `research.md`, and contracts in `specs/002-construction-deterministic-score/contracts/` (construction only; no scrapers; architecture unchanged)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Shared scoring utilities extracted from architecture engine — required before construction scorer

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [x] T002 Create `pipeline/scoring/match_scoring_common.py` with shared helpers (`_normalize_text`, `_token_set`, `_parse_date`, `_factor_to_json`, `assert_score_equals_breakdown`, generic 7-key API breakdown mapper)
- [x] T003 Refactor `pipeline/scoring/arch_match_scoring.py` to import shared utilities from `pipeline/scoring/match_scoring_common.py` with **zero scoring behavior change**
- [x] T004 Run regression `pytest tests/unit/test_arch_match_scoring.py -v` and confirm all tests pass after T003

**Checkpoint**: Foundation ready — user story implementation can now begin

---

## Phase 3: User Story 1 - Trustworthy Internal Match Score (Priority: P1) 🎯 MVP

**Goal**: Construction dashboard Internal match score total always equals sum of seven breakdown component points on every tender match

**Independent Test**: `GET /api/companies/id/{id}/opportunities?kind=construction` — for each `type: "tender"` match with `breakdown`, `score == sum(breakdown.*.points)`; construction tooltip headline total equals Sum row

### Tests for User Story 1

- [x] T005 [P] [US1] Create `tests/unit/test_construction_match_scoring.py` with failing fixtures for sum invariant, empty history, and legacy-score-mismatch scenario (tests fail before engine exists)

### Implementation for User Story 1

- [x] T006 [P] [US1] Implement `score_keywords` (max 35 pts) in `pipeline/scoring/construction_match_scoring.py` per `v0-construction-dashboard/lib/tender-match.ts` weights
- [x] T007 [P] [US1] Implement `score_category` (max 20 pts) in `pipeline/scoring/construction_match_scoring.py`
- [x] T008 [P] [US1] Implement `score_specialization` (max 15 pts) using `trade_tags`, `dominant_sector`, `project_types` in `pipeline/scoring/construction_match_scoring.py`
- [x] T009 [P] [US1] Implement `score_location` (max 15 pts) using `neighborhoods` and service-area tokens only — MUST NOT use street address in `pipeline/scoring/construction_match_scoring.py`
- [x] T010 [P] [US1] Implement `score_value_fit` (max 15 pts), `score_reliability` (max 5 pts), and `score_freshness` (max 10 pts) in `pipeline/scoring/construction_match_scoring.py`
- [x] T011 [US1] Implement `ScoredConstructionMatch`, `score_construction_match(company, tender, tender_source)` with sum invariant and 7-key `api_breakdown` in `pipeline/scoring/construction_match_scoring.py`
- [x] T012 [US1] Complete unit tests in `tests/unit/test_construction_match_scoring.py` (all pass; assert `total == sum(components)` on every fixture)
- [x] T013 [US1] Attach deterministic `score` and `breakdown` to construction tender items in `pipeline/opportunity_discovery.py` (`_discover_construction_opportunities` / `_tender_opportunity_item` path)
- [x] T014 [P] [US1] Extend `mapApiOpportunityMatch` to map optional API `breakdown` to `TenderMatchExplanation` in `v0-construction-dashboard/lib/api.ts`
- [x] T015 [US1] Update `opportunityToTenderMatch` for construction tenders to use API score + explanation (remove skip for `source === "ai"`) in `v0-construction-dashboard/lib/api.ts`
- [x] T016 [US1] Update `MatchScoreTooltip` to display total from `sumMatchBreakdown(explanation.breakdown)` when explanation present in `v0-construction-dashboard/components/match-explanation-tooltip.tsx`

**Checkpoint**: User Story 1 complete — construction Discover path and tooltip show aligned total and breakdown

---

## Phase 4: User Story 2 - Transparent Component Explanations (Priority: P2)

**Goal**: Each breakdown component shows non-empty detail text; location reflects city/region fit only

**Independent Test**: Sample construction matches — every component has a `detail` string; location details reference city/region/municipality, never street addresses

### Implementation for User Story 2

- [x] T017 [US2] Audit and harden location component in `pipeline/scoring/construction_match_scoring.py` — reject or exclude street-level tokens from `primary_address` / `google_address` scoring inputs
- [x] T018 [US2] Ensure all seven component scorers in `pipeline/scoring/construction_match_scoring.py` emit human-readable `detail` for zero-point and partial-credit cases (empty history, missing value, expired deadline)

**Checkpoint**: User Story 2 complete — breakdown is auditable and constitution-compliant for location

---

## Phase 5: User Story 3 - Consistent Cached Match Results (Priority: P3)

**Goal**: Hybrid cache and AI sync persist and return identical score + breakdown for construction company–tender pairs

**Independent Test**: Run construction sync twice for same `company_id`; `tender_matches.breakdown_json` populated; second response returns identical `score` and `breakdown`; hybrid `score_tender_pairs` writes deterministic totals

### Implementation for User Story 3

- [x] T019 [US3] Replace `run_construction_company_scorer` / Claude score path with `score_construction_match` in `score_tender_pairs` when `kind=construction` in `pipeline/ai_matching.py`
- [x] T020 [US3] Refactor `run_construction_ai_matching_sync` and `_score_construction_tender_matches` to score all loaded federal + commercial tenders deterministically (remove Claude matcher/scorer for scores) in `pipeline/ai_matching.py`
- [x] T021 [US3] Persist canonical `breakdown_json` and matching `score` on construction upserts via `_upsert_tender_match` in `pipeline/ai_matching.py`
- [x] T022 [US3] On construction cache hit in `get_fresh_cached_match` / `score_tender_pairs`, reconstruct `api_breakdown` from stored `breakdown_json` and skip re-scoring in `pipeline/ai_matching.py`
- [x] T023 [US3] Relax global 503 `ANTHROPIC_API_KEY` check for `sync=true` + `kind=construction` only in `api/main.py` (architecture and async paths unchanged)

**Checkpoint**: User Story 3 complete — cache and AI sync return stable construction score + breakdown

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Constitution compliance, architecture regression guard, validation

- [x] T024 [P] Constitution compliance review per `.specify/memory/constitution.md` — verify construction paths use Python-only scoring and Claude text-only (if narrative enabled)
- [x] T025 [P] Confirm architecture paths in `pipeline/scoring/arch_match_scoring.py`, `pipeline/ai_matching.py` (architecture branches), and architecture dashboard components are unchanged (regression smoke)
- [x] T026 Run quickstart validation in `specs/002-construction-deterministic-score/quickstart.md` (pytest + opportunities API sum check + construction sync + architecture regression curl)
- [x] T027 [P] Verify sample construction responses match `specs/002-construction-deterministic-score/contracts/company-opportunities-construction.json` and `contracts/ai-matching-construction-sync.json`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately
- **Foundational (Phase 2)**: Depends on Setup — **BLOCKS all user stories**
- **User Story 1 (Phase 3)**: Depends on Foundational — **MVP deliverable**
- **User Story 2 (Phase 4)**: Depends on US1 engine (T006–T011); can overlap late US1 if detail strings added during T006–T011
- **User Story 3 (Phase 5)**: Depends on US1 engine (T011); integrates cache/sync paths
- **Polish (Phase 6)**: Depends on US1 minimum; full validation after US3

### User Story Dependencies

- **User Story 1 (P1)**: Can start after Foundational — no dependency on US2/US3
- **User Story 2 (P2)**: Depends on construction engine from US1 — independently testable via unit tests + manual breakdown review
- **User Story 3 (P3)**: Depends on construction engine from US1 — independently testable via sync + cache repeat

### Within Each User Story

- Tests written before engine implementation (T005 before T006)
- Component scorers before `score_construction_match` aggregate (T006–T010 before T011)
- Backend breakdown attachment before frontend mapping (T013 before T014–T016)
- Engine complete before ai_matching integration (T011 before T019–T022)

### Parallel Opportunities

- T006–T010 (component scorers) can run in parallel after T002–T004
- T014 can start once API response shape from T013 is known
- T024, T025, T027 can run in parallel during Polish
- US2 (T017–T018) can run in parallel with US3 backend tasks if engine (T011) is done

---

## Parallel Example: User Story 1

```bash
# Component scorers in parallel (after Foundational):
Task T006: score_keywords in pipeline/scoring/construction_match_scoring.py
Task T007: score_category in pipeline/scoring/construction_match_scoring.py
Task T008: score_specialization in pipeline/scoring/construction_match_scoring.py
Task T009: score_location in pipeline/scoring/construction_match_scoring.py
Task T010: score_value_fit, score_reliability, score_freshness in pipeline/scoring/construction_match_scoring.py

# Frontend mapping in parallel (after T013):
Task T014: mapApiOpportunityMatch in v0-construction-dashboard/lib/api.ts
Task T016: MatchScoreTooltip in v0-construction-dashboard/components/match-explanation-tooltip.tsx
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup
2. Complete Phase 2: Foundational (shared scoring utils + arch regression)
3. Complete Phase 3: User Story 1 (engine + Discover API + frontend tooltip)
4. **STOP and VALIDATE**: quickstart steps 1–4 + construction dashboard tooltip check
5. Deploy/demo if ready

### Incremental Delivery

1. Setup + Foundational → shared engine foundation
2. User Story 1 → Discover + tooltip fixed (MVP)
3. User Story 2 → detail/location hardening
4. User Story 3 → hybrid cache + AI sync aligned
5. Polish → full quickstart + contract validation

### Parallel Team Strategy

1. Team completes Setup + Foundational together
2. Once Foundational is done:
   - Developer A: US1 backend (T005–T013)
   - Developer B: US1 frontend (T014–T016, after T013)
3. After US1 MVP validated:
   - Developer A: US3 cache/sync (T019–T023)
   - Developer B: US2 detail audit (T017–T018)

---

## Notes

- Do **not** modify scrapers, `opportunity_discovery` architecture branches, or architecture dashboard components
- `breakdown_json` column already exists from feature 001 — no new migration required
- Legacy construction `tender_matches` rows without breakdown should be rescored on next cache touch or treated as cache miss
- Commit after each task or logical group; stop at any checkpoint to validate independently
