# Research: Competitive Intelligence (Phase 1)

**Feature**: `006-competitive-intelligence` | **Date**: 2026-06-17

## R1 — Peer selection strategy

**Decision**: Two-stage ranking — similarity pre-score on cohort (top 20), then threat score on survivors (return top 3–5).

**Rationale**: Computing full threat scores (including permit 90d scans) for entire market cohort is O(n × permits). Pre-filtering by cheap overlap functions keeps p95 latency under 2s while preserving relevance. Existing `opportunity_discovery` peer logic (`award_categories &&`) is too narrow alone; CIP `sector_focus` + geo + value bands match the product audit.

**Alternatives considered**:
- *Threat score entire cohort* — rejected: permit scans on 200+ companies exceed SC-006 budget.
- *Random top by total_value* — rejected: not actionable; ignores overlap.
- *Precomputed materialized view* — rejected: violates no-new-tables constraint.

## R2 — Geographic overlap at city level

**Decision**: Combine Jaccard on CIP `service_cities` ∪ `concentration_map.geo` (60%) with city-token set overlap from `primary_city` (40%). +10 bonus when `primary_city` exact match. Do **not** use `companies.neighborhoods[]` in scoring explanations (street-derived per `company_intelligence._neighborhood_from_address`).

**Rationale**: Constitution III requires city/region granularity. CIP `concentration_map` is already city-level from `parse_city_from_address` in `cip_builder.py`. Neighborhood arrays are street names and would leak street-level matching if used in threat explanations.

**Alternatives considered**:
- *Neighborhood Jaccard* — rejected: constitution violation + noisy street tokens.
- *Lat/long distance* — rejected: not in schema; out of scope.

## R3 — Category overlap metric

**Decision**: Bhattacharyya coefficient on CIP `sector_focus` distributions, scaled 0–100. Fallback: Jaccard on union of `project_types` + `award_categories` token sets when `sector_focus` empty.

**Rationale**: `sector_focus` is a normalized distribution built from permits in `build_cip()`; Bhattacharyya is standard for histogram similarity and is deterministic. Token Jaccard fallback covers sparse profiles.

**Alternatives considered**:
- *dominant_sector equality only* — rejected: binary, loses partial overlap signal.
- *Cosine on trade_tags* — rejected: tags are categorical, not weighted distribution.

## R4 — Value overlap metric

**Decision**: Log-distance between medians with p25–p75 intersection bonus, reusing band logic from `pipeline/fit/dimensions.score_value_fit`.

**Rationale**: Aligns with existing value-fit semantics users already see in BD gates. Log-distance handles orders-of-magnitude differences between GCs and specialty trades.

**Alternatives considered**:
- *Absolute dollar difference* — rejected: dominated by large firms.
- *total_value ratio only* — rejected: conflates portfolio size with typical deal size.

## R5 — Award and permit activity

**Decision**:
- **Awards**: COUNT `contract_awards` WHERE `company_id` AND `award_date >= today-90d`, normalized to cohort p90; +buyer overlap bonus via Jaccard on `award_clients`.
- **Permits**: 50% normalized 90d permit count (via `normalize_vendor_name(applicant)`), 50% recency from `last_project_date`. Scan capped at 500 permit rows per peer; run **only** for final top-5 peers after threat ranking pass.

**Rationale**: Activity components measure *pace*, not just scale. Bounded permit scan meets SC-006. `last_project_date` fallback handles unmatched applicants.

**Alternatives considered**:
- *Use total_projects only* — rejected: no recency signal.
- *Scan all cohort permits* — rejected: performance.

## R6 — Market cohort widening

**Decision**: Start with `kind + dominant_sector (or primary_trade) + city`. If cohort size < 8, drop city gate and set `market.definition` to `sector_only_widened`.

**Rationale**: Spec FR-009. Prevents empty medians in thin city slices while keeping sector relevance.

**Alternatives considered**:
- *Never widen* — rejected: sparse Vancouver sub-sectors would fail SC-005.
- *Widen to all companies* — rejected: medians become meaningless.

## R7 — Architecture kind degradation

**Decision**: Award component always 0 with detail `"N/A — awards not tracked for architecture firms"`. Benchmark `award_count` returns `null` + `not_applicable: true`. Confidence computed on remaining four components only.

**Rationale**: `arch_companies` has no award columns; `build_cip()` sets `award_count = 0` for architecture. Showing zero awards would mislead.

**Alternatives considered**:
- *Hide competitive section for arch* — rejected: spec US4 requires graceful degradation.
- *Join awards by name* — rejected: unreliable matching; out of scope.

## R8 — API shape and reuse

**Decision**: Single `GET .../competitive-intelligence` endpoint; threat breakdown uses `ScoredRecommendation.to_explanation_dict()` shape from `pipeline/scoring/explain.py` via `weighted_fit()`.

**Rationale**: One round-trip (FR-004). Matches tender match breakdown UX in dashboard. Constitution I satisfied by `assert_score_equals_breakdown` pattern from `construction_match_scoring.py`.

**Alternatives considered**:
- *Three endpoints* — rejected: user + spec require single call.
- *Embed in company GET* — rejected: heavy compute on every profile load.

## R9 — UI placement

**Decision**: New `CompetitiveIntelligencePanel` inserted after profile header in `company-intelligence-dashboard.tsx`; fetches competitive-intelligence endpoint on profile load (lazy, below fold acceptable).

**Rationale**: Prior UX audit Option A+B: benchmark strip + peer cards without merging into BD `competitive_intelligence` award feed (different product surface).

**Alternatives considered**:
- *Replace BD section* — rejected: BD award intelligence stays for pursuits.
- *Separate page* — rejected: reduces discovery; Phase 1 is profile-embedded.
