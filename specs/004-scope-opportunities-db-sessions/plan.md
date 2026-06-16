# Implementation Plan: Scoped Database Sessions for Opportunities Discovery

**Branch**: `004-scope-opportunities-db-sessions` | **Date**: 2026-06-16 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/004-scope-opportunities-db-sessions/spec.md`

**User constraints**: Fix QueuePool exhaustion by scoping DB sessions to short DB-only phases in the opportunities path. Ranking, scores, hybrid top-20 selection, assembly, and API JSON MUST remain identical to pre-fix behavior (features 001/002). Scope: `api/main.py` opportunities routes, `pipeline/opportunity_discovery.py`, `pipeline/ai_matching.py` (discover-path only), minimal `db/connection.py` helpers if needed. Do NOT raise pool limits as the primary fix. No frontend, scraper, or scoring algorithm changes.

## Summary

Production fails with `sqlalchemy.exc.TimeoutError: QueuePool limit of size 5 overflow 10 reached` because each opportunities request calls `get_session()` once and holds a checked-out connection for the **entire** discover pipeline (60–300+ seconds): rule scan over 800 tenders, hybrid scoring with persists, rule-to-items over hundreds of candidates, permits, awards, assembly, and breakdown attach — while only 15 pool connections exist.

**Approach (FIX A)**:

1. **Refactor discover into phased pipeline** — Split `_discover_construction_opportunities` and `_discover_architecture_opportunities` into explicit phases: **Read → CPU → Hybrid Write → CPU → Final Read/Write**, each DB phase using its own short-lived session via a context manager.
2. **Detach ORM to plain data** — After read phase, convert company, tenders, permits, awards, and tender-match cache rows to existing dataclass/dict structures (`RuleTenderCandidate`, payloads, `CompanySignals`) so CPU phases need no session.
3. **Thin route handlers** — Remove long-lived `get_session()` from opportunities routes; delegate lifecycle to `discover_opportunities()` which owns phase sessions internally.
4. **Parity guard** — Golden-file or snapshot tests comparing discover output for pinned company IDs before/after refactor.
5. **Instrumentation** — Log cumulative DB phase duration per request (validates SC-004: ≤10s total connection time).

## Technical Context

**Language/Version**: Python 3.11+ (existing repo standard)

**Primary Dependencies**: FastAPI, Uvicorn, SQLAlchemy 2.x, psycopg2, existing scoring modules (`pipeline/scoring/*`, `pipeline/opportunity_discovery.py`, `pipeline/ai_matching.py`)

**Storage**: PostgreSQL on Railway — read/write `tender_matches`, tenders, permits, contract_awards (unchanged schema)

**Testing**: pytest — unit tests for phase session scoping; integration/parity test comparing discover JSON for baseline company IDs; concurrent load script (quickstart)

**Target Platform**: Railway API (`uvicorn api.main:app`); Vercel proxies opportunities routes unchanged

**Performance Goals**:
- Cumulative DB connection hold per discover request: **≤10 seconds** (instrumented)
- Five concurrent discover requests: **100% success**, zero pool exhaustion
- Lightweight endpoints (`/api/permits`, `/api/tenders`, `/api/health`): **p95 < 5s** while discover runs

**Constraints**:
- Default pool settings unchanged (`pool_size=5`, `max_overflow=10`, `pool_timeout=30`)
- No change to scoring math, thresholds, hybrid top-20 selection, or assembly slot logic
- Opportunities JSON response shape unchanged (CC-004)
- Hybrid inline persist during discover preserved (short write session)

**Scale/Scope**: ~4 files primary; optional `db/connection.py` context helper; new unit + parity tests

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Reference: `.specify/memory/constitution.md` (TenderScope v1.0.0)

| Principle | Gate | Pass? |
|-----------|------|-------|
| I. Transparent AI Scoring | Breakdowns and totals unchanged; same `breakdown` on construction tenders | ✅ |
| II. Claude API Scope | No new LLM calls; discover path stays deterministic Python | ✅ |
| III. Location Matching | No location logic touched | ✅ N/A |
| IV. Consistent API JSON | Same `discover_opportunities` return dict; routes unchanged externally | ✅ |
| V. Python-Native Scoring | Scoring modules called identically; only session timing changes | ✅ |

No constitution violations — Complexity Tracking not required.

## Project Structure

### Documentation (this feature)

```text
specs/004-scope-opportunities-db-sessions/
├── plan.md              # This file
├── research.md          # Phase 0
├── data-model.md        # Phase 1
├── quickstart.md        # Phase 1
├── contracts/
│   └── opportunities-discovery-response.json
└── tasks.md             # Phase 2 (/speckit-tasks — not yet created)
```

### Source Code (repository root)

```text
api/
└── main.py              # MODIFY: opportunities routes — no long-lived session

pipeline/
├── opportunity_discovery.py   # MODIFY: phased discover (read/cpu/write/cpu/final)
└── ai_matching.py             # MODIFY: score_tender_pairs accepts optional external cache;
                               #         ensure session not required after return

db/
└── connection.py        # OPTIONAL: session_scope() context manager for phased use

tests/
└── unit/
    ├── test_opportunities_session_phases.py   # NEW: phase timing + session close mocks
    └── test_opportunities_parity.py            # NEW: golden output vs baseline JSON
```

**Structure Decision**: Refactor within existing pipeline modules — no new packages. Phase boundaries live in `opportunity_discovery.py`.

## Implementation Phases

### Phase A — Session Context Helper (`db/connection.py`, optional)

1. Add `session_scope()` context manager:
   - Yields session from `get_session_factory()`
   - `finally: session.close()` always
   - Re-raise on error after close

2. Do **not** change default pool size (document in research.md as rejected primary fix).

### Phase B — Phased Construction Discover (`pipeline/opportunity_discovery.py`)

Replace monolithic session threading with:

| Phase | Session? | Work |
|-------|----------|------|
| **B1 Read** | Yes (short) | `session.get(Company)` → `CompanySignals.from_company`; load tender rows (400 federal + 400 commercial); load permit candidate rows; load award candidate rows; `load_fresh_company_tender_matches` → dict |
| **B2 CPU** | No | `_scan_construction_rule_tenders` on in-memory rows; sort top-20 for hybrid input |
| **B3 Hybrid Write** | Yes (short) | `score_tender_pairs(session, ..., persist=True)` — batch commit; close |
| **B4 CPU** | No | `_rule_tenders_to_opportunity_items` with preloaded `fresh_cache` + `hybrid_pairs`; permit/award rule scoring on detached rows; `_assemble_construction_opportunities` |
| **B5 Final Write/Read** | Yes (short) | `_attach_final_construction_tender_breakdowns` on `top` only; close |

**Parity rule**: Each CPU function receives the same inputs the monolithic path had at that step; no reordering of ranking steps.

### Phase C — Phased Architecture Discover (`pipeline/opportunity_discovery.py`)

Same pattern as Phase B:

| Phase | Session? | Work |
|-------|----------|------|
| **C1 Read** | Yes | `ArchCompany`, arch tender rows, permit rows, fresh cache |
| **C2 CPU** | No | Rule scan, top-20 selection |
| **C3 Hybrid Write** | Yes | `score_tender_pairs` (architecture, inline_cap=5) |
| **C4 CPU** | No | Rule-to-items, `_cached_ai_tenders_to_opportunity_items` using preloaded cache + batch-loaded tender rows (eliminate N+1) |
| **C5 CPU** | No | Assembly |
| **C6 Final** | Yes | Breakdown attach if needed for final items (architecture may skip if no construction-style breakdown) |

### Phase D — Route Handlers (`api/main.py`)

1. `company_opportunities` / `arch_company_opportunities`:
   - Remove `session = get_session()` / `finally: session.close()`
   - Call `discover_opportunities(company_id=..., kind=..., ...)` with no session parameter
2. Update `discover_opportunities()` signature to open/close its own phase sessions internally.

### Phase E — Tests & Validation

1. **Parity test**: Load baseline JSON for company IDs 1921 (construction) and 19 (architecture); after refactor, assert match IDs, scores, order identical (mock DB or recorded fixtures).
2. **Session mock test**: Patch `Session.close` or pool checkout; assert `close()` called between phases, not held across simulated CPU sleep.
3. **Concurrent script**: quickstart §4 — 5 parallel discover + health/permits checks.

### Phase F — Observability

Log per request:
```text
[OpportunityDiscovery] company={id} kind={kind} db_phases_total={s}s cpu_phases_total={s}s
```

## Complexity Tracking

> Not required — all constitution gates pass.

## Post-Design Constitution Re-Check

| Principle | Post-design status |
|-----------|-------------------|
| I–V | ✅ Unchanged scoring outputs; same API JSON; no LLM/location changes |

**Ready for**: `/speckit-tasks`
