# Tasks: Non-Blocking API Startup for Reliable Deploys

**Input**: Design documents from `specs/003-fix-railway-startup/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/health-api.json, quickstart.md

**Tests**: Included per plan.md Phase D (unit tests for connect timeout and background init state)

**Organization**: Tasks grouped by user story for independent implementation and testing.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: User story label (US1, US2, US3)

## Path Conventions

- Backend root: repo root (`api/`, `db/`, `tests/`)
- Scope limit: **only** `api/main.py`, `db/connection.py`, and `tests/unit/test_db_startup.py`

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm feature context and scope boundaries before code changes

- [x] T001 Review `specs/003-fix-railway-startup/spec.md`, `plan.md`, `research.md`, `data-model.md`, and `contracts/health-api.json` (startup/DB path only; no scoring, frontend, or pipeline changes)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Bounded database connections — required before non-blocking startup can fail fast instead of hanging

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [x] T002 Add `_db_connect_timeout_seconds()` reading `DB_CONNECT_TIMEOUT` env (default 10, minimum 1) in `db/connection.py`
- [x] T003 Extend `_engine_connect_args()` to pass `connect_timeout` to psycopg2 via SQLAlchemy `connect_args` in `db/connection.py`
- [x] T004 Confirm `"timeout expired"` remains in `TRANSIENT_DB_ERROR_MARKERS` so `run_with_db_retry` retries timeout failures in `db/connection.py`
- [x] T005 [P] Add unit test asserting `connect_timeout` present in engine connect args for Railway-style URLs in `tests/unit/test_db_startup.py`

**Checkpoint**: Connection attempts are bounded; retries can fire instead of infinite hang

---

## Phase 3: User Story 1 - Deploy Passes Healthcheck (Priority: P1) 🎯 MVP

**Goal**: HTTP port binds within the healthcheck window; `/api/health` responds even when DB init is slow or incomplete

**Independent Test**: Start `uvicorn api.main:app`; confirm `Application startup complete` within ~10s; `curl /api/health` returns valid JSON (degraded OK if DB not ready)

### Tests for User Story 1

- [x] T006 [P] [US1] Add unit tests for `DbInitStatus` transitions (`pending` → `running` → `complete`/`failed`) with mocked `init_db` in `tests/unit/test_db_startup.py`

### Implementation for User Story 1

- [x] T007 [US1] Add thread-safe `DbInitStatus` state (`pending`|`running`|`complete`|`failed`), lock, timestamps, and error field in `db/connection.py`
- [x] T008 [US1] Implement `get_db_init_status()` returning status dict per `data-model.md` in `db/connection.py`
- [x] T009 [US1] Implement `start_init_db_background()` — single-flight daemon thread calling existing `init_db(raise_on_failure=False)` in `db/connection.py`
- [x] T010 [US1] Refactor lifespan: remove synchronous `init_db()`; call `start_scheduler()` then `start_init_db_background()` before `yield` in `api/main.py`
- [x] T011 [US1] Extend `/api/health` with additive `db_init_status` and `db_init_error` fields per `contracts/health-api.json` in `api/main.py`

**Checkpoint**: Deploy healthcheck can pass; health JSON backward-compatible with new optional fields

---

## Phase 4: User Story 2 - Fast-Fail on Stalled Database Connections (Priority: P1)

**Goal**: Unreachable database host fails within bounded time with visible logs/status — not silent infinite hang

**Independent Test**: Set `DATABASE_URL` to unreachable host; port binds within ≤60s; health shows `database_connected: false` and `db_init_status: failed`

### Tests for User Story 2

- [x] T012 [P] [US2] Add unit test verifying background init sets `failed` status and truncated error when `init_db` returns False in `tests/unit/test_db_startup.py`

### Implementation for User Story 2

- [x] T013 [US2] Ensure background init thread logs failure outcome and sets `_db_init_error` on retry exhaustion in `db/connection.py`
- [x] T014 [US2] Validate unreachable-database startup scenario per `specs/003-fix-railway-startup/quickstart.md` section 3 (local manual check)

**Checkpoint**: Operators can diagnose "app up, DB down" via health response and logs

---

## Phase 5: User Story 3 - Unchanged Runtime Behavior When Database Is Healthy (Priority: P2)

**Goal**: No regression in scoring, scheduler, or API responses when database initializes normally

**Independent Test**: With healthy DB, `/api/health` reports `status: ok`; opportunities endpoint returns same JSON shape as pre-fix

### Implementation for User Story 3

- [x] T015 [P] [US3] Run regression `pytest tests/unit/test_arch_match_scoring.py tests/unit/test_construction_match_scoring.py -v` and confirm all pass (scoring untouched)
- [ ] T016 [US3] Smoke test `/api/health` and `/api/companies/id/1735/opportunities?kind=construction&min_score=0&limit=3` with healthy DB per `specs/003-fix-railway-startup/quickstart.md` section 5

**Checkpoint**: Feature 001/002 behavior unchanged when DB is available

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Full validation and scope verification

- [x] T017 Run full unit suite `pytest tests/unit/test_db_startup.py -v` and confirm all pass
- [ ] T018 Validate healthy-database local startup per `specs/003-fix-railway-startup/quickstart.md` section 2
- [ ] T019 Validate Railway deploy healthcheck per `specs/003-fix-railway-startup/quickstart.md` section 4
- [x] T020 Confirm git diff touches only `api/main.py`, `db/connection.py`, and `tests/unit/test_db_startup.py` (no scoring, frontend, or pipeline files)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately
- **Foundational (Phase 2)**: Depends on Setup — **BLOCKS all user stories**
- **User Story 1 (Phase 3)**: Depends on Foundational (connect timeout must exist before background init)
- **User Story 2 (Phase 4)**: Depends on US1 (background init + status fields must exist to validate fast-fail)
- **User Story 3 (Phase 5)**: Depends on US1 (healthy startup path must work)
- **Polish (Phase 6)**: Depends on US1–US3 completion

### User Story Dependencies

- **User Story 1 (P1)**: After Foundational — delivers MVP deploy fix
- **User Story 2 (P1)**: After US1 — validates fast-fail observability on top of background init
- **User Story 3 (P2)**: After US1 — regression guard; independent of US2 validation

### Within Each User Story

- Tests before or alongside implementation (T006 before T007–T011; T012 after T009)
- `db/connection.py` state helpers before `api/main.py` lifespan change
- Health endpoint extension after background init wired

### Parallel Opportunities

- **Phase 2**: T005 [P] parallel with T004 after T003 completes
- **Phase 3**: T006 [P] can start once T007 interface is known (write tests first against planned API)
- **Phase 5**: T015 [P] fully parallel with T016 (different validation paths)
- **Phase 6**: T017–T019 sequential validation; T020 scope check anytime after code complete

---

## Parallel Example: User Story 1

```bash
# Write init status tests while implementing connection state:
Task T006: "Add unit tests for DbInitStatus transitions in tests/unit/test_db_startup.py"

# Then implement db layer (sequential within db/connection.py):
Task T007 → T008 → T009 in db/connection.py

# Then wire API (depends on T009):
Task T010 → T011 in api/main.py
```

---

## Parallel Example: Foundational + Tests

```bash
# After T003 lands connect_timeout:
Task T004: "Confirm timeout in TRANSIENT_DB_ERROR_MARKERS in db/connection.py"
Task T005: "Unit test connect_args in tests/unit/test_db_startup.py"  # parallel
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup (T001)
2. Complete Phase 2: Foundational (T002–T005)
3. Complete Phase 3: User Story 1 (T006–T011)
4. **STOP and VALIDATE**: Local startup + `/api/health` responds within seconds
5. Deploy to Railway — healthcheck should pass

### Incremental Delivery

1. Setup + Foundational → bounded connections
2. User Story 1 → non-blocking startup + health fields → **Deploy MVP**
3. User Story 2 → fast-fail observability confirmed
4. User Story 3 → regression smoke on scoring/API
5. Polish → full quickstart + scope guard

### Parallel Team Strategy

With two developers after Foundational:

- **Developer A**: US1 implementation (T007–T011) + T006 tests
- **Developer B**: US2 tests (T012) + US3 regression (T015–T016) after US1 checkpoint

---

## Notes

- Do **not** modify `pipeline/`, scoring modules, or frontend dashboards
- Keep existing `init_db()` / `_run_migrations()` logic unchanged — only **when** it runs changes
- `railway.toml` unchanged unless deploy still fails after US1
- Health endpoint must keep all existing required fields (`status`, `database_connected`, scheduler flags)
