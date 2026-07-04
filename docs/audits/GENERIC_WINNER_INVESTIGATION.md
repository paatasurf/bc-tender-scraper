# Generic Winner Investigation — Scenario B Merge Blocked

**Generated:** 2026-07-04  
**Status:** **DO NOT APPLY** — original 167-group batch invalidated  
**Classification:** Read-only production analysis

---

## Executive conclusion

Your instinct was correct: the flagged groups are **dangerous**, not borderline. The original Scenario B batch (167 groups) was built on **cross-name permit→company linkage errors**. After proper guardrails, **zero groups remain safe for auto-merge** on current production data.

| Batch | Safe groups | Aliases | Verdict |
|-------|------------:|--------:|---------|
| Original (167) | 167 | 260 | **Unsafe** — generic buckets win, cross-name junk |
| + generic-bucket filter + 0-project mismatch filter | 83 | 97 | **Still unsafe** — e.g. Bigcity → Architectural Collective |
| + require `normalize(company.name) == normalize(PI business_name)` | **0** | **0** | **Correct** — no legitimate duplicates |

---

## 1. Company 548732 — `"Architect"` (1072 projects)

### Is this a real company?

**No.** It is a **generic bucket row**, not a single real firm named "Architect".

| Metric | Value |
|--------|------:|
| `companies.name` | **`Architect`** (literal single word) |
| `entity_role` | `canonical` |
| `total_projects` | 1072 |
| **Distinct permit applicant strings** | **16** |

### Top applicant strings on permits linked to 548732

| Applicant (raw) | Permits |
|-----------------|--------:|
| Carman Kwan DBA: Architectural Collective Inc. | **891** |
| Ernie Ho DBA: architectural designer | 59 |
| Architect 57 Inc. DBA: Architect 57 Inc. | 53 |
| Antonio Rigor DBA: Architecture49 | 10 |
| (12 more distinct architect / designer names…) | … |

### Sample permits (most recent)

| Permit | Applicant | Contractor | PI `business_name` |
|-------:|-----------|------------|-------------------|
| 4855733 | Carman Kwan DBA: Architectural Collective Inc. | G & H Demolition & Excavation Ltd. | Architectural Collective Inc. |
| 4855730 | Architect 57 Inc. DBA: Architect 57 Inc. | — | Architect 57 Inc. |
| 4368501 | Carman Kwan DBA: Architectural Collective Inc. | — | Architectural Collective Inc. |

**Interpretation:** 891/1072 permits belong to **Architectural Collective Inc.** (Carman Kwan DBA), but `permits.company_id` points at the bucket row `"Architect"`. The company **name** is wrong; the permits are pre-existing data-quality debt.

### Why it would "win" merge

Winner rule (original):

1. Pick **root with highest `total_projects`** in the PI group  
2. Pick **company on that root** with highest `(total_value + total_award_value, total_projects, -id)`

548732 has 1072 projects → root 548732 wins → **"Architect" becomes canonical** over specifically-named firms. **Backwards.**

---

## 2. LQ Design GROUP Ltd — `"construction"` / `"DEMOLITION LTD"` roots

### Why does `DEMOLITION LTD` have PI `business_name = LQ Design GROUP Ltd`?

**Not a parsing error.** The parser correctly read the permit applicant.

**Company 572962** (`DEMOLITION LTD`, 0 projects):

| Permit | Raw applicant | PI `business_name` |
|-------:|---------------|-------------------|
| 4317566 | **QI LI DBA: LQ Design GROUP Ltd** | **LQ Design GROUP Ltd** |

The company **record name** is garbage; the **permit applicant** is QI LI / LQ Design. `permits.company_id` was assigned to the wrong company row (discovery/resolution bug).

Same pattern for 17 other 0-project rows in the LQ group (`Kylin Construction`, `East West Excavating 2022 Ltd`, etc.): each has **1 permit** with applicant `QI LI DBA: LQ Design GROUP Ltd` but a **unrelated** `companies.name`.

### Company 548949 — literally named `"construction"`

| Finding | Detail |
|---------|--------|
| Name | `construction` (generic bucket) |
| Projects | 76 |
| Dominant PI on its permits | `construction` (Amarjeet Pooni DBA, etc.) |
| Stray rows | Act III Design, Construction Manager, Construction Company |

**Would win** Act III Design group under original `total_projects` rule — same bucket problem.

---

## 3. Systemic winner-selection rule

```
norm_key = normalize_vendor_name(parsed_identities.business_name)   # applicant field only
group    = all company_ids linked via permits with that norm_key

primary_root    = root with MAX sum(total_projects) in group
primary_company = company on that root with MAX (value, projects, -id)
```

### Generic-named winners (original 167 batch)

Using **strict** bucket detection (single-word profession names only: `Architect`, `construction`, … — **not** brands like `Kerr Construction`, `MWL Demolition`):

| Group `business_name` | Winner | Winner id | Problem |
|-----------------------|--------|----------:|---------|
| Architectural Collective Inc. | **Architect** | 548732 | Generic bucket beats specific firm |
| Act III Design & Construction Ltd. | **construction** | 548949 | Generic bucket beats DBA row |

**Broader heuristic false positives** (brand names flagged by old `is_generic_business_name`): Kerr Construction, MWL Demolition, SSDG, TKA+D — these are **legitimate brand abbreviations**, not single-word buckets. They were **not** excluded under the strict rule.

**Backwards merges under strict bucket rule:** **2 groups** (Architect, construction).

---

## 4. Revised batch after guardrails (implemented in code)

New link eligibility (`pipeline/parsed_identity_canonical_merge.py`):

1. **Skip generic bucket `companies.name`** — e.g. `Architect`, `construction`  
2. **Skip when `normalize(company.name) != normalize(PI business_name)`** — drops cross-name junk (DEMOLITION LTD + LQ Design PI)  
3. **Exclude group if primary would still be generic bucket** (belt-and-suspenders)  
4. Existing: applicant-only, `parse_confidence >= 0.8`, exclude `>=100` roots, generic PI `business_name`

### Results

| Stage | Safe groups |
|-------|------------:|
| Original | 167 |
| After (1)+(2) | **0** |
| Name-aligned duplicate check (independent query) | **0** multi-company groups |

**There are no production cases where two+ company rows share the same normalized name AND the same PI business key.** The 167 groups were entirely **cross-name false positives** from bad `permits.company_id` assignment.

---

## 5. Recommendation

| Action | Status |
|--------|--------|
| **Apply original 167-group Scenario B** | **BLOCKED** |
| **Apply revised 0-group batch** | Nothing to apply |
| **Fix upstream** | Company discovery / permit resolution must not create bucket rows (`Architect`, `construction`) or assign permits to mismatched company_ids |
| **Ledcor Group** | Remains separate (unchanged) |

### Before any future merge attempt

1. Clean bucket companies (548732 `Architect`, 548949 `construction`, …)  
2. Re-resolve `permits.company_id` from applicant + PI `business_name`  
3. Re-run dry-run — expect merges only among **name-aligned duplicate company rows**

---

## Artifacts

- `exports/generic_winner_investigation.json` — full query output (548732 samples, LQ junk rows, winner audit)
- `exports/parsed_identity_merge_report.json` — revised dry-run (**0 groups**)
- `docs/audits/PARSED_IDENTITY_MERGE_REVIEW.md` — empty safe list (supersedes 167-group list)

---

*Apply explicitly blocked pending upstream data cleanup.*
