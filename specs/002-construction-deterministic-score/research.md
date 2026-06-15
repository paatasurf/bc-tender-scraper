# Research: Construction Deterministic Internal Match Scoring

**Feature**: `002-construction-deterministic-score` | **Date**: 2026-06-15

## R1 — Root Cause of Total ≠ Sum on Construction Dashboard

**Decision**: The bug is a **split source of truth** between displayed total and breakdown.

**Rationale**:

1. `OpportunityCard` passes `opportunity.score` into `MatchScoreTooltip` via `tenderMatch.score` (`company-intelligence-dashboard.tsx`).
2. `opportunityToTenderMatch` (`api.ts` lines 595–616):
   - For `source === "ai"`: returns match **without** `explanation` — tooltip falls through to `explainTenderMatch` for breakdown but keeps **AI/hybrid score** as total.
   - For `source === "rules"`: computes client-side `explainTenderMatch` breakdown but keeps **server discovery score** (`_score_construction_tender_rules` or hybrid-resolved score) as total — server and client formulas differ.
3. Hybrid construction scores in `tender_matches` come from `run_construction_company_scorer` → `_normalize_scorer_payload`, same fallback bug as architecture pre-001 (`computed if computed > 0 else declared`).

**Alternatives considered**:
- Fix frontend only (display sum as total) — rejected; violates constitution V (scoring must be Python-native) and leaves inconsistent API/cache data.
- Align client `explainTenderMatch` to server rules — rejected; duplicates logic; TS and Python will drift.

---

## R2 — Reuse vs Duplicate Architecture Engine

**Decision**: Extract **`match_scoring_common.py`** shared utilities; add **`construction_match_scoring.py`** as a sibling to `arch_match_scoring.py`. Do not merge architecture and construction into one scorer.

**Rationale**:
- Architecture uses **5 canonical components** mapped to 7 API keys (keywords/reliability often 0).
- Construction uses **7 active components** with different weights (max 35+20+15+15+15+5+10, clamped to 100).
- Shared: `BreakdownFactor`, text normalization, date parsing, sum invariant, JSON persistence shape, optional Claude explanation pattern.

**Alternatives considered**:
- Single polymorphic scorer with `kind` parameter — rejected; overly complex; different inputs (`ArchCompany` vs `Company`, tender models).
- Copy-paste arch module — rejected; user explicitly requires shared engine, not duplication.

---

## R3 — Authoritative Scoring Weights for Construction

**Decision**: Port component weights and tier rules from `v0-construction-dashboard/lib/tender-match.ts` into Python, with one constitution fix for location.

**Rationale**: The dashboard breakdown UI already labels seven components and sums them — Python must produce the **same component model** the UI displays. Existing TS weights:

- Keywords: 9 pts per match, max 35
- Category: 10 pts per permit type match, max 20
- Specialization (Houzz in TS): 8 pts per match, max 15 → for construction companies use **trade_tags / dominant_sector / project_types** instead of Houzz-only
- Location: 5 pts per match, max 15 — **Python uses neighborhoods + service areas only** (not `googleAddress` street tokens)
- Value fit: 15 / 9 / 3 tier bands
- Reliability: up to 5 from `ai_reliability_score` when relevance exists
- Freshness: 10 if ≤30 days, 7 otherwise, 0 if expired

**Alternatives considered**:
- Reuse `_score_construction_tender_rules` from `opportunity_discovery.py` as-is — rejected; different component decomposition (buyer_pts, scope_pts, penalty) does not map to 7-key UI breakdown.

---

## R4 — Integration Surface (Minimal API Touch)

**Decision**: Extend **existing** construction endpoints only:

| Endpoint | Change |
|----------|--------|
| `GET /api/companies/id/{id}/opportunities` | Add optional `breakdown` on tender-type matches |
| `POST /api/ai-matching` (`kind=construction`, `sync=true`) | Deterministic scores + breakdown (mirror arch contract) |
| `score_tender_pairs` (internal) | Deterministic construction scoring for hybrid cache |

**Rationale**: Construction dashboard loads opportunities via `fetchCompanyOpportunities`; AI tab uses `fetchAiMatching`. No new routes required.

**Alternatives considered**:
- New `/api/companies/{id}/match-breakdown` endpoint — rejected; extra round-trip; out of scope constraint.

---

## R5 — Frontend Total Display

**Decision**: When API provides `explanation`/`breakdown`, display **`sum(breakdown)`** as the headline total (must equal `match.score`). Deprecate client-only `explainTenderMatch` for production construction paths.

**Rationale**: Defense in depth — even if API regresses, sum display stays consistent with breakdown. Primary fix remains server-side.

**Alternatives considered**:
- Server-only fix, no frontend change — rejected; user explicitly requires frontend to read computed total; AI-source branch currently skips explanation entirely.

---

## R6 — Architecture Regression Guard

**Decision**: Zero modifications to `arch_match_scoring.py` behavior beyond importing shared utils; run existing `test_arch_match_scoring.py` in CI after refactor.

**Rationale**: User constraint — architecture dashboard already works; must not touch.
