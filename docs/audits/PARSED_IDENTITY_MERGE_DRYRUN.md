# Parsed Identity Merge Dry-Run Audit

**Generated:** 2026-07-04 05:58 UTC  
**Classification:** Class C analysis (read-only; no `--apply`, no schema/data writes)  
**Database:** Production (Railway), queried via `guard_readonly_db`  
**Analyst:** ad-hoc Python simulation (results captured in this document only)

---

## Executive summary

This dry-run estimates the impact of adding `normalize_vendor_name(parsed_identities.business_name)` as an **additional grouping signal** in canonical company merge — without applying any changes.

| Scenario | Companies in scope | Canonical roots before | After (safe merges only) | Root reduction | Companies repointed |
|----------|-------------------:|-----------------------:|-------------------------:|---------------:|--------------------:|
| **A — Naive (all PI fields)** | 5,067 | 5,062 | 3,136 | **1,926** | 1,930 |
| **B — Applicant field only (recommended)** | 5,051 | 5,051 | 4,804 | **247** | 247 |

**Key finding:** Scenario A looks high-impact but is **unsafe** — it attributes contractor-field parsed names to the permit's `company_id` (resolved applicant), causing false merges (e.g. LMDG Building Code Consultants → Ledcor). **Scenario B** restricts to `field_name='applicant'` and is the correct wiring shape.

**Row lifecycle:** This operation would **only reclassify existing company rows** as aliases (`entity_role=alias`, set `canonical_company_id`). **No new company rows. No deletions.** Matches existing `company_canonical_merge.py` contract.

**Registry context:** Total companies in production: **14,888** (642 canonical, 1,993 applicant_alias, 12,096 standalone, 157 probable_person). In-scope companies are the subset with ≥1 high-confidence permit-linked `parsed_identities` row.

---

## Methodology

### Grouping signal (proposed)

```
norm_key = normalize_vendor_name(parsed_identities.business_name)
```

- Source: `parsed_identities` joined to `permits` on `source_type='permit'` AND `source_id=permits.id`
- Confidence gate: `parse_confidence >= 0.8`
- Companies in scope: any `permits.company_id` linked to a qualifying row

### Merge simulation

1. Group companies by shared `norm_key`
2. For groups with **>1 distinct canonical root** (`COALESCE(canonical_company_id, id)`):
   - **Safe auto-merge:** not generic name AND `<100` distinct roots
   - **Excluded (manual review):** generic name pattern OR `>=100` roots
3. Safe groups: union all roots to primary root (highest `total_projects` in group)
4. Count distinct roots after union-find across all safe merges

### Risk exclusion rules (auto-merge blocked)

| Rule | Rationale |
|------|-----------|
| `distinct_roots >= 100` | Ambiguous mega-groups (e.g. generic trade names shared by many unrelated firms) |
| Generic name heuristics | Short names or patterns like `Demolition Ltd.`, `Construction Inc.`, `Excavating Ltd.` |

Excluded multi-root groups: **135** (Scenario A) / **34** (Scenario B)  
Safe multi-root groups: **1,330** (Scenario A) / **167** (Scenario B)

---

## Critical caveat: contractor-field contamination (Scenario A)

Permits store **one** `company_id` (typically the resolved **applicant**). `parsed_identities` also parses the **contractor** field on the same permit. Joining all PI rows → `permits.company_id` attributes contractor parsed names to the applicant company.

**Example (production permit 4331628):**

| Field | Value |
|-------|-------|
| `permits.company_id` | 548765 (LMDG Building Code Consultants Ltd.) |
| `permits.applicant` | Michael Van Blokland DBA: LMDG Building Code Consultant |
| `permits.contractor` | Ledcor Construction Limited |
| `parsed_identities` (contractor) | `business_name = Ledcor Construction Limited` |

Under Scenario A, LMDG would be grouped into the Ledcor Construction Limited merge bucket (21 companies, 21 roots → 1). Primary winner by project count would incorrectly be **548765 LMDG** (684 projects), not **8756 Ledcor** (58 projects).

**Required wiring constraint:** Use `field_name IN ('applicant')` (or match PI field to the company resolution role). Scenario B applies this constraint.

---

## Ledcor Construction Limited — specific analysis

Prior investigation reported **15+ canonical roots** for parsed `business_name = "Ledcor Construction Limited"`. That count included contractor-field rows on architect/consultant permits.

### Scenario A (naive — all fields)

| Metric | Value |
|--------|------:|
| Norm key | `ledcorconstruction` |
| Distinct roots before | **21** |
| Distinct companies | 21 |
| Would collapse to | **1 root** |
| PI rows | 118 |

**Warning:** 20 of 21 companies are **not Ledcor** — they are architects/consultants (LMDG, Jensen Hughes, William Harrison, etc.) with contractor-field Ledcor mentions on shared permits. **Do not apply Scenario A.**

Sample false-positive company IDs: 548765 (LMDG), 2257 (Jensen Hughes), 203 (William Harrison Architect), 684 (Gordon MacKenzie Architect).

### Scenario B (applicant field only)

| Metric | Value |
|--------|------:|
| Distinct roots before | **1** (8756) |
| Distinct companies | 1 |
| Collapse | **No change** — already unified under canonical **8756** |
| Applicant PI rows | 50 |

**Ledcor applicant permits already resolve to canonical 8756.** Wiring parsed_identities (applicant-only) does **not** fix remaining Ledcor fragmentation:

| Orphan / split | Issue | PI wiring helps? |
|----------------|-------|------------------|
| 3046 `Chris Burrows DBA: Ledcor` (key `ledcor`) | 0 permits / 0 PI rows | **No** |
| 134005 `Ledcor Highways Ltd.` | 0 permit PI rows | **No** |
| 302683 malformed address row | 1 permit, garbage `business_name` | **No** |
| `ledcor` vs `ledcorconstruction` keys | Parser extracts different business names | **No** — needs parent-key bridging |

---

## Scenario A — Top 20 safe auto-merge groups (naive)

Groups with the most canonical roots that would merge (excluding risky/generic).

| Rank | Parsed business name | Norm key | Roots before | Companies | PI rows |
|------|---------------------|----------|--------------|-----------|---------|
| 1 | East West Excavating Ltd | `eastwestexcavating` | 88 | 88 | 307 |
| 2 | Hans Demolition and Excavating Ltd. | `hansdemolitionandexcavating` | 82 | 82 | 268 |
| 3 | Kingsman Excavating Ltd. | `kingsmanexcavating` | 61 | 62 | 306 |
| 4 | Van-City Excavating Ltd | `vancityexcavating` | 60 | 60 | 209 |
| 5 | PTL Contracting Ltd | `ptlcontracting` | 54 | 54 | 163 |
| 6 | Khela Excavating Ltd. | `khelaexcavating` | 52 | 52 | 121 |
| 7 | East West Excavating 2022 Ltd | `eastwestexcavating2022` | 49 | 49 | 143 |
| 8 | Allright Trucking 99 Ltd. | `allrighttrucking99` | 47 | 47 | 100 |
| 9 | Metro Contracting Ltd | `metrocontracting` | 41 | 41 | 117 |
| 10 | Octiscapes Site Services Ltd | `octiscapessiteservices` | 40 | 40 | 105 |
| 11 | G N A CONTRACTING LTD | `gnacontracting` | 39 | 39 | 177 |
| 12 | Demolition 2008 Ltd | `demolition2008` | 38 | 38 | 96 |
| 13 | TX Contracting Ltd. | `txcontracting` | 36 | 36 | 105 |
| 14 | Mash Construction Ltd. | `mashconstruction` | 36 | 36 | 70 |
| 15 | Eyco Building Group Ltd. | `eycobuilding` | 34 | 34 | 173 |
| 16 | K Excavation and Demolition Services Ltd | `kexcavationanddemolitionservices` | 33 | 33 | 87 |
| 17 | Civil Works Ltd. | `civilworks` | 32 | 32 | 98 |
| 18 | J B Siteworks Inc. | `jbsiteworks` | 32 | 32 | 101 |
| 19 | Power Excavating Ltd | `powerexcavating` | 30 | 30 | 81 |
| 20 | Canadian Turner Construction Company Ltd. | `canadianturnerconstruction` | 29 | 29 | 129 |

Ledcor Construction Limited (21 roots) is **not in the top 20** by root count but is called out separately above — **must not auto-merge under Scenario A** due to contractor-field contamination. Several top groups (excavating/demolition trade names) warrant spot-check even when not excluded by the generic heuristic.

---

## Scenario B — Top 20 safe auto-merge groups (applicant-only, recommended)

| Rank | Parsed business name | Norm key | Roots before | Companies | PI rows |
|------|---------------------|----------|--------------|-----------|---------|
| 1 | LQ Design GROUP Ltd | `lqdesign` | 24 | 24 | 1,954 |
| 2 | DWG Design Work Group Ltd. | `dwgdesignwork` | 8 | 8 | 901 |
| 3 | Architectural Collective Inc. | `architecturalcollective` | 8 | 8 | 904 |
| 4 | Vincent Wan Design | `vincentwandesign` | 8 | 8 | 1,571 |
| 5 | MBD Maple Building Design Inc. | `mbdmaplebuildingdesign` | 8 | 8 | 256 |
| 6 | Lineform Architecture Inc | `lineformarchitecture` | 7 | 7 | 282 |
| 7 | Raj Home Design | `rajhomedesign` | 6 | 6 | 654 |
| 8 | TChen Custom Homes / TC Studio | `tcstudio` | 5 | 5 | 300 |
| 9 | Wiedemann Architectural Design | `wiedemannarchitecturaldesign` | 5 | 5 | 492 |
| 10 | Elite Premium Home Design Ltd. | `elitepremiumhomedesign` | 5 | 5 | 146 |
| 11 | Intarsia Design Ltd. | `intarsiadesign` | 4 | 4 | 460 |
| 12 | space smart home design ltd. | `spacesmarthomedesign` | 4 | 4 | 565 |
| 13 | Eric Stine Architect Inc. | `ericstinearchitect` | 4 | 4 | 162 |
| 14 | Yan Building Design Studio Ltd. | `yanbuildingdesignstudio` | 4 | 4 | 316 |
| 15 | Tyko Development Ltd. | `tykodevelopment` | 3 | 3 | 152 |
| 16 | Architrix Design Studio Inc. | `architrixdesignstudio` | 3 | 3 | 707 |
| 17 | Westpoint Design & Development Ltd. | `westpointdesign&development` | 3 | 3 | 828 |
| 18 | BC Home Drafting & Consulting Ltd. | `bchomedrafting&consulting` | 3 | 3 | 452 |
| 19 | McCuaig and Associates Engineering Ltd. | `mccuaigandassociatesengineering` | 3 | 3 | 1,146 |
| 20 | Lung Designs Group Ltd. | `lungdesigns` | 3 | 3 | 501 |

Scenario B impact is concentrated in **architecture/design firm duplicate trees** (likely recent discovery-run artifacts sharing applicant parsed names). Lower volume but higher precision than Scenario A.

---

## Manual review queue — excluded groups (Scenario A)

These groups match the merge signal but are **blocked from auto-merge** due to generic names or ≥100 roots.

| Parsed business name | Norm key | Roots | Companies | Exclusion reason |
|---------------------|----------|------:|----------:|------------------|
| Demolition Ltd. | `demolition` | 275 | 275 | roots>=100+generic_name |
| Canadian Excavating Ltd. | `canadianexcavating` | 121 | 121 | roots>=100 |
| Excavating Ltd. | `excavating` | 115 | 115 | roots>=100+generic_name |
| Bhullar Excavating and Demolition | `bhullarexcavatingdemolition` | 106 | 106 | roots>=100 |
| Excavation Ltd. | `excavation` | 64 | 64 | generic_name |
| Construction Company | `constructioncompany` | 36 | 36 | generic_name |
| DEVELOPMENT LTD. | `development` | 35 | 35 | generic_name |
| Renovation Ltd. | `renovation` | 35 | 35 | generic_name |
| ETRO CONSTRUCTION LIMITED | `etroconstruction` | 28 | 28 | generic_name |
| CDC Construction Ltd. | `cdcconstruction` | 28 | 28 | generic_name |

**Largest excluded:** `Demolition Ltd.` — **275 roots**, 275 companies (generic + mega-group). These must never auto-merge on parsed name alone.

---

## Confirm: no new rows, alias-only

| Operation | Would occur? |
|-----------|--------------|
| INSERT new `companies` rows | **No** |
| DELETE `companies` rows | **No** |
| UPDATE `entity_role` → `alias` | **Yes** (for repointed members) |
| UPDATE `canonical_company_id` | **Yes** (point to chosen primary) |
| FK remap (`company_fk_remap`) | **Yes** (existing merge behavior — repoint permits/awards to canonical) |

Existing merge code may set `create_canonical_row=True` when no member matches display name; with PI-based grouping, primaries are always chosen from existing members, so **no inserts expected** for Scenario B safe groups.

---

## Recommendations

1. **Do not wire naive all-field join** — contractor contamination creates catastrophic false merges.
2. **Wire applicant-field PI grouping only** — Scenario B: **247 roots** consolidated, **247 companies** repointed, **167** safe merge groups.
3. **Keep generic / mega-group exclusions** — 135 (Scenario A) or 34 (Scenario B) groups need manual review or richer signals.
4. **Ledcor Group consolidation** remains a **separate** task: parent-key bridging (`ledcor` ↔ `ledcorconstruction`) and orphan rows without permit PI coverage.
5. **Before `--apply`:** implement dry-run output in merge script mirroring Scenario B; spot-check top 20 groups; add `FORCED_CANONICAL_IDS_BY_KEY` for known brands if needed.

---

## Raw data artifacts (local, not committed)

- `exports/parsed_identity_merge_dryrun.json` — Scenario A detailed JSON
- `exports/parsed_identity_merge_dryrun_v2.json` — Scenario A + B comparison

---

*Audit produced read-only against production. No production data or merge code was modified.*
