# Feature 005 — Construction Tender Relevance (Matching Redesign)

**Status:** Draft / spec for review
**Owner:** Paata
**Date:** 2026-06-16
**Repo:** `bc-tender-scraper` · primary file `pipeline/opportunity_discovery.py`

-----

## 1. Problem

Construction opportunity discovery surfaces irrelevant tenders. For a real mid-large GC (Naki Ocran, `companies.id = 6560`: New Building / Addition-Alteration, avg project $32.2M, Vancouver) the top tenders are vending machines, police vehicles, a tow truck, a Kootenay bridge watermain, and out-of-region consulting — all scoring 64–69. This is the core value of the product (“show the right opportunities”) failing visibly, and it erodes client trust.

## 2. Evidence (baseline, 2026-06-16, read-only diagnosis)

1. **No work-type / region filter at candidate load.** Pool = most-recent 400 federal `tenders` + entire 168 `commercial_tenders`, `ORDER BY id DESC`. Only filter is `_is_tender_open` (deadline). 496 open candidates today.
1. **Category label is unreliable.** `tenders.category` is the CanadaBuys portal bucket, “almost always Construction” — goods/vehicles/food carry that label. The UI “CONSTRUCTION” chip just mirrors `payload.category`; fixing the chip does **not** fix ranking.
1. **A classifier already exists but discovery ignores it.** The scraper has `matches_target_category()` (construction/architecture/engineering keywords on title+category) used at **ingest**. Discovery does **not** apply it.
1. **Scorer is non-discriminating.** Bad tenders get an almost-fixed template: keywords ~27 + category 10 + specialization 8 + freshness 10 + reliability 4 + location 5–10 = 64–69. Keyword (~27, near max) fires on nearly everything because company keywords include generic tokens (“building”, “new”) and street names. **Real in-region construction scores *lower* (46–55) than the junk → ranking is effectively inverted.**
1. **Value-fit is dead.** `estimated_value` / `value` exists in **both** tender tables but is not passed into the discovery payload, so value-fit is always 0. A $75K vending contract is never penalized against a $32M-avg builder.
1. **No region gate.** Federal `tenders` has a `location` column; `commercial_tenders` has **no dedicated location column**. Out-of-region tenders (Kootenay, Tofino, Okanagan, Prince George) rank as high as local. Company-side `primary_city` / `primary_province` are also empty for Naki.
1. **No `unspsc` / NAICS / goods-services enum** on either tender table — so work-type must be derived from title (+ existing classifier), not a procurement code.

### Reference that works: the permit scorer

`_score_construction_permit` ranks well because permits are a **typed universe** (all construction) and it matches applicant-name + permit-type + address + value. Tenders lack the typed-universe property — that asymmetry is the root of the problem.

### Baseline golden snapshot — Naki Ocran top 15 (before)

|# |Type  |Score|Title                                           |Verdict                     |
|--|------|-----|------------------------------------------------|----------------------------|
|1 |tender|69   |Building Maintenance Services — Black & McDonald|✗ maintenance, not build    |
|2 |tender|69   |Carpenter Creek Bridge Watermain — New Denver   |✗ civil + out-of-region     |
|3 |tender|64   |Food / Cold Beverage Vending — New West         |✗ goods                     |
|4 |tender|64   |Two Police AWD Utility Vehicles                 |✗ goods                     |
|5 |tender|64   |One Flat Deck Tilt Wrecker                      |✗ goods                     |
|6 |permit|71   |1220 Seymour St — New Building                  |✓ own permit                |
|7 |permit|71   |375 Glen Drive — New Building                   |✓ own permit                |
|8 |tender|64   |Consultant for Road Revision — Pr. George       |✗ consulting + out-of-region|
|9 |tender|64   |Multi-Sport Court Renewal — New West            |~ real build, in-region     |
|10|tender|46   |Campbell St Phase 3 — Tofino                    |~ build, out-of-region      |
|11|tender|55   |UBCO Roof Replacement — Kelowna                 |~ build, out-of-region      |
|12|tender|46   |Country Club Dr Landscaping                     |✗ landscaping               |
|13|tender|—    |Portable Diesel Generator — UBC                 |✗ goods                     |
|14|tender|46   |Country Club West Dev — Qualicum                |~ build, out-of-region      |
|15|tender|51   |Coquitlam Main Early Works — Metro Van          |~ civil, in-region          |

**Baseline precision@10 ≈ 2/10** (only the two own-permits are solidly relevant; every top *tender* is junk).

## 3. Goals / Non-goals

**Goals**

- Top-N opportunities are actually relevant (right work-type, region, scale).
- Zero non-construction-work items (goods/vehicles/food/pure-software) in the list.
- Out-of-region tenders ranked below in-region for regional companies.
- Every surfaced item explains *why*; honest empty-state when nothing qualifies.
- Change is measurable (golden set + precision@k) and reversible (flag).

**Non-goals**

- Do not touch the permit scorer (works well) — only keep parity.
- No new data sources; no schema migration in P1.
- Not AI-heavy: keep filtering/scoring deterministic and explainable.
- Architecture dashboard relevance is out of scope (separate feature).

## 4. Definition of a “good match” (for golden labeling)

A tender is **relevant** to a company when all hold:

- **Work-type** is construction work aligned with the company’s discipline (for a GC: building / alteration / structural; not goods, vehicles, food, software, pure consulting, or unrelated services).
- **Region** is within the company’s operating area (or the company is province-wide).
- **Scale** is plausibly within the company’s range when tender value is known.

Anything that is non-construction procurement, clearly out-of-region for a regional firm, or wildly out-of-scale is **not relevant**.

## 5. Acceptance criteria (measurable)

- Golden set: 8–10 companies Paata knows, top-10 hand-labeled relevant/not (~30 min).
- **precision@10 ≥ 0.8** on the golden set after the change (baseline Naki ≈ 0.2).
- **Zero** non-construction-work items (goods/vehicles/food/pure-software) in any top-10.
- Out-of-region items rank below in-region for regional companies.
- **Permit results unchanged** (parity) and construction companies that already work don’t regress.
- Every surfaced item shows a human-readable reason; if fewer than N qualify, the UI says “no strong matches” rather than padding with low-relevance items.

### After Phase 1 (measured 2026-06-16)

- Vending / police vehicles / tilt wrecker / fleet-tires removed from top results ✅
- precision@7 improved from ~2/10 → ~4/7
- Region penalty partially working but Carpenter Creek Bridge (New Denver, ~600 km) still at score 69 — root cause: `primary_city` is empty for Naki Ocran, and `neighborhoods` contains street names (e.g. “Davie Street”), not city names, so city-level token matching fails and no penalty is applied.

## 6. Architecture — the matching funnel

> Principle: **filter hard early → score precisely → AI last → always explain.** Junk is removed by gates before scoring, not scored at 64 and shown.

- **L0 Sourcing** — load recent open tenders (as today).
- **L1 Work-type gate (hard)** — reuse/extend `matches_target_category()` on title (+category as weak hint) to drop goods/vehicles/food/software/non-construction. This is the single highest-leverage change.
- **L2 Region gate (soft penalty)** — use federal `location`; infer region for commercial from title/org. Penalize (not hard-drop) out-of-region for regional firms; neutral when company region unknown.
- **L3 Rule scoring (rebalanced)** — keep signals but: wire `estimated_value` into value-fit so it can **penalize** scale mismatch; cap keyword so it can’t dominate; bring tenders toward permit-scorer rigor.
- **L4 AI rerank** — top-K survivors only (existing hybrid path).
- **L5 Threshold + explainability** — surface only above a quality bar; honest empty-state otherwise.

## 7. Phased plan

**Phase 1 — stop the bleeding (cheap, high impact; behind a flag)**

1. Apply `matches_target_category()` in discovery candidate loading (L1 gate).
1. Wire `estimated_value` into the discovery payload + value-fit (revive L3 value signal).
1. Region soft-penalty using federal `location` (L2), lenient for commercial.
   Measure precision@10 on the golden set before/after.

**Phase 2 — rebalance + UX**

- Fix region matching: add city-level extraction from company address / Google data, not just neighborhood street tokens.
- Add trade-type filter: exclude pure consulting, maintenance-only, and road-consulting from GC candidate pool.
- Cap keyword score dominance (currently ~27/100, fires on almost everything).
- Threshold + “no strong matches” empty state.

**Phase 3 — durability + tuning**

- Tune weights against golden precision@k.
- Optionally backfill a `work_type` column at ingest so L1 is a cheap query filter, not per-request classification.

## 8. Test & eval

- **Golden set:** 8–10 companies, top-10 labeled (CSV/notes). Naki Ocran is entry #1 (snapshot above).
- **Metric:** precision@10 per company; report mean before/after.
- **Parity:** permit results and known-good construction companies unchanged.
- **Capture script:** dump top-15 with score breakdown per golden company (before/after) for the comparison table.

## 9. Risks / rollback

- All Phase-1 changes behind a feature flag; old path retained until precision@10 target is met.
- `commercial_tenders` has no location → region gate stays lenient there (avoid dropping valid local commercial tenders).
- Don’t trust `category` as a positive signal (portal bucket); rely on title + `matches_target_category()`.

## 10. Open questions

- Region matching: radius vs province match? Naki `primary_city`/`primary_province` empty → infer from neighborhoods / own permits?
- Work-type storage: compute in discovery now vs backfill a `work_type` column at ingest (P3).
- Final `min_score` threshold and empty-state copy.
