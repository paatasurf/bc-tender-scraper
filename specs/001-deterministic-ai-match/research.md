# Research: Deterministic AI Match Scoring

**Feature**: `001-deterministic-ai-match` | **Date**: 2026-06-14

## R1 — Root Cause of Score/Breakdown Disconnect

**Decision**: The bug is in `_normalize_scorer_payload` (`pipeline/ai_matching.py` lines 140–145), not the frontend.

**Rationale**: When Claude returns `score: 83` but all `breakdown.*.points` are 0, `computed = 0` and the code uses `declared` score: `score = computed if computed > 0 else declared`. The API returns 83 with zeroed breakdown keys.

**Alternatives considered**:
- Fix normalization only — rejected; constitution requires Python-native scoring, not prompt compliance fixes.
- Post-hoc validation rejecting mismatches — rejected; does not restore trust or determinism.

---

## R2 — Scoring Module Location and Patterns

**Decision**: New module `pipeline/scoring/arch_match_scoring.py`, reusing `BreakdownFactor` and `weighted_fit` patterns from `pipeline/scoring/explain.py`.

**Rationale**: Existing BD scoring (`bps.py`, `rps.py`, `mps.py`) already implements transparent weighted components. Keeps architecture match scoring consistent with repo conventions.

**Alternatives considered**:
- Inline all logic in `ai_matching.py` — rejected; violates single-responsibility and testability.
- Reuse `pipeline/scoring/rps.py` directly — rejected; RPS dimensions differ from the five architecture match components.

---

## R3 — Architecture Sync Candidate Selection

**Decision**: For `run_ai_matching_sync` (architecture), score **all** tenders loaded from `arch_tenders` (up to `max_tenders`), not Claude matcher subset.

**Rationale**: User constraint limits changes to scoring logic. Removing Claude matcher from sync path eliminates LLM influence on which tenders appear and simplifies deterministic ranking. Matcher agent (`run_tender_matcher`) remains in codebase for background batch runs (out of scope).

**Alternatives considered**:
- Keep Claude matcher, replace scorer only — rejected; matcher still uses Claude for relevance filtering, adding latency and non-determinism to candidate set.
- Rule-based pre-filter before scoring — deferred; scoring all ≤50 tenders is fast enough in Python.

---

## R4 — Component Scoring Formulas (v1)

**Decision**: Tiered deterministic formulas per component (documented in data-model.md). All sub-scores are integers; total is exact sum.

### Project type experience (max 40)

| Condition | Points |
|-----------|--------|
| No category/type overlap | 0 |
| Category fuzzy-matches a company `project_types` / `houzz_project_types` tag, count ≤2 | 15 |
| Same, count 3–10 | 28 |
| Same, count >10 | 40 |

Use `total_projects` as fallback count when type-specific count unavailable. Fuzzy match: normalized lowercase token overlap between `arch_tenders.category` and company type tags.

### Specialization / category (max 25)

| Condition | Points |
|-----------|--------|
| No overlap with `website_specializations`, `dominant_sector`, `trade_tags` | 0 |
| Partial token overlap | 12 |
| Strong overlap (category term in ≥2 specialization sources) | 25 |

### Region match (max 15)

| Condition | Points |
|-----------|--------|
| No city/region overlap | 0 |
| Partial (shared BC region keyword, e.g. "Vancouver", "Fraser Valley") | 8 |
| Direct city/district match between tender org/location text and `neighborhoods` / `houzz_service_areas` / `website_service_areas` | 15 |

**Never** use `google_address`, `lat`, `lng` for scoring. Extract region from `arch_tenders.company` (issuing org) and title heuristics only.

### Budget / value fit (max 10)

| Condition | Points |
|-----------|--------|
| Tender value unparseable or company scale unknown | 0 |
| Tender value within company `[value_p25, value_p75]` or ±50% of `avg_project_value` | 10 |
| Within 2× range | 5 |
| Outside 2× range | 0 |

Parse `arch_tenders.value` string via existing `_parse_value` helper.

### Deadline freshness (max 10)

| Condition | Points |
|-----------|--------|
| Valid future deadline >14 days out | 10 |
| Future deadline ≤14 days | 7 |
| Missing/unparseable deadline | 2 |
| Expired deadline | 0 |

**Alternatives considered**:
- Continuous linear scaling — rejected for v1; tiered rules are easier to explain in UI detail strings.
- ML-based type matching — rejected; violates constitution.

---

## R5 — API Breakdown Shape (Frontend Compatibility)

**Decision**: Return existing 7-key breakdown object; map 5 architecture components as follows:

| New component | API key | Max pts |
|---------------|---------|---------|
| Project type experience | `category` | 40 |
| Specialization match | `specialization` | 25 |
| Region match | `location` | 15 |
| Value fit | `value` | 10 |
| Freshness | `freshness` | 10 |
| (unused) | `keywords` | 0 — detail: "Included in project type score" |
| (unused) | `reliability` | 0 — detail: "Not used for architecture matching" |

**Rationale**: `v0-construction-dashboard/lib/ai-match-explanation.ts` expects 7 keys. User constraint avoids frontend changes. Sum of all 7 keys still equals total (40+25+15+10+10+0+0 = 100).

**Alternatives considered**:
- New 5-key schema + frontend update — rejected per user scope constraint.
- Store canonical 5-key in DB, map at API boundary — **selected** for `breakdown_json`; map on read/write.

---

## R6 — Persistence and Cache

**Decision**: Add `breakdown_json JSONB` column to `tender_matches`. Cache hit returns stored breakdown; cache miss runs deterministic scorer.

**Rationale**: `tender_matches` currently stores only `score` and `reasoning` — cached responses lose breakdown, forcing re-display inconsistency.

**Alternatives considered**:
- Embed breakdown in `reasoning` text — rejected; not structured, not parseable by UI.
- Separate table — rejected; unnecessary normalization for v1.

---

## R7 — Claude Explanation Prompt

**Decision**: New function `generate_arch_match_explanation` sends pre-computed component summary to Claude with explicit instruction: **do not output numbers or scores**.

**Rationale**: Constitution II. Fallback uses `build_reasons()` from `explain.py` when key missing or API fails.

**Alternatives considered**:
- Skip Claude entirely — rejected; spec P2 requires narrative explanation.
- Template-only explanations — acceptable as fallback, not primary.

---

## R8 — Anthropic API Key Gate

**Decision**: Relax global 503 check in `POST /api/ai-matching` for architecture sync only.

**Rationale**: Deterministic scoring must work offline from Claude. Construction and async paths unchanged.

**Alternatives considered**:
- Keep 503 — rejected; blocks dashboard when key expired even though scores are computable.
