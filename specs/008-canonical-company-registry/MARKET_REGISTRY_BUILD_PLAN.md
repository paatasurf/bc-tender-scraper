# Market Registry Build Plan (Phase A)

**Version:** 1.2.0  
**Date:** 2026-07-04  
**Revision:** ODB-led Phase A; OrgBook demoted to optional enrichment (not a blocker)  
**Status:** Approved — implementation may begin; **no new concepts after this document**  
**Authority:** Subordinate to [`REGISTRY_CONSTITUTION.md`](REGISTRY_CONSTITUTION.md) and [`spec.md`](spec.md) (R1)  
**Phase scope:** Build the **Market Registry** staging inventory — **not** the Master Registry

---

## Phase A intent

The architecture is approved. **Do not implement the Registry Engine yet.** **Do not migrate data into the Master Registry.**

Phase A produces a **Market Registry** — a staging layer that inventories every real BC construction company identifiable from reliable public sources.

| Phase A does | Phase A does not |
|---|---|
| Collect company identities from public sources | Merge into canonical companies |
| Preserve original source strings | Classify registry layer or anchor score |
| Normalize names for matching prep | Deduplicate aggressively |
| Record BN, city, registry IDs when present | Attach permits or tenders |
| Assign source confidence | Build analytics or scores |
| Measure coverage vs **Enterprise Seed** first | Write to `companies` / Master Registry |
| Generate **Market Registry Quality Report** after every build | Use row count as success metric |

**The Market Registry is not the source of truth.** It is input material for a later Master Registry build (Registry Engine + R1 schema).

### First KPI (non-negotiable)

> **Enterprise Coverage** — not total company count.

The Registry must first recognize the companies that **define** the BC construction market. Every build and every source load is validated against the permanent **Enterprise Seed** benchmark before any other metric is reported.

### Phase A source priority (revised — 2026-07-04)

OrgBook is **not** a Phase A blocker. Phase A proceeds on sources that are publicly available today.

| Priority | Source | Role in Phase A |
|---:|---|---|
| **1** | **ODB (Statistics Canada)** | Primary legal-entity bulk corpus; BC NAICS 23 filter |
| **2** | **Awards** (CanadaBuys, BC provincial, Vancouver) | Enterprise validation + vendor discovery |
| **3** | **Enterprise Seed** | Permanent coverage benchmark (~80–120 firms); evaluated after each load |
| **4** | **Google** (enrichment) | On-demand website / corroboration — not a bulk ingest source |
| **5** | **OrgBook / BC Registries export** *(optional)* | Legal identity enrichment when/if an official bulk export is obtained — **do not wait** |

**TenderScope existing data** (permits, tenders, awards already in DB) continues to feed evidence linking in parallel; it is not a separate Market Registry ingest step.

**OrgBook importer (`orgbook_import.py`) remains correct and format-agnostic** — load when a file arrives; never scrape OrgBook API for bulk reference data.

### Staging record shape (logical — `market_registry`)

Every discovered company row in the Market Registry should carry:

| Field | Rule |
|---|---|
| `source` | Origin dataset identifier |
| `source_record_id` | Stable external key when available |
| `original_name` | Exact string from source — never invented |
| `normalized_name` | Deterministic normalization (Python) |
| `city` | When provided; city-level only |
| `province` | Default `BC` when implied |
| `business_number` | Only when source provides it |
| `website` | Only when source provides it |
| `registry_identifiers` | OrgBook ID, BC Registries ID, ODB idx, licence no., etc. |
| `source_confidence` | Enum — see §1.4 |
| `name_type` | `legal`, `trade`, `vendor`, `unknown` |
| `ingested_at` | Timestamp |

**Do not invent missing values.** Null is correct when the source is silent.

---

## Enterprise Seed Dataset (`enterprise_registry_seed`)

### Purpose

A **permanent, curated benchmark dataset** — not a temporary import file.

`enterprise_registry_seed` is the reference dataset used to validate Registry quality **throughout the project**. It is evaluated **first** after every Market Registry build. It does not live in the Market Registry observation stream; it is the **coverage target** against which observations are matched.

| Property | Rule |
|---|---|
| Permanence | Version-controlled; amended only by explicit curation |
| Scope | BC-active construction companies that define the market core |
| Target size | **~80–120 firms** (enterprise checklist from [`MARKET_BASELINE.md`](MARKET_BASELINE.md) §5) |
| Role | First ingestion source; first Quality Report section; ongoing benchmark |

### Record schema (logical)

Each Enterprise Seed record contains:

| Field | Required | Description |
|---|---|---|
| `seed_id` | ✅ | Stable internal key (never reused) |
| `canonical_company_name` | ✅ | TenderScope display / recognition name |
| `legal_name` | Optional | Registered legal entity when different from canonical |
| `website` | Optional | Official URL when known — do not invent |
| `province` | ✅ | Default `BC` |
| `primary_city` | Optional | City-level HQ / primary BC office |
| `market_segment` | ✅ | `enterprise` (all seed rows); may note `mid_market` edge cases |
| `inclusion_rationale` | ✅ | Why this company belongs in Enterprise Seed (1–2 sentences) |
| `sources` | ✅ | List of evidence URLs or citations (On-Site Top 40, awards, OrgBook, editorial) |
| `verification_status` | ✅ | `verified`, `pending`, `disputed` |
| `aliases` | Optional | Known spellings for match testing (PCL variants, etc.) |
| `added_at` / `updated_at` | ✅ | Curation audit |

**Do not invent missing values.** Empty `legal_name` or `website` is valid.

### Seed composition (initial curation guide)

| Tier | Examples | Inclusion rationale pattern |
|---|---|---|
| National GC (BC-active) | PCL Constructors Westcoast, Ledcor, EllisDon, Graham, Aecon, Bird, Kiewit | On-Site Top 40 + major BC award volume |
| Major regional GC / CM | Chandos, Axiom Builders, ITC Construction, Ventana, Kindred | BC Top lists + provincial/municipal awards |
| Large trades (BC-scale) | Flynn, Priestly Demolition, Emil Anderson | Revenue / award scale in BC |
| Developer-builders (GC-relevant) | Bosa, Onni, Westbank | Major multi-family / commercial builder activity |

### Enterprise Seed vs Market Registry

```
enterprise_registry_seed     ←  permanent benchmark (~100 rows)
        │
        │  coverage match (after each build)
        ▼
market_registry              ←  multi-source observations (staging)
```

Enterprise Seed rows are **not** copied into `market_registry` as observations. Coverage = *at least one* `market_registry` observation matches each seed record (normalized name, BN, alias, or website domain — light match only in Phase A).

---

## 1. Usable public sources

Sources ranked for **legal or reliable operating company names** in BC construction. Municipal permits are **excluded** (evidence only — Constitution §3).

### 1.1 Summary table (ODB-led Phase A order)

| Priority | Source | Est. BC construction rows | Legal names? | Bulk access | Role |
|---:|---|---:|---|---|---|
| **1** | **ODB (Statistics Canada)** | 8,000–14,000 | Mostly legal | ✅ CSV | **Primary bulk observation source — start here** |
| **2** | **CanadaBuys award vendors** | 400–900 | ✅ Legal | ✅ CSV | Enterprise validation |
| **2** | **BC provincial contract awards** | 300–700 | ✅ Mostly | ✅ CKAN | Mid-market + enterprise |
| **2** | **Vancouver awarded contracts** | 200–500 | ✅ Mostly | ✅ API | Regional LM |
| **3** | **`enterprise_registry_seed`** | **~80–120** | ✅ Curated | Manual / versioned file | **Permanent benchmark — coverage KPI, not a bulk ingest blocker** |
| **4** | **Google enrichment** | — | Trade / web | On-demand | Corroboration only |
| **5** | **OrgBook BC / BC Registries export** *(optional)* | 20,000–79,000* | ✅ Legal + DBA | ⚠️ Official export only | Enrichment when file obtained — **not required for Phase A** |
| — | **BC Housing — Licensed Builders** | ~8,200 | ✅ Company + licence | ⚠️ Web / export request | Regional residential GC (parallel when export ready) |
| — | **Association directories** | 3,000–8,000 | Trade names | ⚠️ Partnership | Mid-market fill (parallel) |
| — | **Other future sources** | TBD | TBD | TBD | WorkSafeBC, bonding, etc. |
| — | WorkSafeBC employer register | — | Would be legal | ❌ Research data | Deferred |
| — | StatsCan CBC tables | 0 names | ❌ Counts only | ✅ Aggregate | Sizing only |
| — | Municipal permits | — | ⚠️ Mixed | In TenderScope | **Excluded** from Market Registry ingest |

\*OrgBook: optional enrichment; ODB provides the construction-sector legal corpus for Phase A.

TenderScope existing pipelines: ODB (`odbus_import.py`), OrgBook (`orgbook_import.py`), awards (`import_contract_awards.py`).

---

### 1.2 Source detail sheets

*(Detail unchanged in substance — see prior sections; key role updates below.)*

#### 1.2.1 OrgBook BC / BC Registries

| Attribute | Detail |
|---|---|
| **Accessibility** | API v4 lookup; **no bulk download** (ToS). Bulk via `bcregistries@gov.bc.ca`. |
| **Licensing** | BC Open Government Licence; bulk extraction prohibited via API. |
| **Estimated count** | 2.5M+ all industries; construction subset after filter: **20k–79k**. |
| **Name type** | Legal + DBA + BN + registry ID. |
| **Update frequency** | Near real-time (API). |
| **Complexity** | **High** until export obtained. |
| **Market Registry role** | Optional legal identity enrichment — **Priority 5**; **not a Phase A blocker**. |

---

#### 1.2.2 ODB — Statistics Canada

| Attribute | Detail |
|---|---|
| **Accessibility** | [StatCan 21-26-0003](https://www150.statcan.gc.ca/n1/en/catalogue/21260003) CSV. |
| **Licensing** | Open Government Licence – Canada. |
| **Estimated count (BC NAICS 23)** | **8,000–14,000**. |
| **Name type** | Legal / registered + alt name + BN when present. |
| **Update frequency** | Occasional (2023 release; 2022 collection vintage). |
| **Complexity** | **Low** — pipeline exists. |
| **Market Registry role** | **First bulk observation load (Step 1)** — validated immediately against Enterprise Seed. |

---

#### 1.2.3–1.2.5 Award sources (CanadaBuys, BC provincial, Vancouver)

| Source | Est. distinct vendors | Confidence | Step |
|---|---:|---|---:|
| CanadaBuys BC | 400–900 | B — legal unverified to A if BN matched | 2 |
| BC provincial | 300–700 | B | 3 |
| Vancouver | 200–500 | C — mostly company names | 4 |

**Market Registry role:** Validate Enterprise Seed coverage; enrich mid-market observations.

---

#### 1.2.6 WorkSafeBC — deferred

Not publicly bulk-available (PopData research agreement). **Not Phase A.**

---

#### 1.2.7 Association directories — Step 7

BidCentral + VRCA/VICA/SICA/NRCA. Partnership or export required. Trade names supplement legal sources.

---

#### 1.2.8 BC Housing — Step 5

~8,200 licensed residential builders. Export request to `licensinginfo@bchousing.org`. Strong regional fill.

---

#### 1.2.9 Enterprise Seed — Step 0 (see dedicated section above)

Replaces prior "On-Site manual CSV" as **`enterprise_registry_seed`** permanent dataset. Not a subsection of external sources — **architectural primitive**.

---

#### 1.2.10 Municipal permits — excluded

Evidence only. Not a Market Registry source in Phase A.

---

### 1.3 Source confidence scale (staging)

| Level | Criteria | Example sources |
|---|---|---|
| **A — Legal verified** | Legal name + BN or registry ID | OrgBook export, ODB with `business_id_no` |
| **B — Legal unverified** | Legal-style name, no BN | CanadaBuys, BC provincial awards |
| **C — Operating name** | Credible business name, may be trade name | Vancouver awards, BidCentral |
| **D — Directory / editorial** | Curated benchmark | `enterprise_registry_seed` |
| **E — Weak** | Incomplete or ambiguous | Do not ingest |

---

## 2. Estimated Market Registry size

### 2.1 Raw observations by source (after full Phase A load)

| Source | Low | Mid | High |
|---|---:|---:|---:|
| `enterprise_registry_seed` | 80 | 100 | 120 |
| ODB BC NAICS 23 | 8,000 | 11,000 | 14,000 |
| CanadaBuys BC vendors | 400 | 650 | 900 |
| BC provincial award vendors | 300 | 500 | 700 |
| Vancouver award vendors | 200 | 350 | 500 |
| BC Housing licensed builders | 8,000 | 8,200 | 8,400 |
| OrgBook / Registries (construction-filtered) | 0* | 20,000 | 79,000 |
| Association directories | 3,000 | 5,000 | 8,000 |
| **Raw observation total** | **~20,000** | **~46,000** | **~112,000** |

\*Zero until Registries export obtained.

### 2.2 Light deduplication estimate (BN + exact normalized name)

| Scenario | Unique companies (est.) |
|---|---:|
| After Steps 0–2 (Seed + ODB + CanadaBuys) | ~8,500–12,000 |
| Recommended Phase A (Steps 0–5) | ~14,000–18,000 |
| Full Phase A (Steps 0–7) | ~25,000–35,000 |

**Cross-check:** StatsCan BC NAICS 23 employers ≈ **28,000**; total establishments ≈ **79,000**. Market Registry targets **employer-weighted** coverage — not full census.

---

## 3. Coverage analysis (post-build)

Coverage is reported in the **Market Registry Quality Report** (§4). Estimates below assume Enterprise Seed is the enterprise denominator.

### 3.1 Enterprise (denominator = `enterprise_registry_seed` count)

| Build stage | Enterprise Seed coverage (est.) |
|---|---:|
| Step 0 only (seed loaded) | 100% seed present; 0% external corroboration |
| Steps 0–2 (+ ODB + CanadaBuys) | **~90–98%** seed matched in observations |
| Steps 0–5 (+ provincial + Vancouver + BC Housing) | **~95–100%** |
| Full Phase A (+ OrgBook + associations) | **~100%** with BN/legal confirmation for majors |

**Gap metric:** Quality Report lists **missing seed IDs** explicitly — not a percentage alone.

### 3.2 Mid Market (~400–1,500) — estimated

| Build stage | Coverage (est.) |
|---|---:|
| Steps 0–2 | 40–55% |
| Steps 0–5 | 55–70% |
| Full Phase A | 70–85% |

*Method:* Matched observation count in mid-market proxy band (award vendors + ODB employers not in seed) vs MARKET_BASELINE mid-market range.

### 3.3 Regional (~2,000–5,000) — estimated

| Build stage | Coverage (est.) |
|---|---:|
| Steps 0–2 | 25–40% |
| Steps 0–5 | 45–60% |
| Full Phase A | 60–75% |

---

## 4. Market Registry Quality Report (mandatory)

After **every** Market Registry build — including partial builds after each ingestion step — generate:

**`MARKET_REGISTRY_QUALITY_REPORT.md`** (+ machine-readable `MARKET_REGISTRY_QUALITY_REPORT.json`)

Enterprise Seed is **always evaluated first** in the report.

### 4.1 Coverage

| Metric | Description |
|---|---|
| **Enterprise Seed coverage** | `matched / total seed` — list **covered** and **missing** seed IDs by name |
| **Enterprise Seed match quality** | Per seed: match method (BN, normalized name, alias, website domain), source(s) that matched |
| **Mid Market coverage (estimated)** | Proxy count vs 400–1,500 baseline range |
| **Regional coverage (estimated)** | Proxy count vs 2,000–5,000 baseline range |
| **BC market represented (%)** | Unique observations vs StatsCan employer baseline (~28k) — **with quality warning**, not primary KPI |

### 4.2 Registry quality

| Metric | Description |
|---|---|
| **Total observations** | Row count in `market_registry` |
| **Estimated unique companies** | Light dedup: BN exact + normalized name exact |
| **Multi-source companies** | Unique keys appearing in ≥2 sources |
| **Single-source companies** | Unique keys appearing in exactly 1 source |
| **With Business Number** | Observation rows with non-null BN |
| **With website** | Observation rows with non-null website |
| **With registry identifiers** | Rows with OrgBook ID, ODB idx, licence no., etc. |

### 4.3 Source analysis

For **every** source (including `enterprise_registry_seed` as benchmark, not observation count):

| Per-source metric | Description |
|---|---|
| **Observations** | Rows ingested from this source |
| **Unique companies** | Light dedup within source |
| **Overlap with prior sources** | Companies already seen in earlier ingestion steps |
| **New companies contributed** | Net-new unique keys from this source alone |
| **Enterprise Seed hits** | Seed IDs newly matched by this source |

### 4.4 Data quality

| Metric | Description |
|---|---|
| **Duplicate candidates** | Same BN or same normalized name + different original_name (no merge — flag only) |
| **Missing legal names** | Rows where `name_type` ≠ legal and no separate legal field |
| **Missing city** | Null `city` |
| **Missing website** | Null `website` (informational — not required) |
| **Missing identifiers** | No BN and no registry identifier |

### 4.5 Overall registry health

Single **summary score** (0–100) — deterministic, Python-computed — describing Market Registry completeness:

| Component | Weight (draft) | Input |
|---|---:|---|
| Enterprise Seed coverage | **40%** | matched seed / total seed |
| Multi-source corroboration rate | 20% | multi-source uniques / total uniques |
| BN or registry ID fill rate | 20% | rows with BN or registry ID / total rows |
| Source diversity | 10% | active sources with >0 observations / planned sources |
| Data quality (inverse missing-city rate on enterprise matches) | 10% | enterprise-matched rows with city |

**Forbidden as health score inputs:** raw row count, permit count, tender count.

Report must state **score version** for reproducibility.

### 4.6 Report cadence

| Trigger | Report required |
|---|---|
| Initial Enterprise Seed load | ✅ Baseline (0% external corroboration) |
| After each ingestion step (1–7) | ✅ Incremental |
| Full Phase A complete | ✅ Final |
| Any source refresh / re-ingest | ✅ Before/after comparison |

---

## 5. Recommended ingestion order

### 5.1 Sequence (Enterprise-first)

```
Step 1 — ODB BC NAICS 23 CSV               → Quality Report  ← START HERE
Step 2 — CanadaBuys BC distinct vendors    → Quality Report
Step 3 — BC provincial award vendors       → Quality Report
Step 4 — Vancouver award vendors           → Quality Report
Step 5 — Enterprise Seed coverage audit    → Quality Report (benchmark KPI)
Step 6 — Google enrichment (on-demand)     → optional corroboration
Step 7 — BC Housing licensed builders      → Quality Report (when export ready)
Step 8 — Association directory exports     → Quality Report (when obtained)
Step 9 — OrgBook / BC Registries export    → Quality Report (optional, when file obtained)
Step 10 — Other future sources             → Quality Report
```

**Enterprise Seed is always evaluated first in every Quality Report** — before aggregate row counts — even though bulk ingest begins with ODB + awards.

### 5.2 Rationale

| Step | Why this order |
|---|---|
| **1 ODB** | Public CSV today; primary legal-name corpus with NAICS 23 filter |
| **2–4 Awards** | High-confidence vendors; strongest enterprise corroboration; already in TenderScope |
| **5 Enterprise Seed audit** | Permanent benchmark KPI (~100 rows); measures coverage after ODB + awards load |
| **6 Google** | Enrichment only — does not block staging inventory |
| **7 BC Housing** | Large licensed GC set; parallel when export ready |
| **8 Associations** | Trade names; partnership required |
| **9 OrgBook** | Optional enrichment when official export obtained — **never blocks Steps 1–5** |
| **10 Future** | WorkSafeBC, bonding, etc. |

### 5.3 Parallel tracks (non-blocking)

| Track | Runs during |
|---|---|
| BC Registries export request (OrgBook) | Non-blocking — parallel to Steps 1–5 |
| BC Housing export request | Steps 1–4 |
| BCCA/BidCentral partnership | Steps 1–5 |
| Curate / amend Enterprise Seed | Ongoing |

---

## 6. Success criteria (Phase A)

Phase A is successful when TenderScope can answer:

| Question | Source |
|---|---|
| **Which Enterprise companies are covered?** | Quality Report §4.1 — matched seed list |
| **Which are still missing?** | Quality Report §4.1 — missing seed IDs |
| **Which sources contribute the highest-quality identities?** | Quality Report §4.3 — BN fill + Seed hits per source |
| **What percentage of the BC construction market is represented?** | Quality Report §4.1 — employer baseline comparison |

Phase A has **failed** if the only answer is:

> ~~"How many rows were imported?"~~

### Phase A acceptance gates

| Gate | Target |
|---|---|
| Enterprise Seed dataset curated | **≥80 records**, all `verification_status` ≥ pending with sources |
| Enterprise Seed coverage (full build) | **≥95%** matched in `market_registry` |
| Missing enterprise list | **Explicit zero** or documented dispute queue |
| Quality Report generated | After every ingestion step |
| Registry health score | Documented with version; trend ↑ across steps |
| No Master Registry mutation | Zero writes to `companies` canonical rows |
| No Registry Engine | Deferred to post–Phase A |

---

## 7. Risks

*(Retained from v1.0 — Enterprise-first mitigations added.)*

### 7.1 Duplicate risks

Same company across ODB + awards under different spellings — **keep all rows**; flag in Quality Report §4.4 only.

### 7.2 Licensing restrictions

OrgBook API bulk scrape **prohibited** (ToS); use official BC Registries data products if/when needed — **not required for Phase A**. Association scrape **high risk** — partnership first.

### 7.3 Enterprise Seed risks

| Risk | Mitigation |
|---|---|
| Seed list incomplete | Cross-check On-Site Top 40 + MARKET_BASELINE §5; version seed file |
| Seed name ≠ observation name | Store `aliases` on seed rows; report match method |
| False "covered" on weak match | Require match method disclosure in Quality Report |

### 7.4 Operational risks

| Risk | Mitigation |
|---|---|
| Row count as success | Constitution §9; Quality Report health score |
| Skipping Quality Report | Mandatory after each step — CI gate in implementation |
| Seed treated as observation source | Seed is benchmark table — separate from `market_registry` |

---

## 8. Phase A deliverables

| # | Deliverable | When |
|---|---|---|
| D0 | **`enterprise_registry_seed`** curated dataset (≥80 rows) | **Before Step 1** |
| D1 | `market_registry` staging design | Implementation start |
| D2 | Enterprise Seed load + baseline Quality Report | Step 0 |
| D3 | ODB BC NAICS 23 load + Quality Report | Step 1 |
| D4 | Award vendor loaders (3 sources) + Quality Reports | Steps 2–4 |
| D5 | BC Housing ingest + Quality Report | Step 5 |
| D6 | OrgBook export ingest + Quality Report | Step 6 |
| D7 | Association ingest + Quality Report | Step 7 |
| D8 | **`MARKET_REGISTRY_QUALITY_REPORT`** template + JSON schema | Before Step 0 |
| D9 | Phase A final Quality Report + acceptance sign-off | Step 8 complete |

**Not in Phase A:** Registry Engine, TS public IDs, merge, `companies` migration, permit linking.

---

## 9. Relationship to Master Registry (R1)

```
enterprise_registry_seed   ←  permanent benchmark (never replaced)
        │
        ▼
Phase A: market_registry   ←  staging observations
        │  Quality Report after each step
        ▼
Phase B: Registry Engine   ←  R1 implementation (post–Phase A)
        │  MATCH / MERGE / CREATE / REJECT
        ▼
Master Registry            ←  canonical companies + passport
```

Enterprise Seed remains the **coverage benchmark** even after Master Registry exists.

---

## 10. Immediate actions (no code)

| Priority | Action | Blocks |
|---:|---|---|
| 1 | **Curate `enterprise_registry_seed`** (≥80 firms, all fields) | Step 0 |
| 2 | Define Quality Report JSON schema | Step 0 report |
| 3 | Download ODB CSV from StatCan | Step 1 |
| 4 | Email BC Registries for bulk export | Step 6 |
| 5 | Email BC Housing for builder export | Step 5 |
| 6 | Contact BCCA/BidCentral for partnership | Step 7 |

---

## Implementation status (production)

| Date | Milestone | Detail |
|---|---|---|
| 2026-07-04 | **Migration 022 live** | `market_registry` table + batch columns on `odbus_reference` applied via `init_db()` on Railway production (`8431158`). |
| 2026-07-04 | **ODB primary import complete** | `run_odbus_import.py --filter primary_naics23 --apply` — **4,745 active** rows in `odbus_reference`; 1 prior fixture row superseded; no DELETE. Dry-run artifact: `exports/odbus_import_primary_dryrun_class_d.json` (`git_commit_sha` = `8431158`). |
| 2026-07-04 | **Production confirmation guard** | `db_safety.py` hardened to refuse mocked TTY/`input()` for Class C/D applies (`fix(db_safety)` follow-up commit). |

**Next (Phase A):** Enterprise Seed → `market_registry` load; mirror ODB observations into `market_registry`; Quality Report after Step 1.

---

## Related documents

| Document | Role |
|---|---|
| [`REGISTRY_CONSTITUTION.md`](REGISTRY_CONSTITUTION.md) | Identity law — permits excluded |
| [`spec.md`](spec.md) | Master Registry R1 — post–Phase A |
| [`MARKET_BASELINE.md`](MARKET_BASELINE.md) | Market denominators + enterprise checklist |
| [`TENDERSCOPE_REGISTRY_STRATEGY.md`](TENDERSCOPE_REGISTRY_STRATEGY.md) | Anchor Registry (post-Master Registry) |

---

## Revision history

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-07-03 | Initial Market Registry build plan |
| 1.1.0 | 2026-07-03 | **Enterprise-first ingestion**; `enterprise_registry_seed`; mandatory Quality Report |

---

**Version:** 1.1.0 | **Status:** Approved — **final Phase A spec; implementation only from here**
