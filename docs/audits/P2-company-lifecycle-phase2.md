# Company Lifecycle — Phase 2 (Schema + FK-Only Resolver)

**Status:** Implemented 2026-07-02  
**Scope:** Additive schema on `companies`, deterministic resolver, internal endpoint, n8n workflow. **No filtering** (Phase 3).

---

## Executive summary

Phase 2 adds `lifecycle_status`, `is_operating`, and resolver-managed activity timestamps to the `companies` table. The nightly resolver classifies ~14K companies using **verified FK joins only** — no text-name matching at resolve time.

**Distribution correction (2026-07-02):** An informal investigation figure cited ~38% `no_observable_activity` (5,429 / 14,139). That estimate **included unverified text-name matching** (`permits.applicant` → `normalize_vendor_name()` → company) and denormalized row dates. It is **retracted**.

**Authoritative Phase 2 forecast (FK-only, ref 2026-07-02):**

| Status | Count | % |
|---|---:|---:|
| `active` | 617 | 4.4% |
| `quiet` | 461 | 3.3% |
| `dormant` | 1,328 | 9.4% |
| `no_observable_activity` | 11,733 | **83.0%** |

FK activity sources: `contract_awards.company_id` + `award_date` (2,404 companies), `tender_outcomes.recorded_at` (2 companies). Permits are **not** included until `permits.company_id` FK exists and is populated.

**Future phase (logged):** Improve company↔permit linkage via normalized name matching with confidence scores (and/or `permits.company_id` backfill). That work will organically shrink the `no_observable_activity` bucket without changing resolver age rules.

---

## 1. Schema (`companies`)

| Column | Purpose |
|---|---|
| `lifecycle_status` | `active` / `quiet` / `dormant` / `no_observable_activity` |
| `lifecycle_status_override` | Manual override — resolver skips |
| `last_activity_at` | Computed MAX of FK-linked activity timestamps |
| `status_changed_at` | Set when resolver applies a transition |
| `is_operating` | `true` for active, quiet, no_observable_activity; `false` for dormant |

Neutral backfill: all rows → `active`, `is_operating=true` until first resolve.

**Not touched:** legacy `company_lifecycle` column (dashboard classifier), competitive intelligence, company APIs, frontend.

Migration: `db/migrations/012_company_lifecycle.sql`  
Bootstrap: `db/connection.py` → `_ensure_company_lifecycle_columns()`

---

## 2. Resolver rules (`pipeline/company_lifecycle_resolver.py`)

1. Override wins → `skipped_override`
2. `last_activity_at = MAX(...)` from verified FK only:
   - `contract_awards.company_id` + `award_date`
   - `tender_outcomes.recorded_at`
   - `permits.company_id` (when column exists and is populated — not yet)
3. Age thresholds (ref = resolve time UTC):
   - ≤365 days → `active`
   - 366–730 days → `quiet`
   - >730 days → `dormant`
   - No FK-linked dated records → `no_observable_activity`
4. Idempotent: unchanged rows → `skipped_no_change`

---

## 3. Internal endpoint

`POST /internal/lifecycle/resolve-companies?background=true`

- Registered in `api/main.py` (same precedent as resolve-permits / reconcile-awards)
- `verify_internal_key` required
- `background=true` returns `{"status":"started"}` immediately (~14K rows with joins)

---

## 4. n8n workflow

`n8n/workflows/company_lifecycle_resolver.json`

- Cron: **06:20 America/Vancouver** (after tender 06:00, permit 06:15, before awards 06:30)
- HTTP Request typeVersion 4.2, `background=true`

---

## 5. Investigation vs implementation (signal reconciliation)

| Signal | Investigation probe | Phase 2 resolver |
|---|---|---|
| `contract_awards.company_id` FK | ✅ | ✅ |
| `tender_outcomes.recorded_at` | ✅ | ✅ |
| `permits.applicant` text / normalize | ✅ (retracted for production forecast) | ❌ by design |
| `companies.last_project_date` / `last_award_date` | ✅ (100% coverage, denormalized) | ❌ not FK at resolve time |

Permit volume context (production, 2026-07-02):

- 103,421 permit rows with `applicant`
- 11,867 **distinct companies** via exact `applicant = companies.name` (avg ~8.7 permits/company)
- 81K+ figure refers to **permit rows**, not distinct companies

Investigation combined distribution (row dates + award FK + normalized permits) for comparison only — **not** what the resolver produces:

| Status | Combined (investigation) |
|---|---:|
| active | 3,220 |
| quiet | 1,626 |
| dormant | 9,293 |
| no_observable_activity | 0 |

---

## 6. Tests

- `tests/unit/test_company_lifecycle_schema.py`
- `tests/unit/test_company_lifecycle_resolver.py` (rules, override, idempotency, endpoint)

---

## 7. Phase 3 (not in scope)

- Default filtering on `is_operating` / `lifecycle_status` in CI peers, company APIs, frontend
- Permit FK backfill + confidence-scored name matching (future — shrinks `no_observable_activity`)
