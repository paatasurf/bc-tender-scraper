# Implementation Plan: Deterministic Internal Match Scoring (Construction Dashboard)

**Branch**: `002-construction-deterministic-score` | **Date**: 2026-06-15 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/002-construction-deterministic-score/spec.md`

**User constraints**: Python/FastAPI backend; PostgreSQL (`companies`, `tenders`, `commercial_tenders`, `tender_matches`); Railway auto-deploy; React dashboard on Vercel. **Reuse** deterministic scoring patterns from `001-deterministic-ai-match` via shared modules — do not duplicate. **Do not** change scrapers or unrelated endpoints. **Do not** modify architecture dashboard behavior.

## Summary

Fix the construction **Internal match score** disconnect: the tooltip shows `opportunity.score` (from discovery hybrid cache or legacy Claude scoring) while the breakdown is computed separately in the browser (`explainTenderMatch`), producing totals like **54** vs sum **37**.

**Approach**: Add a **Python deterministic construction match engine** (seven components) in `pipeline/scoring/`, extracted shared utilities from `arch_match_scoring.py`. Use it as the single source of truth for:

1. Construction tender scores returned by **Discover** (`GET /api/companies/.../opportunities`)
2. Construction **hybrid cache** writes (`score_tender_pairs`, `kind=construction`)
3. Construction **AI matching sync** (`POST /api/ai-matching`, `kind=construction`, `sync=true`)

Each match returns `score` + `breakdown` (7-key API shape) where `score == sum(breakdown.*.points)`. The React dashboard reads both from the API and displays the **same total** as the breakdown sum. Claude, if used, produces narrative text only.

**Architecture dashboard**: unchanged — continues using `arch_match_scoring.py` only.

## Technical Context

**Language/Version**: Python 3.11+ (existing repo standard)

**Primary Dependencies**: FastAPI, SQLAlchemy, existing `pipeline/scoring/explain.py` (`BreakdownFactor`), Anthropic SDK (optional narrative only — not for scores)

**Storage**: PostgreSQL on Railway — `companies`, `tenders`, `commercial_tenders`, `tender_matches` (existing `breakdown_json JSONB` from feature 001)

**Testing**: pytest — `tests/unit/test_construction_match_scoring.py` (sum invariant + fixtures); manual validation via quickstart

**Target Platform**: Railway API; Vercel frontend (`v0-construction-dashboard/`)

**Performance Goals**: Discover + scoring for one construction company (≤400 rule candidates, ≤5 hybrid rescoring) remains within existing interactive tolerances (<60s typical)

**Constraints**:
- No scraper or pipeline ingestion changes
- No changes to architecture code paths, arch API responses, or arch dashboard components
- Location component: city/region/neighborhood tokens only — **never** street-address token matching (constitution III; improves on current frontend use of `googleAddress`)
- Construction-only frontend changes in `match-explanation-tooltip.tsx`, `api.ts`, `opportunityToTenderMatch`

**Scale/Scope**: Construction company intelligence view — tender internal match scores only (not permit/contract-award cards)

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Reference: `.specify/memory/constitution.md` (TenderScope v1.0.0)

| Principle | Gate | Pass? |
|-----------|------|-------|
| I. Transparent AI Scoring | Total = sum of seven component points; breakdown in API/UI | ✅ |
| II. Claude API Scope | Claude for text only on construction paths; no LLM scores | ✅ |
| III. Location Matching | City/region/neighborhood only; no street-address scoring | ✅ |
| IV. Consistent API JSON | Extend existing opportunity + ai-matching envelopes with optional `breakdown` | ✅ |
| V. Python-Native Scoring | All construction component logic in Python shared scoring modules | ✅ |

No constitution violations — Complexity Tracking not required.

## Project Structure

### Documentation (this feature)

```text
specs/002-construction-deterministic-score/
├── plan.md              # This file
├── research.md          # Phase 0
├── data-model.md        # Phase 1
├── quickstart.md        # Phase 1
├── contracts/
│   ├── company-opportunities-construction.json
│   └── ai-matching-construction-sync.json
└── tasks.md             # Phase 2 (/speckit-tasks — not yet created)
```

### Source Code (repository root)

```text
pipeline/
├── ai_matching.py                         # MODIFY: construction sync + score_tender_pairs only
├── opportunity_discovery.py               # MODIFY: attach breakdown to construction tender items
└── scoring/
    ├── explain.py                         # REUSE: BreakdownFactor, build_reasons
    ├── match_scoring_common.py            # NEW: shared text/date utils, sum invariant, 7-key mapper
    ├── arch_match_scoring.py              # MODIFY: import from common (no behavior change)
    └── construction_match_scoring.py      # NEW: 7-component construction engine

db/
├── models.py                              # unchanged (breakdown_json exists)
└── connection.py                          # unchanged

api/
└── main.py                                # MODIFY: relax 503 for construction sync (mirror arch)

v0-construction-dashboard/
├── lib/api.ts                             # MODIFY: map opportunity breakdown; construction paths
├── lib/tender-match.ts                    # MODIFY: prefer API explanation; dev-only fallback
└── components/match-explanation-tooltip.tsx  # MODIFY: displayScore = sum(breakdown) when present

tests/
└── unit/
    ├── test_arch_match_scoring.py         # unchanged (regression)
    └── test_construction_match_scoring.py # NEW
```

**Structure Decision**: Monorepo — FastAPI at repo root; frontend in `v0-construction-dashboard/`. Shared scoring lives under `pipeline/scoring/` per feature 001 pattern.

## Implementation Phases

### Phase A — Shared Scoring Foundation

1. Create `pipeline/scoring/match_scoring_common.py`:
   - Move reusable helpers from `arch_match_scoring.py`: `_normalize_text`, `_token_set`, `_parse_date`, `_factor_to_json`
   - Add `assert_score_equals_breakdown(total, api_breakdown)`
   - Add `to_api_breakdown_seven_key(factors: dict[str, BreakdownFactor])` for native 7-key output
   - Add `breakdown_json_to_api_breakdown_generic(stored, key_order)` for cache reads

2. Refactor `arch_match_scoring.py` to import from `match_scoring_common.py` — **zero scoring behavior change**; run existing arch unit tests.

### Phase B — Construction Deterministic Engine

1. Create `pipeline/scoring/construction_match_scoring.py`:
   - `score_construction_match(company: Company, tender, tender_source: str) -> ScoredConstructionMatch`
   - Seven component scorers (port weights from `v0-construction-dashboard/lib/tender-match.ts`):

     | API key | Max pts | Source signal |
     |---------|---------|---------------|
     | keywords | 35 | Company name + project type keyword expansion vs tender haystack |
     | category | 20 | Permit project types vs tender category/title |
     | specialization | 15 | Trade tags / `dominant_sector` / capability profile vs tender (replaces Houzz-only for construction firms) |
     | location | 15 | `neighborhoods`, service areas — **city/region tokens ≥4 chars only** |
     | value | 15 | Tender value vs `avg_project_value` / award value bands |
     | reliability | 5 | `ai_reliability_score` when relevance signals present |
     | freshness | 10 | Deadline proximity tiers |

   - Total = sum of components, clamped to `[0, 100]`
   - `assert api_sum == total` before return
   - Persist canonical `breakdown_json` with seven keys

2. Unit tests: sum invariant, empty history, AI/hybrid legacy mismatch scenario, no street-address location credit, expired deadline.

### Phase C — Backend Integration (Construction Only)

1. **`score_tender_pairs`** (`kind=construction`):
   - Replace `run_construction_company_scorer` / `_call_claude` with `score_construction_match`
   - Persist `breakdown_json`; set `score` from engine total
   - Optional Claude narrative via new `generate_construction_match_explanation` (breakdown as read-only input)

2. **`run_construction_ai_matching_sync`** / `_score_construction_tender_matches`:
   - Same deterministic path as architecture refactor in 001
   - Score all loaded federal + commercial tenders; remove Claude matcher/scorer for scores

3. **`discover_opportunities` construction path**:
   - When building tender opportunity items (`_rule_tenders_to_opportunity_items`, hybrid resolve):
     - Score with `score_construction_match` for display
     - Attach `breakdown` (7-key) on each tender match in response payload
     - Set `score` = deterministic total (override hybrid legacy score for **display**)
   - Hybrid cache refresh still runs via `score_tender_pairs` (now deterministic)

4. **`api/main.py`**:
   - For `sync=true` + `kind=construction`: do not require `ANTHROPIC_API_KEY` at request start (deterministic scoring proceeds; explanation fallback if key missing)

### Phase D — Frontend (Construction Dashboard Only)

1. **`mapApiOpportunityMatch`**: map optional `breakdown` from API tender matches → `TenderMatchExplanation` via existing `explanationFromAiBreakdown`.

2. **`opportunityToTenderMatch`**: for construction + tender:
   - Always attach API `breakdown`/`explanation` when present
   - Set `score` from API (already equals breakdown sum)
   - Remove branch that skips explanation for `source === "ai"` on construction

3. **`MatchScoreTooltip`**: when `explanation` present, set displayed total to `sumMatchBreakdown(explanation.breakdown)` (must equal `match.score`; dev assert optional)

4. **Do not change** architecture-specific rendering paths or arch opportunity mapping.

### Explicitly Out of Scope

- Scrapers, CSV imports, pipeline cron jobs
- `opportunity_discovery` architecture branches
- Architecture dashboard components and arch AI matching behavior
- Permit / contract-award internal scores on construction dashboard
- BD intelligence panel scoring
- `GET .../tender-match/{id}` win-strategy endpoint (Claude analysis — separate feature surface)

## Complexity Tracking

> Not applicable — all constitution gates pass without exceptions.
