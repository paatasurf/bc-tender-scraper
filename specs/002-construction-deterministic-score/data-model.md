# Data Model: Construction Deterministic Internal Match Scoring

**Feature**: `002-construction-deterministic-score` | **Date**: 2026-06-15

## Entities

### Company (`companies`)

**Role**: Construction firm profile for scoring inputs.

| Field | Scoring use |
|-------|-------------|
| `name` | Keyword tokenization + expansion |
| `project_types` | Keywords, category, specialization |
| `neighborhoods` | Location component (city/district names) |
| `avg_project_value` | Value fit component |
| `avg_award_value` | Value fit fallback |
| `award_count`, `total_projects` | Reliability / relevance context |
| `ai_reliability_score` | Reliability component (0–5 scaled) |
| `trade_tags`, `dominant_sector` | Specialization component |
| `capability_profile_json` | Specialization secondary signals |

**Not used for scoring**: `primary_address`, precise lat/lng, full street parsing (constitution III).

---

### Construction Tender (`tenders` federal, `commercial_tenders`)

**Role**: Opportunity being scored.

| Field | Scoring use |
|-------|-------------|
| `title`, `category` | Keywords, category, specialization haystack |
| `organization` / `company` | Issuer name in haystack; city extraction heuristics |
| `location` (federal) | Region/city tokens in haystack |
| `estimated_value` / `value` | Value fit (parsed float) |
| `closing_date` / `deadline` | Freshness component |
| `tender_source` | `"federal"` or `"commercial"` in match keys |

---

### TenderMatch (`tender_matches`)

**Role**: Cached construction company–tender score (hybrid + AI sync).

| Field | Type | Notes |
|-------|------|-------|
| `company_kind` | VARCHAR(20) | `"construction"` for this feature |
| `company_id` | INTEGER | → `companies.id` |
| `tender_source` | VARCHAR(20) | `"federal"` or `"commercial"` |
| `tender_id` | INTEGER | → `tenders.id` or `commercial_tenders.id` |
| `score` | INTEGER | **Must equal** sum of breakdown component points |
| `reasoning` | TEXT | Optional narrative (Claude or fallback) |
| `breakdown_json` | JSONB | **Existing column** — seven canonical components |
| `created_at` | TIMESTAMPTZ | Cache TTL (168h hybrid window) |

**Unique constraint** (existing): `(company_kind, company_id, tender_source, tender_id)`

#### `breakdown_json` schema (construction canonical storage)

```json
{
  "keywords": { "points": 18, "max_points": 35, "detail": "Matched: electrical, mechanical" },
  "category": { "points": 10, "max_points": 20, "detail": "Permit types include Building · tender: Construction" },
  "specialization": { "points": 8, "max_points": 15, "detail": "Trade tags align with tender scope" },
  "location": { "points": 10, "max_points": 15, "detail": "Service overlap: Vancouver, Burnaby" },
  "value_fit": { "points": 9, "max_points": 15, "detail": "Tender $1.2M within range vs $800K avg (1.5×)" },
  "reliability": { "points": 4, "max_points": 5, "detail": "Reliability score 82 with relevance signals" },
  "freshness": { "points": 10, "max_points": 10, "detail": "Closes in 12 days — urgent window" }
}
```

**Storage key naming**: canonical JSON uses snake_case factor ids; API response maps to 7-key frontend shape (`value` not `value_fit` in API — follow existing `ApiAiMatchBreakdown` convention from feature 001).

---

### ScoredConstructionMatch (logical / in-memory)

**Role**: Return type of `score_construction_match()`.

| Field | Description |
|-------|-------------|
| `score` | 0–100, sum of components |
| `breakdown` | `list[BreakdownFactor]` |
| `breakdown_json` | Persisted canonical dict |
| `api_breakdown` | 7-key dict for API/dashboard |
| `match_reason` | Top contributing labels |

---

### Opportunity Tender Item (API response extension)

**Role**: One row in `discover_opportunities` → `matches[]` for construction tenders.

Existing fields unchanged; **add**:

| Field | Type | Description |
|-------|------|-------------|
| `breakdown` | object | 7-key breakdown (same shape as AI matching) |
| `score` | int | Deterministic total (= sum of breakdown points) |

---

## Validation Rules

1. `score == sum(breakdown.*.points)` for every construction tender match returned to dashboard.
2. Each component `points <= max_points` defined for that component.
3. `0 <= score <= 100`.
4. Location component MUST NOT award points based solely on street-address token overlap.
5. Architecture `tender_matches` rows (`company_kind=architecture`) MUST NOT be modified by construction scoring code paths.

---

## State Transitions

### Legacy construction cache row (score-only, no breakdown)

```
legacy row → rescored on next hybrid/sync/discover touch → breakdown_json populated, score updated
```

### Fresh deterministic row

```
score_construction_match → upsert tender_matches(score, breakdown_json, reasoning) → API returns consistent pair
```

---

## Migration

**No new columns required** — `breakdown_json` added in feature 001. Construction rows may have empty `breakdown_json` until rescored; display path MUST rescore or treat as cache miss when breakdown missing.
