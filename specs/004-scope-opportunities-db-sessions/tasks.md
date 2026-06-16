---
description: "Task list for scoped DB sessions in opportunities discovery"
---

# Tasks: Scoped Database Sessions for Opportunities Discovery

**Input**: Design documents from `specs/004-scope-opportunities-db-sessions/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: Included — spec SC-003 (parity) and SC-001/SC-002 (concurrent load) require automated validation.

**Organization**: Tasks grouped by user story for independent implementation and testing.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: User story label (US1, US2, US3)
- Include exact file paths in descriptions

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Baseline fixtures and fixture layout before refactor (parity guard per research R5)

- [x] T001 Create `tests/fixtures/opportunities/` directory and `tests/fixtures/opportunities/README.md` documenting baseline capture params (construction 1921 `min_score=50&limit=15`, architecture 19 `min_score=40&limit=15`)
- [x] T002 [P] Capture `baseline-construction-1921.json` per `specs/004-scope-opportunities-db-sessions/quickstart.md` §2 into `tests/fixtures/opportunities/baseline-construction-1921.json` (use `scripts/capture_opportunities_baselines.py` when production responds)
- [x] T003 [P] Capture `baseline-arch-19.json` per `specs/004-scope-opportunities-db-sessions/quickstart.md` §2 into `tests/fixtures/opportunities/baseline-arch-19.json` (use `scripts/capture_opportunities_baselines.py` when production responds)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Session lifecycle primitives and discover entry-point refactor — MUST complete before user story phases

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [x] T004 Add `session_scope()` context manager with guaranteed `session.close()` on success and error in `db/connection.py`
- [x] T005 Add `DiscoveryReadBundle` and `SessionPhaseMetrics` dataclasses per `specs/004-scope-opportunities-db-sessions/data-model.md` in `pipeline/opportunity_discovery.py`
- [x] T006 Refactor `discover_opportunities()` in `pipeline/opportunity_discovery.py` to manage phase sessions internally (remove required external `session` parameter)
- [x] T007 Add `_finalize_read_bundle(session, bundle)` helper in `pipeline/opportunity_discovery.py` to `session.expunge()` all ORM entities before read-phase session closes

**Checkpoint**: Foundation ready — phased discover implementation can begin

---

## Phase 3: User Story 1 - Discover Opportunities Without Taking Down the Site (Priority: P1) 🎯 MVP

**Goal**: Construction discover path releases DB connections during CPU phases; concurrent discovery no longer exhausts the pool; lightweight endpoints stay responsive.

**Independent Test**: Run `scripts/verify_opportunities_concurrent.py` — 5 parallel discover requests plus health/permits probes all return 200 with no `QueuePool limit` errors (SC-001, SC-002).

### Implementation for User Story 1

- [x] T008 [US1] Implement `_load_construction_read_bundle(session, company_id, max_candidates)` in `pipeline/opportunity_discovery.py` (company, signals, tender rows, permits, awards, fresh_cache)
- [x] T009 [US1] Split `_discover_construction_opportunities()` into B1–B5 phased pipeline (Read → CPU rule scan → Hybrid Write → CPU assembly → Final breakdown) using `session_scope()` in `pipeline/opportunity_discovery.py`
- [x] T010 [US1] Refactor `company_opportunities()` in `api/main.py` to call `discover_opportunities()` without `get_session()` / long-lived `finally: session.close()`
- [x] T011 [P] [US1] Add construction session-phase tests in `tests/unit/test_opportunities_session_phases.py` (mock pool checkout; assert `close()` between phases, not held across simulated CPU delay)
- [x] T012 [P] [US1] Create `scripts/verify_opportunities_concurrent.py` per `specs/004-scope-opportunities-db-sessions/quickstart.md` §4 (5 parallel discover + health/permits probes)

**Checkpoint**: Construction discover completes under concurrent load; MVP deployable for construction dashboard

---

## Phase 4: User Story 2 - Construction and Architecture Parity (Priority: P1)

**Goal**: Architecture discover follows the same session-scoping pattern; both kinds return identical match IDs, scores, and order vs pre-refactor baselines.

**Independent Test**: `pytest tests/unit/test_opportunities_parity.py -v` passes for construction 1921 and architecture 19 (SC-003).

### Implementation for User Story 2

- [x] T013 [US2] Implement `_load_architecture_read_bundle(session, company_id, max_candidates)` in `pipeline/opportunity_discovery.py` (ArchCompany, arch tenders, permits, fresh_cache)
- [x] T014 [US2] Split `_discover_architecture_opportunities()` into C1–C6 phased pipeline using `session_scope()` in `pipeline/opportunity_discovery.py`
- [x] T015 [US2] Refactor `arch_company_opportunities()` in `api/main.py` to call `discover_opportunities()` without long-lived session
- [x] T016 [US2] Ensure `score_tender_pairs()` in `pipeline/ai_matching.py` works with preloaded `fresh_cache` and does not require caller to hold session after hybrid write returns
- [x] T017 [P] [US2] Add parity tests in `tests/unit/test_opportunities_parity.py` comparing `(type, id, score)` tuples and list order vs `tests/fixtures/opportunities/baseline-*.json`
- [x] T018 [P] [US2] Extend `tests/unit/test_opportunities_session_phases.py` with architecture-path session close assertions

**Checkpoint**: Both construction and architecture paths scoped; parity regression guard in place

---

## Phase 5: User Story 3 - Operator Confidence Under Deploy and Load (Priority: P2)

**Goal**: Operators can verify post-deploy that no request holds connections through CPU phases; cumulative DB time is observable and under 10s (SC-004).

**Independent Test**: Logs show `[OpportunityDiscovery] ... db_phases_total=` under 10s for a full discover request; deploy script reports 200 for company 1921.

### Implementation for User Story 3

- [x] T019 [US3] Emit `[OpportunityDiscovery] company={id} kind={kind} db_phases_total={s}s cpu_phases_total={s}s` from `SessionPhaseMetrics` in `pipeline/opportunity_discovery.py`
- [x] T020 [US3] Update `scripts/verify_company_opportunities_deploy.py` to assert status=200, log response time, `total_candidates`, and final match count for company 1921
- [x] T021 [US3] Execute full validation checklist in `specs/004-scope-opportunities-db-sessions/quickstart.md` (§1–§6) and record results in PR description or deploy notes

**Checkpoint**: Observable metrics and deploy verification documented

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Contract compliance, regression sweep, constitution gate

- [x] T022 Verify opportunities response keys match `specs/004-scope-opportunities-db-sessions/contracts/opportunities-discovery-response.json` (no new response fields; metrics in logs only)
- [x] T023 Run full `pytest` suite from repo root and fix any regressions outside opportunities path
- [x] T024 Constitution compliance spot-check per `.specify/memory/constitution.md` (CC-001–CC-005 unchanged for opportunities endpoints)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately; T002/T003 should run before refactor merge (baseline capture)
- **Foundational (Phase 2)**: Depends on Phase 1 — **BLOCKS all user stories**
- **User Story 1 (Phase 3)**: Depends on Phase 2 — MVP construction path
- **User Story 2 (Phase 4)**: Depends on Phase 2; can start after T006/T007 even if US1 in progress (different functions in same file — coordinate to avoid merge conflicts)
- **User Story 3 (Phase 5)**: Depends on US1 + US2 completion (metrics wrap both paths)
- **Polish (Phase 6)**: Depends on all user stories

### User Story Dependencies

- **US1 (P1)**: Foundational only — delivers construction scoped sessions + concurrent validation (MVP)
- **US2 (P1)**: Foundational only — architecture path + parity tests; logically follows US1 for same-file edits but independently testable per kind
- **US3 (P2)**: US1 + US2 — observability and deploy scripts cover both kinds

### Within Each User Story

- Read-bundle loaders before phased pipeline split
- Pipeline split before route handler thin-out
- Implementation before parity/concurrent tests that assume refactor complete

### Parallel Opportunities

- **Phase 1**: T002 ∥ T003 (different fixture files)
- **Phase 3**: T011 ∥ T012 (test file ∥ script file)
- **Phase 4**: T017 ∥ T018 (parity tests ∥ session tests, different test modules/sections)
- **Phase 6**: T022 ∥ T024 (contract check ∥ constitution review)

---

## Parallel Example: User Story 1

```bash
# After T009 completes, run in parallel:
# T011 — tests/unit/test_opportunities_session_phases.py
# T012 — scripts/verify_opportunities_concurrent.py
```

## Parallel Example: User Story 2

```bash
# After T014 completes, run in parallel:
# T017 — tests/unit/test_opportunities_parity.py
# T018 — tests/unit/test_opportunities_session_phases.py (architecture section)
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup (baseline fixtures)
2. Complete Phase 2: Foundational (session_scope, discover entry refactor)
3. Complete Phase 3: User Story 1 (construction phased discover + concurrent script)
4. **STOP and VALIDATE**: `scripts/verify_opportunities_concurrent.py` + local smoke per quickstart §3
5. Deploy if pool exhaustion resolved for construction path

### Incremental Delivery

1. Setup + Foundational → phase infrastructure ready
2. US1 → construction discover scoped → concurrent validation (MVP)
3. US2 → architecture scoped + parity tests → both dashboards protected
4. US3 → metrics + deploy verification → operator confidence
5. Polish → full regression + constitution check

### Parallel Team Strategy

With two developers after Foundational:

- Developer A: US1 construction pipeline (`T008`–`T010`) then concurrent script (`T012`)
- Developer B: US2 architecture pipeline (`T013`–`T015`) after US1 read-bundle pattern established, or pair on `opportunity_discovery.py` sequentially

---

## Notes

- Do **not** raise `pool_size`, `max_overflow`, or `pool_timeout` as primary fix (FR-008)
- Ranking, scores, hybrid top-20, assembly slots MUST remain identical (FR-004)
- `get_session()` retry-hold fix in `db/connection.py` is out of scope (research R7)
- Commit after each phase checkpoint; run parity tests before merging US2

---

## Task Summary

| Phase | Tasks | Count |
|-------|-------|-------|
| Setup | T001–T003 | 3 |
| Foundational | T004–T007 | 4 |
| US1 (P1) MVP | T008–T012 | 5 |
| US2 (P1) | T013–T018 | 6 |
| US3 (P2) | T019–T021 | 3 |
| Polish | T022–T024 | 3 |
| **Total** | **T001–T024** | **24** |

**Suggested MVP scope**: Phase 1 + Phase 2 + Phase 3 (User Story 1) — **12 tasks**
