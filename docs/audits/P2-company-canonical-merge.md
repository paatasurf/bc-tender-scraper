# P2 Company Canonical Merge — Audit

**Date:** 2026-07-03  
**Scope:** DBA-based company deduplication, FK remap, import-time `resolve_company()`.

## Schema (migrations 014 + 015)

- `companies`: `display_name`, `entity_role`, `canonical_company_id`, `applicant_signatory`, merge metadata
- `permits.company_id` FK + merge metadata
- Audit: `company_canonical_merge_runs`, `company_canonical_merge_rollback`, `company_applicant_aliases`
- `entity_role` values: `standalone`, `canonical`, `applicant_alias`, `probable_person`

## Merge engine (`pipeline/company_canonical_merge.py`)

- Deterministic grouping by DBA / normalized trade name
- **Safe tier:** 626 DBA groups auto-applied; 90 excluded (78 probable_person, 12 review)
- **Pontem anchor:** `FORCED_CANONICAL_IDS_BY_KEY = {"pontem": 8638}`
- Plan vs apply alias math: plan counts n−1 per group; `create_canonical_row=True` marks all n members as alias (+553 extra vs plan 1440 → 1993 applied)

## FK remap (`pipeline/company_fk_remap.py`)

Remaps on merge apply: `contract_awards.company_id`, `tender_outcomes.company_id`, `client_profiles.company_id`, `company_wiki.company_id`, `google_enrichment_logs.company_id`, `permits.company_id`.

**Not remapped:** `tender_matches` (168h recalculable cache).

## Import faucet (`pipeline/company_resolution.py`)

- BC confidence: incorporated+BC=1.0, other BC=0.9 (logged), DBA=1.0
- Probable person → skip company creation
- Canonical key conflict without canonical candidate → `review`, no auto-create
- **Canonical preference:** DBA family prefix match + prefer `entity_role=canonical` over standalone

## Local verification (2026-07-03)

| Metric | Before | After |
|---|---:|---:|
| total_companies | 14,139 | 14,697 |
| canonical | 0 | 626 |
| applicant_alias | 0 | 1,993 |
| probable_person | 0 | 157 |
| permits.company_id | 0 | 30,393 |

- Pontem 8638: canonical, 127 permits, 2 aliases
- Ledcor 8756: canonical, 11 DBA aliases; 3046 standalone excluded (wave-2 dedup candidate)
- Faucet `"New Person DBA: Ledcor"` → 8756, no new row

## Production deploy note

**Code deploy alone does not mutate production data.** Migrations 014/015 and `scripts/run_company_canonical_merge.py --apply` must be run separately on production after review.

## Tests

- `tests/unit/test_company_canonical_merge.py`
- `tests/unit/test_company_canonical_schema.py`
- `tests/unit/test_company_resolution.py`
- `tests/unit/test_company_fk_remap.py`
- `tests/unit/test_company_analytics.py`
