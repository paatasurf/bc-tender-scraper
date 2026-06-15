# Implementation Plan: Deterministic AI Match Scoring (Architecture Dashboard)

**Branch**: `001-deterministic-ai-match` | **Date**: 2026-06-14 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/001-deterministic-ai-match/spec.md`

**User constraints**: Backend Python/FastAPI only for scoring path; PostgreSQL (`arch_companies`, `arch_tenders`, `tender_matches`); Railway deploy; React dashboard already exists on Vercel. **Do not change scraper logic or any other API endpoints.** Scope limited to architecture scoring in the `POST /api/ai-matching` sync path (`kind=architecture`).

## Summary

Replace Claude-generated architecture match scores with a **deterministic Python scoring engine** where `total_score = sum(five weighted components)` (40 + 25 + 15 + 10 + 10 = 100). Claude is invoked **only after** scoring to produce a 2–3 sentence narrative explanation from the pre-computed breakdown.

**Root cause of current bug**: `_normalize_scorer_payload` in `pipeline/ai_matching.py` falls back to Claude's declared `score` when breakdown sums to 0 (`computed if computed > 0 else declared`), producing totals like 83 with all-zero breakdowns.

**Approach**: Add `pipeline/scoring/arch_match_scoring.py` with pure scoring functions; refactor architecture branches in `pipeline/ai_matching.py` (`run_ai_matching_sync`, `_score_company_tender_matches`) to use deterministic scoring and optional Claude explanation. Persist breakdown JSON on `tender_matches`. Map the 5-component breakdown to the existing 7-key API shape so the React dashboard renders correctly without frontend changes.

## Technical Context

**Language/Version**: Python 3.11+ (existing repo standard)

**Primary Dependencies**: FastAPI, SQLAlchemy, Anthropic SDK (explanation text only), existing `pipeline/scoring/explain.py` patterns

**Storage**: PostgreSQL on Railway — `arch_companies`, `arch_tenders`, `tender_matches` (+ new `breakdown_json JSONB` column)

**Testing**: pytest — new unit tests for scoring engine; manual/API smoke via quickstart

**Target Platform**: Railway (API auto-deploy on git push); Vercel frontend (no changes required for v1)

**Project Type**: Web service (FastAPI backend) + React dashboard consumer

**Performance Goals**: Architecture sync match for one company against ≤50 tenders completes in <15s excluding optional Claude explanation calls

**Constraints**:
- No scraper or pipeline ingestion changes
- No changes to endpoints other than minimal `POST /api/ai-matching` gate adjustment (allow architecture sync without API key when explanation is skipped)
- Construction matching (`kind=construction`) and hybrid cache path (`score_tender_pairs`) unchanged in this release
- Location matching at city/region only — never street address or lat/lng

**Scale/Scope**: Architecture dashboard sync flow only; ~50 tenders per request; single company per sync call

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Reference: `.specify/memory/constitution.md` (TenderScope v1.0.0)

| Principle | Gate | Pass? |
|-----------|------|-------|
| I. Transparent AI Scoring | Total score = sum of weighted components; breakdown exposed in API/UI | ✅ |
| II. Claude API Scope | Claude used for text only; no LLM-generated scores or numbers | ✅ |
| III. Location Matching | City/region granularity only; no street-address matching | ✅ |
| IV. Consistent API JSON | Existing sync response envelope preserved; breakdown extended | ✅ |
| V. Python-Native Scoring | All component logic in `pipeline/scoring/arch_match_scoring.py` | ✅ |

No constitution violations — Complexity Tracking not required.

## Project Structure

### Documentation (this feature)

```text
specs/001-deterministic-ai-match/
├── plan.md              # This file
├── research.md          # Phase 0 — decisions and root-cause analysis
├── data-model.md        # Phase 1 — entities and migration
├── quickstart.md        # Phase 1 — validation steps
├── contracts/           # Phase 1 — API response contract
│   └── ai-matching-architecture-sync.json
└── tasks.md             # Phase 2 (/speckit-tasks — not yet created)
```

### Source Code (repository root)

```text
pipeline/
├── ai_matching.py                    # MODIFY: architecture sync path only
└── scoring/
    ├── explain.py                    # REUSE: BreakdownFactor, weighted_fit patterns
    └── arch_match_scoring.py         # NEW: deterministic 5-component engine

db/
├── models.py                         # MODIFY: TenderMatch.breakdown_json
└── connection.py                     # MODIFY: migration for breakdown_json column

api/
└── main.py                           # MODIFY: ai-matching only — relax 503 for arch sync

tests/
└── unit/
    └── test_arch_match_scoring.py    # NEW: component sum invariant tests
```

**Structure Decision**: Monorepo with FastAPI at repo root (`api/`, `pipeline/`, `db/`). Frontend in `v0-construction-dashboard/` — **out of scope** for this release (API maps to existing 7-key breakdown for compatibility).

## Implementation Phases

### Phase A — Scoring Engine (core)

1. Create `pipeline/scoring/arch_match_scoring.py`:
   - `ArchMatchBreakdown` dataclass with five components
   - `score_architecture_match(company: ArchCompany, tender: ArchTender) -> ScoredArchMatch`
   - Component scorers: `score_project_type`, `score_specialization`, `score_region`, `score_value_fit`, `score_freshness`
   - `assert total == sum(components)` before return
   - `to_api_breakdown()` maps 5 components → existing 7-key `ApiAiMatchBreakdown` shape (keywords/reliability = 0 with explanatory detail)

2. Unit tests proving sum invariant, edge cases (missing data, expired deadline, no region overlap)

### Phase B — Pipeline Integration

1. Add `generate_arch_match_explanation(breakdown, company, tender) -> str`:
   - Calls Claude with breakdown as read-only context (no score request)
   - Fallback: `build_reasons()` from top components

2. Refactor `_score_company_tender_matches` / `run_ai_matching_sync`:
   - Remove `run_company_scorer` call for architecture path
   - Remove `run_tender_matcher` for architecture sync — score **all** tenders in loaded catalog (deterministic ranking replaces Claude candidate filter)
   - Use cache via `get_fresh_cached_match` when fresh; return cached breakdown from DB
   - Optional Claude explanation only for newly scored pairs (respect `AI_MATCHING_DELAY_SECONDS`)

3. Extend `_upsert_tender_match` to accept and persist `breakdown_json`

### Phase C — Database Migration

1. Add `breakdown_json JSONB` to `tender_matches` via `db/connection.py` migration
2. Add `breakdown_json` column to `TenderMatch` model in `db/models.py`

### Phase D — API Gate (minimal)

1. In `api/main.py` `trigger_ai_matching`:
   - For `sync=true` + `kind=architecture`: do **not** require `ANTHROPIC_API_KEY` at request start (deterministic scoring proceeds; explanation uses fallback if key missing)
   - Construction sync and async background runs keep existing key requirement

### Explicitly Out of Scope

- `pipeline/score_tender_pairs` / hybrid discovery cache (still uses legacy Claude scorer for architecture)
- `run_ai_matching` background batch job for architecture firms
- Construction matching (`kind=construction`)
- All scrapers, `opportunity_discovery.py`, other API routes
- Frontend component changes (handled via API key mapping)

## Complexity Tracking

> Not applicable — all constitution gates pass without exceptions.
