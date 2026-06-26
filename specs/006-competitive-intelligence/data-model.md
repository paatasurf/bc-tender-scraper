# Data Model: Competitive Intelligence (Phase 1)

**Feature**: `006-competitive-intelligence` | **Date**: 2026-06-17

**No new database tables or migrations.** This document defines runtime entities computed on read from existing PostgreSQL tables.

## Existing Tables (read-only)

| Table | Role in Phase 1 |
|-------|-----------------|
| `companies` | Subject/peers (construction); KPI columns for benchmark |
| `arch_companies` | Subject/peers (architecture); no award columns |
| `permits` | Permit 90d activity scan (`applicant`, `issue_date`) |
| `contract_awards` | Award 90d activity (`company_id`, `award_date`, `award_value`) |
| `cip_json` on company row | Cached `CompanyIntelligenceProfile` — sector, geo, value bands |

## Runtime Entities

### CompetitiveIntelligenceRequest

| Field | Type | Source |
|-------|------|--------|
| company_id | int | Path param |
| kind | `"construction"` \| `"architecture"` | Route prefix |
| peer_limit | int | Query, clamped 3–5, default 5 |
| refresh_cip | bool | Query, default false |

### MarketCohort

Set of company rows used for medians and activity normalization.

| Field | Type | Description |
|-------|------|-------------|
| members | list[Company \| ArchCompany] | Filtered cohort rows |
| definition | str | Human-readable filter description |
| definition_key | str | `sector_and_city` \| `sector_only_widened` |
| cohort_size | int | `len(members)` |
| data_scope | str | Always `vancouver_permits_and_public_awards` |

**Filter rules**:
1. Same `kind` (table selection)
2. `dominant_sector == subject.dominant_sector` OR `primary_trade == subject.primary_trade`
3. City gate: `primary_city` match (construction) OR shared CIP `service_cities` (architecture) — omitted when widening
4. Quality: `total_projects >= 2` OR `award_count >= 1`
5. Exclude subject `id`

**Widening**: If `cohort_size < 8` after step 3, re-query without city gate.

### PeerCandidate

Intermediate entity after cohort filter, before threat ranking.

| Field | Type | Description |
|-------|------|-------------|
| company_id | int | Peer PK |
| name | str | Display name |
| similarity | float | 0–1 pre-score |
| category_overlap_raw | float | 0–100 |
| geographic_overlap_raw | float | 0–100 |
| value_overlap_raw | float | 0–100 |
| cip | CompanyIntelligenceProfile | Hydrated via `get_cip()` |

### ThreatScoreResult

| Field | Type | Validation |
|-------|------|------------|
| score | int | 0–100, `== sum(breakdown.points)` |
| score_label | str | `"Competitive Threat Score"` |
| breakdown | list[BreakdownFactor] | Five factors, fixed max_points |
| reasons | list[str] | Top contributors |
| confidence | str | `high` \| `medium` \| `low` |
| raw_components | dict | Per-component raw 0–100 for confidence count |

**Breakdown factors**:

| factor | label | max_points |
|--------|-------|------------|
| geographic_overlap | Geographic overlap | 25 |
| category_overlap | Category overlap | 25 |
| value_overlap | Value overlap | 20 |
| award_activity | Award activity | 15 |
| permit_activity | Permit activity | 15 |

Architecture kind: `award_activity.points = 0`, detail N/A.

### TopCompetitor

| Field | Type | Description |
|-------|------|-------------|
| company_id | int | Peer PK |
| name | str | |
| company_kind | str | `construction` \| `architecture` |
| threat_score | int | |
| threat_breakdown | dict | `to_explanation_dict()` shape |
| similarity | float | Pre-score 0–1 |
| total_projects | int | Summary stat |
| total_value | float | |
| award_count | int | 0 or N/A for arch |
| profile_url | str | Optional deep link hint for UI |

### BenchmarkMetric

| Field | Type | Description |
|-------|------|-------------|
| key | str | `total_projects`, `total_value`, etc. |
| label | str | Display label |
| company | number \| null | Subject value |
| market_median | number \| null | Cohort median |
| top_competitor_median | number \| null | Median of returned peers |
| unit | str | `count` \| `currency` \| `score` |
| not_applicable | bool | true for arch `award_count` |

### CompetitiveIntelligenceResponse

Top-level API payload.

| Field | Type | Required |
|-------|------|----------|
| company_id | int | yes |
| kind | str | yes |
| engine_version | str | yes (`competitive_intel_v1`) |
| computed_at | ISO datetime | yes |
| market | MarketCohort summary | yes |
| benchmark | `{ metrics: BenchmarkMetric[] }` | yes |
| top_competitors | TopCompetitor[] | yes (may be empty) |
| warnings | str[] | no |

## State Transitions

```text
[Request]
  → Load subject row (404 if missing)
  → get_cip(subject) — may persist cip_json
  → build_market_cohort (widen if < 8)
  → filter_peer_candidates (limit 200)
  → similarity_pre_score → top 20
  → compute_threat_score for each (permit scan top 5 only on final pass)
  → select top peer_limit by threat_score
  → compute_benchmark_strip(subject, cohort, peers)
  → if len(peers) < 3: warnings += insufficient_market_data
[Response]
```

## Validation Rules

| Rule | Enforcement |
|------|-------------|
| V-001 | `threat_score == sum(breakdown.points)` | `assert_score_equals_breakdown` in unit tests |
| V-002 | Geo detail strings exclude street suffix tokens | Regex guard in tests |
| V-003 | `peer_limit` clamped [3, 5] | API layer |
| V-004 | Subject never in `top_competitors` | Filter `id != subject.id` |
| V-005 | No LLM calls in pipeline | Static import audit / mock in tests |
| V-006 | Permit scan ≤ 500 rows per peer | Hard cap in `activity.py` |
