# Tasks: Deterministic AI Match Scoring (Architecture Dashboard)

**Input**: Design documents from `specs/001-deterministic-ai-match/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: Included per plan.md (unit tests for scoring engine sum invariant and edge cases)

**Organization**: Tasks grouped by user story for independent implementation and testing.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: User story label (US1, US2, US3)

## Path Conventions

- Backend root: repo root (`api/`, `pipeline/`, `db/`, `tests/`)
- Frontend out of scope — API maps to existing 7-key breakdown

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm feature context and design alignment before code changes

- [x] T001 Review `specs/001-deterministic-ai-match/spec.md`, `plan.md`, and `contracts/ai-matching-architecture-sync.json` for scope boundaries (architecture sync only; no scraper/other endpoint changes)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Database schema and model changes required before scoring integration

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [x] T002 Add `_migrate_tender_matches_breakdown_json` migration (`ALTER TABLE tender_matches ADD COLUMN IF NOT EXISTS breakdown_json JSONB`) in `db/connection.py`
- [x] T003 Register `_migrate_tender_matches_breakdown_json` in the schema init sequence (alongside `_migrate_tender_matches_company_kind`) in `db/connection.py`
- [x] T004 Add `breakdown_json: Mapped[dict | None]` JSONB column to `TenderMatch` in `db/models.py`
- [x] T005 [P] Create `pipeline/scoring/arch_match_scoring.py` module skeleton with `ScoredArchMatch` dataclass, shared token-normalization helpers, and `BreakdownFactor` imports from `pipeline/scoring/explain.py`

**Checkpoint**: Foundation ready — user story implementation can now begin

---

## Phase 3: User Story 1 - Trustworthy Match Score (Priority: P1) 🎯 MVP

**Goal**: Deterministic 0–100 scores where total always equals sum of five weighted components, returned in `POST /api/ai-matching` architecture sync responses with 7-key API breakdown mapping

**Independent Test**: POST `{"company_id": N, "kind": "architecture", "sync": true, "min_score": 0, "limit": 10}` — for every match, `score == sum(breakdown.*.points)` and no high total with all-zero components

### Implementation for User Story 1

- [x] T006 [P] [US1] Implement `score_project_type` (max 40 pts) using `arch_companies.project_types`, `houzz_project_types`, and `total_projects` in `pipeline/scoring/arch_match_scoring.py`
- [x] T007 [P] [US1] Implement `score_specialization` (max 25 pts) using tender category vs `website_specializations`, `dominant_sector`, `trade_tags` in `pipeline/scoring/arch_match_scoring.py`
- [x] T008 [P] [US1] Implement `score_region` (max 15 pts) using city/district matching from `neighborhoods`, `houzz_service_areas`, `website_service_areas` — MUST NOT use `google_address`, `lat`, or `lng` in `pipeline/scoring/arch_match_scoring.py`
- [x] T009 [P] [US1] Implement `score_value_fit` (max 10 pts) parsing `arch_tenders.value` via existing `_parse_value` and comparing to `avg_project_value`, `value_p25`, `value_p75` in `pipeline/scoring/arch_match_scoring.py`
- [x] T010 [P] [US1] Implement `score_freshness` (max 10 pts) from `arch_tenders.deadline` tier rules in `pipeline/scoring/arch_match_scoring.py`
- [x] T011 [US1] Implement `score_architecture_match(company, tender)` with sum invariant assertion and `to_api_breakdown()` mapping 5 components → 7-key API shape per `specs/001-deterministic-ai-match/research.md` in `pipeline/scoring/arch_match_scoring.py`
- [x] T012 [US1] Add unit tests for sum invariant, empty project history, expired deadline, missing value, and no region overlap in `tests/unit/test_arch_match_scoring.py`
- [x] T013 [US1] Refactor `_score_company_tender_matches` architecture path: remove `run_tender_matcher` and `run_company_scorer` calls; score all loaded `arch_tenders` via `score_architecture_match` in `pipeline/ai_matching.py`
- [x] T014 [US1] Ensure architecture results always include `breakdown` via `_match_result_dict` and deterministic `match_reason` from top components in `pipeline/ai_matching.py`
- [x] T015 [US1] Relax global 503 `ANTHROPIC_API_KEY` check for `sync=true` + `kind=architecture` only in `api/main.py` (construction sync and async paths unchanged)

**Checkpoint**: User Story 1 complete — architecture sync returns trustworthy decomposable scores without Claude

---

## Phase 4: User Story 2 - Narrative Explanation (Priority: P2)

**Goal**: Human-readable `reasoning` text generated after scoring; Claude used for text only with deterministic fallback

**Independent Test**: Match with known breakdown returns non-empty `reasoning` referencing dominant factors; with `ANTHROPIC_API_KEY` unset, fallback explanation still returned alongside full breakdown

### Implementation for User Story 2

- [x] T016 [P] [US2] Implement `build_arch_match_fallback_explanation(breakdown)` using `build_reasons` from `pipeline/scoring/explain.py` in `pipeline/ai_matching.py`
- [x] T017 [US2] Implement `generate_arch_match_explanation(company, tender, breakdown)` with Claude prompt that forbids numeric output (text-only, breakdown as read-only context) in `pipeline/ai_matching.py`
- [x] T018 [US2] Wire explanation generation after `score_architecture_match` in `_score_company_tender_matches` with fallback on missing key or API error in `pipeline/ai_matching.py`

**Checkpoint**: User Story 2 complete — narrative explains pre-computed scores, never invents them

---

## Phase 5: User Story 3 - Consistent Cached Match Results (Priority: P3)

**Goal**: Persist and return identical score + breakdown on cache hits within 168h TTL

**Independent Test**: Run architecture sync twice for same `company_id`; second response returns identical `score` and `breakdown` per tender; `tender_matches.breakdown_json` populated in PostgreSQL

### Implementation for User Story 3

- [x] T019 [US3] Extend `_upsert_tender_match` to accept optional `breakdown_json` and persist on insert/update in `pipeline/ai_matching.py`
- [x] T020 [US3] Pass canonical 5-key `breakdown_json` from scorer on fresh architecture matches in `_score_company_tender_matches` in `pipeline/ai_matching.py`
- [x] T021 [US3] On cache hit via `get_fresh_cached_match`, reconstruct `api_breakdown` from stored `breakdown_json` and skip re-scoring in `_score_company_tender_matches` in `pipeline/ai_matching.py`
- [x] T022 [US3] Include cached breakdown in `_match_result_dict` response when serving stale-cache hits in `pipeline/ai_matching.py`

**Checkpoint**: User Story 3 complete — cached matches return stable score and breakdown

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Constitution compliance, regression checks, and validation

- [x] T023 [P] Constitution compliance review per `.specify/memory/constitution.md` — verify no Claude score generation, city/region-only matching, Python-only scoring
- [x] T024 [P] Confirm `score_tender_pairs`, `run_ai_matching` background batch, and construction paths in `pipeline/ai_matching.py` remain on legacy Claude scorer (unchanged)
- [x] T025 Run quickstart validation steps in `specs/001-deterministic-ai-match/quickstart.md` (pytest + API sum check + optional no-key test)
- [x] T026 [P] Verify API response matches `specs/001-deterministic-ai-match/contracts/ai-matching-architecture-sync.json` schema for sample architecture sync response

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately
- **Foundational (Phase 2)**: Depends on Setup — **BLOCKS all user stories**
- **User Story 1 (Phase 3)**: Depends on Foundational — MVP deliverable
- **User Story 2 (Phase 4)**: Depends on US1 scoring path (needs breakdown to explain)
- **User Story 3 (Phase 5)**: Depends on Foundational DB column + US1 scorer output shape
- **Polish (Phase 6)**: Depends on US1–US3 completion

### User Story Dependencies

- **US1 (P1)**: Can start after Foundational — no dependency on US2/US3
- **US2 (P2)**: Depends on US1 (`score_architecture_match` and breakdown in pipeline)
- **US3 (P3)**: Depends on Foundational migration + US1 scorer; can parallelize with US2 after US1 core tasks (T011–T014) complete

### Within Each User Story

- Component scorers (T006–T010) before `score_architecture_match` (T011)
- T011 before unit tests (T012) and pipeline integration (T013–T014)
- US2 explanation wiring (T018) after US1 pipeline refactor (T013)
- US3 cache read (T021–T022) after US3 persist (T019–T020)

### Parallel Opportunities

- **Phase 2**: T005 parallel with T002–T004 after T004 model lands (T005 only needs skeleton)
- **Phase 3**: T006–T010 all parallel once T005 skeleton exists
- **Phase 4**: T016 parallel with T017 (different functions, same file — can be one session)
- **Phase 6**: T023, T024, T026 parallel

---

## Parallel Example: User Story 1 Component Scorers

```bash
# Launch all five component scorers together after T005 skeleton:
Task T006: score_project_type in pipeline/scoring/arch_match_scoring.py
Task T007: score_specialization in pipeline/scoring/arch_match_scoring.py
Task T008: score_region in pipeline/scoring/arch_match_scoring.py
Task T009: score_value_fit in pipeline/scoring/arch_match_scoring.py
Task T010: score_freshness in pipeline/scoring/arch_match_scoring.py
# Then sequentially: T011 → T012 → T013 → T014 → T015
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup (T001)
2. Complete Phase 2: Foundational (T002–T005)
3. Complete Phase 3: User Story 1 (T006–T015)
4. **STOP and VALIDATE**: Run T012 + quickstart step 3 (score == sum)
5. Deploy to Railway — dashboard breakdown integrity restored

### Incremental Delivery

1. Foundational → US1 → Deploy (MVP: trustworthy scores)
2. Add US2 → Deploy (narrative explanations)
3. Add US3 → Deploy (cached breakdown persistence)
4. Polish → Final validation

### Suggested MVP Scope

**User Story 1 only** (T001–T015): Delivers the core trust fix — deterministic scores with decomposable breakdown. US2 fallback can use template text temporarily if US2 deferred; US3 cache works with score/reasoning only until breakdown_json wired.

---

## Notes

- Total tasks: **26**
- Do not modify scrapers, `opportunity_discovery.py`, or endpoints other than minimal `api/main.py` ai-matching gate
- Frontend unchanged — 7-key breakdown mapping handles dashboard compatibility
- Commit after each task or logical group (e.g., T006–T010 as one commit)
