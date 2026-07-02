# Permit Lifecycle — Phase 1 Investigation (Corrected)

**Status:** Investigation complete — findings corrected 2026-07-02  
**Scope:** Read-only aggregates on production `permits` table. No code in this phase.

---

## Executive summary

Phase 1 investigated applying the tender lifecycle pattern (P2-01…P2-03) to building permits. The table had **no lifecycle columns** and **no lifecycle status field** in municipal open-data feeds. Age-based fallback rules were proposed; Vancouver source-status mapping was deferred pending a verified status source.

**Correction (2026-07-02):** Status counts cited during Phase 2 planning — **46,745 Issued / 20,894 Finaled / 2,406 Cancelled** — were **never verified** in Phase 1 SQL or repo probes. They originated from user assertions, not from investigation queries. **These counts are retracted.**

Verified facts (production DB + live APIs, 2026-07-02):

| Claim | Verified? | Actual finding |
|---|---|---|
| 46,745 Issued / 20,894 Finaled / 2,406 Cancelled in DB | **No** | `source_status_raw` empty on **all** 111,773 permits |
| Vancouver open data has Issued/Finaled/Cancelled | **No** | `issued-building-permits` API: 20 fields, **no status field** |
| `permitcategory` = lifecycle status | **No** | Work complexity (4 values + null), not Finaled/Issued/Cancelled |
| 96.7% of permits younger than 6 months | **No** | ~4% Vancouver ≤6mo by `issue_date`; Phase 1 found ~95.8% **older** than 6mo |
| Surrey `PermitStatus` in live feed | **No** | ArcGIS layer has no `PermitStatus`; ~303 live records vs 8,274 in DB |

---

## 1. Table structure (verified)

### `permits` schema at investigation time (15 columns, pre-Phase 2)

| Column | Lifecycle relevance |
|---|---|
| `issue_date` | Primary reference date (all cities) |
| `application_date` | Vancouver pre-issue date |
| `permit_type` | Work category — **not** lifecycle status |
| No status column | No lifecycle state in schema |

### Row counts by source (2026-07-02)

| Source | Rows |
|---|---|
| vancouver | 102,113 |
| surrey | 8,274 |
| burnaby | 1,386 |
| **Total** | **111,773** |

---

## 2. Source status vocabulary (verified)

**No lifecycle status column exists in ingested data.**

- **Vancouver COV** (`issued-building-permits`): fields include `permitnumber`, `issuedate`, `typeofwork`, `permitcategory`, etc. **`permitcategory` is work complexity**, not Issued/Finaled/Cancelled.
- **Surrey ArcGIS** (`IssuedBuildingPermits`): no status attribute in live schema.
- **Burnaby**: no status field in current import path.

`source_status_raw` was added in Phase 2 schema specifically to hold **future** true municipal lifecycle statuses. It must remain empty until a verified source is wired.

---

## 3. Age distribution (verified — Phase 1 probe)

Using `GREATEST(issue_date, application_date)` as reference date:

| Bucket | Vancouver | Surrey |
|---|---|---|
| ≤ 6 months | ~4% | varies |
| 6–24 months | minority | varies |
| > 24 months (stale threshold) | **~86%** | majority with dates |

Phase 1 conclusion stands: **age-based stale rule (24 months) will affect the majority of Vancouver permits** on first resolver run, because no source status exists to mark completions.

---

## 4. Recommended lifecycle model (unchanged)

| Status | Meaning |
|---|---|
| `active` | Issued, construction plausibly ongoing |
| `completed` | Finaled / closed at source |
| `cancelled` | Withdrawn / cancelled at source |
| `stale` | No source status; reference date > 24 months |
| `unknown` | No source status and no parseable dates |

**Priority:** manual override → `source_status_raw` mapping (dormant until real data) → age fallback.

**Stale threshold:** 730 days (24 months) — conservative vs ~9–11 month median completion cited in industry literature.

---

## 5. Future status source — backlog

| City | Candidate | Status | Notes |
|---|---|---|---|
| Vancouver | **PLPOS** | Backlog | COV permit lifecycle / inspection system; not in current open-data feed |
| Surrey | TBD | Backlog | Identify alternate export if status exists outside ArcGIS issued layer |
| Burnaby | TBD | Backlog | No status in current import |

**Do not** populate `source_status_raw` from `permitcategory`, `typeofwork`, or other work-type fields.

---

## 6. Phase 2 outcome (for context)

Phase 2 (`8d77e97`) added lifecycle columns + nightly resolver. A subsequent commit (`dab7458`) incorrectly mapped work-type fields into `source_status_raw`; that mapping is **reverted** pending PLPOS investigation.

Resolver logic is unchanged: source-status rules stay dormant while `source_status_raw` is empty; age rules apply on first `resolve-permits` run.

**Stale under age-only rules is a coarse temporary label.** Many permits marked `stale` by the 24-month age rule are likely **completed constructions** in reality — we simply lack source confirmation today. When PLPOS (or another verified municipal status source) populates `source_status_raw`, the resolver reclassifies them: **source-status rules outrank age rules** (`Finaled` → `completed`, etc.). Until then, `stale` must not be treated as ground truth for downstream features (e.g. **Contractor Reliability**), which will depend on accurate completion/cancellation signals from source status, not age inference alone.

---

## Retraction log

| Date | Item | Action |
|---|---|---|
| 2026-07-02 | 46,745 / 20,894 / 2,406 status counts | **Retracted** — never from Phase 1 SQL |
| 2026-07-02 | 96.7% younger than 6 months | **Retracted** — contradicted verified date distribution |
| 2026-07-02 | `permitcategory` → `source_status_raw` | **Reverted** — work type, not lifecycle status |
