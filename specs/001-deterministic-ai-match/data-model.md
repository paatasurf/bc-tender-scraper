# Data Model: Deterministic AI Match Scoring

**Feature**: `001-deterministic-ai-match` | **Date**: 2026-06-14

## Entities

### ArchCompany (`arch_companies`)

**Role**: Source profile for scoring inputs.

| Field | Scoring use |
|-------|-------------|
| `project_types` | Project type experience — type tags from permit aggregation |
| `total_projects` | Fallback experience depth when type-specific count unavailable |
| `houzz_project_types` | Project type + specialization overlap |
| `website_specializations` | Specialization component |
| `dominant_sector` | Specialization component |
| `trade_tags` | Specialization component |
| `neighborhoods` | Region component (city/district names from permits) |
| `houzz_service_areas` | Region component |
| `website_service_areas` | Region component |
| `geographic_reach` | Region tie-breaker label only |
| `avg_project_value` | Value fit component |
| `value_p25`, `value_p75` | Value fit band |
| `total_value` | Value fit fallback scale signal |

**Not used for scoring**: `google_address`, `lat`, `lng` (constitution III).

---

### ArchTender (`arch_tenders`)

**Role**: Opportunity being scored.

| Field | Scoring use |
|-------|-------------|
| `category` | Project type + specialization matching |
| `company` | Issuing organization — region extraction (city name heuristics) |
| `title` | Secondary token source for category/type |
| `value` | Value fit (parsed to float) |
| `deadline` | Freshness component |
| `status` | Informational only in v1 |

---

### TenderMatch (`tender_matches`)

**Role**: Cached company–tender score for architecture sync and discovery.

| Field | Type | Notes |
|-------|------|-------|
| `id` | SERIAL PK | unchanged |
| `company_kind` | VARCHAR(20) | `"architecture"` for this feature |
| `company_id` | INTEGER | FK logical → `arch_companies.id` |
| `tender_source` | VARCHAR(20) | `"arch"` |
| `tender_id` | INTEGER | FK logical → `arch_tenders.id` |
| `score` | INTEGER | **Must equal** sum of breakdown component points |
| `reasoning` | TEXT | Narrative explanation (Claude or fallback) |
| `breakdown_json` | JSONB | **NEW** — canonical 5-component breakdown |
| `created_at` | TIMESTAMPTZ | Cache TTL anchor (existing 168h window) |

**Unique constraint** (existing): `(company_kind, company_id, tender_source, tender_id)`

#### `breakdown_json` schema (canonical storage)

```json
{
  "project_type": { "points": 28, "max_points": 40, "detail": "12 residential projects in portfolio" },
  "specialization": { "points": 25, "max_points": 25, "detail": "Category aligns with website specializations" },
  "region": { "points": 15, "max_points": 15, "detail": "Tender in Vancouver; firm serves Vancouver, Burnaby" },
  "value_fit": { "points": 10, "max_points": 10, "detail": "Tender $2.1M within firm P25–P75 band" },
  "freshness": { "points": 7, "max_points": 10, "detail": "Deadline in 9 days" }
}
```

---

### ScoredArchMatch (logical / in-memory)

**Role**: Return type of `score_architecture_match()`.

| Field | Type | Validation |
|-------|------|------------|
| `score` | int | 0–100, equals sum of components |
| `breakdown` | list[BreakdownFactor] | Five factors with points ≤ max_points |
| `breakdown_json` | dict | Serializable canonical form for DB |
| `api_breakdown` | dict | Seven-key mapped form for API response |
| `match_reason` | str | Deterministic top-factor summary |

---

## State Transitions

```text
[Cache miss] → score_architecture_match() → optional Claude explanation
            → upsert tender_matches (score, reasoning, breakdown_json)
            → return API match object

[Cache hit, fresh] → read tender_matches → map breakdown_json to api_breakdown
                  → return (skip scoring + Claude)

[Cache hit, stale] → same as cache miss
```

---

## Migration

**File**: `db/connection.py` — new function `_migrate_tender_matches_breakdown_json`:

```sql
ALTER TABLE tender_matches
ADD COLUMN IF NOT EXISTS breakdown_json JSONB;
```

**File**: `db/models.py` — add to `TenderMatch`:

```python
breakdown_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
```

No backfill required — stale rows re-score on next sync request.

---

## Validation Rules

1. `score == sum(c.points for c in breakdown)` — enforced in scorer before persist
2. Each component: `0 <= points <= max_points`
3. `breakdown_json` must contain exactly five keys: `project_type`, `specialization`, `region`, `value_fit`, `freshness`
4. Region scorer MUST NOT read street-level fields from company or tender
