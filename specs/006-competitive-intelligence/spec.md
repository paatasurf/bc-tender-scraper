# Feature Specification: Competitive Intelligence (Phase 1)

**Feature Branch**: `006-competitive-intelligence`

**Created**: 2026-06-17

**Status**: Draft

**Input**: Feature 006 — Competitive Intelligence (Phase 1). Deterministic, explainable competitive-intelligence module for company profiles. Compute-on-read from existing tables (`companies`, `arch_companies`, `permits`, `contract_awards`, `cip_json`). No new data sources, tables, or migrations.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Benchmark Against Market on Company Profile (Priority: P1)

A construction company owner or BD manager opens a company profile in TenderScope and immediately sees how that company compares to its market on five key metrics: total projects, total project value, average project value, awards won, and reliability score. Each metric shows **You**, **Market Median**, and **Top-Rival Median** so the user can answer "Are we bigger, smaller, or typical for our sector and city?" without exporting data to a spreadsheet.

**Why this priority**: Benchmarking is the fastest path to perceived value — users understand comparisons before they understand threat scores or peer rankings.

**Independent Test**: Open any construction company profile with at least three peers in the same sector and city. Verify all five benchmark rows render with numeric values for You and Market Median; Top-Rival Median populates when competitors are identified.

**Acceptance Scenarios**:

1. **Given** a construction company with permit history in Vancouver and `dominant_sector = institutional`, **When** the user views the competitive intelligence section, **Then** the benchmark strip shows total projects, total value, average project value, award count, and reliability score with three columns (company, market median, top-rival median).
2. **Given** a company whose metrics exceed the market median on total value, **When** the user reads the benchmark strip, **Then** the company value is visually distinguishable from the median (e.g., above/below indicator).
3. **Given** a market cohort with fewer than eight matching companies, **When** benchmarks are computed, **Then** the system widens the cohort (drops city gate) and labels the market definition in the response so the user knows the comparison scope.

---

### User Story 2 - See Top Competitors Automatically (Priority: P1)

The user views a ranked list of 3–5 competitors automatically identified for the subject company — no watchlist setup, no manual peer selection. Each competitor shows a name, summary stats, and a link to their profile. Competitors are relevant: same sector/trade, overlapping geography, and comparable project scale.

**Why this priority**: Actionable competitive intelligence requires knowing *who* matters, not just market averages.

**Independent Test**: Open profiles for three known Vancouver GCs in the same sector. Verify returned peers share sector/trade and geographic overlap; list length is 3–5 when sufficient market data exists.

**Acceptance Scenarios**:

1. **Given** a subject company with `total_projects >= 2` and peers in the same `dominant_sector` and city, **When** competitive intelligence loads, **Then** the response includes 3–5 ranked competitors without any user configuration.
2. **Given** a subject company and its automatically selected peers, **When** the user inspects peer names, **Then** no peer is the subject company itself.
3. **Given** a market with fewer than three qualifying peers after cohort filtering, **When** competitive intelligence loads, **Then** `top_competitors` is empty and `warnings` includes `insufficient_market_data`.

---

### User Story 3 - Understand Competitive Threat with Transparent Score (Priority: P2)

For each identified competitor, the user sees a **Competitive Threat Score** (0–100) with an expandable breakdown showing exactly how geographic overlap, category overlap, value overlap, award activity, and permit activity contributed. The displayed total always equals the sum of breakdown component points. Explanations reference cities and sectors — never street addresses.

**Why this priority**: A single ranked number drives action; transparent breakdown builds trust (constitution principle #1).

**Independent Test**: For any peer with a threat score, sum the five breakdown component points and verify equality with the displayed total. Re-request the same company–peer pair and confirm identical scores.

**Acceptance Scenarios**:

1. **Given** two companies with overlapping Vancouver service cities and shared institutional sector focus, **When** threat score is computed, **Then** geographic and category components contribute positive points and the breakdown sums to the total.
2. **Given** the same subject and peer with unchanged underlying data, **When** competitive intelligence is requested twice, **Then** threat score and all component points are identical.
3. **Given** a threat score breakdown, **When** the user reads geographic detail strings, **Then** only city, municipality, or region names appear — no street-level addresses.

---

### User Story 4 - Architecture Firm Profile with Graceful Degradation (Priority: P3)

An architecture firm user opens an `arch_companies` profile. Competitive intelligence still shows benchmarks and peers based on permit/Houzz/CIP data, but award-based metrics display as not applicable because architecture firms are not tracked in the awards table. Threat scores omit the award-activity component and carry a reduced-confidence flag.

**Why this priority**: Architecture Intelligence is a first-class product surface; Phase 1 must not break or mislead those users.

**Independent Test**: Open an `arch_companies` profile with Houzz or permit data. Verify awards benchmark row shows N/A, threat breakdown has no award-activity component (or shows 0 with N/A label), and `confidence` reflects fewer active components.

**Acceptance Scenarios**:

1. **Given** an architecture company profile, **When** competitive intelligence loads, **Then** `award_count` benchmark shows N/A and peers are selected from `arch_companies` cohort.
2. **Given** an architecture peer threat score, **When** the user views the breakdown, **Then** at most four scoring components contribute (geo, category, value, permit) and confidence is `medium` or `low` unless permit data is rich.

---

### Edge Cases

- Subject company has zero projects and zero awards → return 200 with empty peers, low confidence, and explanatory warning.
- `ai_reliability_score` is null for subject or peers → reliability benchmark uses median over non-null values only; show "—" when no values exist.
- CIP not yet built (`cip_json` null) → lazy-build via existing `get_cip()` path; response may be slower on first request.
- Peer has awards but `company_id` not linked on `contract_awards` → award activity falls back to zero with detail "no linked awards."
- Permit 90-day scan returns no rows for a peer → permit activity uses recency from `last_project_date` as partial signal.
- `peer_limit` query param below 3 or above 5 → clamp to 3–5 range.
- Company not found → 404 standard error envelope.

## Requirements *(mandatory)*

### Constitution Compliance *(mandatory for TenderScope)*

Reference: `.specify/memory/constitution.md`

- **CC-001**: Satisfied — Competitive Threat Score MUST equal the sum of five breakdown component points (`geographic_overlap` max 25, `category_overlap` max 25, `value_overlap` max 20, `award_activity` max 15, `permit_activity` max 15). API and UI MUST expose `breakdown` with `points` and `max_points` per component.
- **CC-002**: Satisfied — no LLM MAY generate threat scores, peer rankings, benchmark medians, or confidence labels. Narrative text (if added later) MAY describe pre-computed results only.
- **CC-003**: Satisfied — geographic overlap MUST use city-level signals (`primary_city`, CIP `service_cities`, CIP `concentration_map` geos). Street addresses and neighborhood street tokens MUST NOT appear in geographic scoring explanations.
- **CC-004**: Satisfied — new endpoints MUST follow existing API conventions (`_row_to_dict` snake_case fields, structured error responses, consistent top-level keys with sibling company endpoints).
- **CC-005**: Satisfied — all scoring, peer selection, cohort filtering, and median computation MUST be implemented in Python modules under `pipeline/competitive_intel/`.

### Functional Requirements

#### Scope boundaries

- **FR-001**: Phase 1 MUST include exactly three capabilities: Competitive Threat Score, Top Competitors, and Benchmark Strip. Watchlists, alerts, missed-opportunities, and market-positioning dashboards are OUT OF SCOPE.
- **FR-002**: System MUST NOT require new data sources, database tables, or schema migrations for Phase 1.
- **FR-003**: System MUST compute all Phase 1 outputs on read from existing PostgreSQL tables: `companies`, `arch_companies`, `permits`, `contract_awards`, and persisted `cip_json`.

#### API

- **FR-004**: System MUST expose a single consolidated endpoint per company kind:
  - `GET /api/companies/{company_id}/competitive-intelligence?peer_limit=5`
  - `GET /api/arch-companies/{company_id}/competitive-intelligence?peer_limit=5`
- **FR-005**: Response MUST include `benchmark`, `top_competitors` (each with embedded `threat_score` and `threat_breakdown`), `market` (cohort definition and size), `engine_version`, `computed_at`, and optional `warnings` array.
- **FR-006**: `peer_limit` MUST be clamped to integer range 3–5 (default 5).

#### Benchmark Strip

- **FR-007**: Benchmark MUST compare subject company against market median and top-rival median for: `total_projects`, `total_value`, `avg_project_value`, `award_count`, `ai_reliability_score`.
- **FR-008**: Market cohort MUST filter by same `kind` (construction vs architecture), same `dominant_sector` (fallback: `primary_trade`), and same city (`primary_city` for construction; parsed service city for architecture).
- **FR-009**: When market cohort size is fewer than eight, system MUST widen cohort by removing the city gate while retaining sector/trade filter.
- **FR-010**: Quality gate for cohort members: `total_projects >= 2` OR `award_count >= 1`.
- **FR-011**: Top-rival median MUST be computed from the median of each metric across the returned top competitors (up to `peer_limit`).
- **FR-012**: For architecture kind, `award_count` benchmark MUST return `null` with label indicating not applicable.

#### Top Competitors

- **FR-013**: System MUST auto-select 3–5 peers without user watchlist or configuration.
- **FR-014**: Peer candidate filter MUST require: same kind; `id != subject`; (`dominant_sector` match OR `primary_trade` match); geographic gate (shared city via `primary_city` or CIP `service_cities`); quality gate per FR-010.
- **FR-015**: Similarity pre-score MUST be `0.35 × category_overlap + 0.35 × geographic_overlap + 0.30 × value_overlap` (raw 0–100 scale), keeping top 20 candidates.
- **FR-016**: Final peer list MUST be top 3–5 by Competitive Threat Score descending; tie-break by `total_value` descending.
- **FR-017**: When fewer than three peers pass filtering, `top_competitors` MUST be empty and `warnings` MUST include `insufficient_market_data`.

#### Competitive Threat Score

- **FR-018**: Threat score MUST be an integer 0–100 per peer, computed as the sum of five weighted components with fixed max points: geographic 25, category 25, value 20, award 15, permit 15.
- **FR-019**: Geographic overlap raw score MUST combine city-level Jaccard similarity on CIP `service_cities` ∪ `concentration_map` geos (60% weight) with city-token overlap (40% weight), scaled 0–100, with +10 bonus (capped at 100) when `primary_city` matches.
- **FR-020**: Category overlap raw score MUST use Bhattacharyya coefficient on CIP `sector_focus` distributions (0–100); fallback to Jaccard on `project_types` + `award_categories` when `sector_focus` is empty.
- **FR-021**: Value overlap raw score MUST use log-distance between CIP `value_range.median` (fallback: `avg_project_value`) with range-intersection bonus when `p25`–`p75` bands overlap.
- **FR-022**: Award activity raw score MUST use count of `contract_awards` in trailing 90 days linked by `company_id`, normalized against market cohort p90, plus buyer-client overlap bonus from `award_clients` Jaccard.
- **FR-023**: Permit activity raw score MUST combine 90-day permit count (normalized to cohort p90, 50% weight) with recency from `last_project_date` (50% weight). Permit 90-day scan MUST run only for final top-5 peers, capped at 500 permit rows per peer lookup via `normalize_vendor_name(applicant)`.
- **FR-024**: For architecture kind, award-activity component MUST be omitted (0 points, detail "N/A — awards not tracked for architecture firms").
- **FR-025**: `threat_breakdown` MUST follow the same structure as tender match breakdowns: `score`, `breakdown[]` with `factor`, `label`, `points`, `max_points`, `detail`, and `reasons[]` listing top contributing components.
- **FR-026**: `confidence` MUST be `high` when ≥4 components have raw score > 0, `medium` when 2–3, `low` otherwise.

#### UI (Phase 1 delivery)

- **FR-027**: Company profile dashboard MUST render a Competitive Intelligence section consuming the single API endpoint (no multiple round-trips).
- **FR-028**: UI MUST display benchmark strip (three columns), competitor cards sorted by threat score, and expandable threat breakdown per peer.
- **FR-029**: UI MUST show empty state when `warnings` contains `insufficient_market_data` and footnote that scores are deterministic from permits, awards, and company intelligence profile.

#### Reuse constraints (no reinvention)

- **FR-030**: Implementation MUST reuse `get_cip()` from `pipeline/cip_builder.py` for `sector_focus`, `concentration_map`, `value_range`, `neighborhoods`, `dominant_sector`.
- **FR-031**: Breakdown structure MUST reuse `BreakdownFactor` and `weighted_fit()` from `pipeline/scoring/explain.py`.
- **FR-032**: Token overlap utilities MUST reuse `_tokenize()` patterns from `pipeline/opportunity_discovery.py`.
- **FR-033**: City parsing MUST reuse `_parse_city_from_address` from `pipeline/scoring/construction_match_scoring.py`.
- **FR-034**: Vendor normalization MUST reuse `normalize_vendor_name()` from `pipeline/company_matching.py`.

### Key Entities

- **Subject Company**: The profile being viewed (`companies` or `arch_companies` row plus hydrated CIP).
- **Market Cohort**: Set of companies matching sector/trade and optional city gate, used for median benchmarks and activity normalization.
- **Peer Competitor**: A cohort member ranked by similarity pre-score then threat score; includes company identity, summary stats, threat score, and breakdown.
- **Competitive Threat Score**: Deterministic 0–100 integer measuring how much a peer overlaps and outpaces the subject across five components.
- **Benchmark Metric**: One of five comparable KPIs with subject value, market median, and top-rival median.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Users viewing a construction company profile with a valid market cohort (≥8 peers) see benchmark comparisons and 3–5 named competitors within one profile load (single API call).
- **SC-002**: 100% of displayed threat scores satisfy `total == sum(breakdown.points)` in automated tests across a fixture set of at least 20 subject–peer pairs.
- **SC-003**: Repeated requests for the same `company_id` and `kind` with unchanged data return identical threat scores and benchmark medians (determinism).
- **SC-004**: Geographic breakdown detail strings contain zero street-address tokens in automated validation across all fixture outputs.
- **SC-005**: Profiles in sparse markets (cohort < 3) show a clear empty state with warning — never fabricated peer names or median values.
- **SC-006**: 95% of competitive-intelligence API requests for companies with existing `cip_json` complete within 2 seconds under production-scale cohort sizes (≤200 candidates).

## Assumptions

- Permit data remains primarily Vancouver COV; market cohort geography is labeled accordingly in API responses (`data_scope: vancouver_permits_and_public_awards`).
- `cip_json` is populated lazily on first access with 24-hour TTL (existing `PROFILE_TTL_HOURS` behavior).
- Architecture firms continue to have no award columns on `arch_companies`; award N/A behavior is acceptable for Phase 1.
- `ai_reliability_score` sparsity is acceptable; medians computed over non-null subset only.
- Phase 1 does not include user accounts, watchlists, or saved preferences.
- Dashboard work ships in the same feature increment or immediately following API availability; API is the source of truth.
- Existing BD `competitive_intelligence` section (award intelligence items) remains unchanged; this feature is a distinct company-profile competitive module.

## Out of Scope (Phase 1)

- User watchlists and alerts
- Missed-opportunities revenue estimator
- Market-positioning dashboard (share estimates, growth trends)
- Tender pursuit collision (`tender_matches` integration)
- Bid tabulation / win-loss analytics
- New permit sources beyond existing Vancouver ingest
- Caching tables or materialized views for peer rankings
- Claude-generated explanations of threat scores

## Module Layout (implementation reference)

```
pipeline/competitive_intel/
  cohort.py       # market cohort + peer candidate SQL filters
  overlap.py      # geo, category, value overlap pure functions
  activity.py     # award_90d, permit_90d counters
  threat_score.py # ThreatScoreResult + compute_threat_score()
  peers.py        # select_top_competitors()
  benchmark.py    # compute_benchmark_strip()
  service.py      # get_competitive_intelligence() orchestrator
```
