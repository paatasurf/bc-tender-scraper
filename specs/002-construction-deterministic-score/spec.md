# Feature Specification: Deterministic Internal Match Scoring (Construction Dashboard)

**Feature Branch**: `002-construction-deterministic-score`

**Created**: 2026-06-15

**Status**: Draft

**Input**: User description: "Apply the deterministic scoring engine to the CONSTRUCTION dashboard (INTERNAL MATCH SCORE), the same way it already works on the architecture dashboard. Total Score must always equal the sum of breakdown components. Construction dashboard only."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Trustworthy Internal Match Score on Construction Dashboard (Priority: P1)

A construction company user opens the TenderScope construction company intelligence
view and reviews tender opportunities with **Internal match score** badges. Each
match shows a total score (0–100) and a breakdown (keywords, category, specialization,
location, value fit, reliability, freshness). The **Total** displayed in the tooltip
always equals the **Sum** of the breakdown point values shown below it.

**Why this priority**: Users currently see totals (e.g., 54) that do not match the
sum of breakdown components (e.g., 37) because the total comes from a legacy stored
score while the breakdown is computed separately. This disconnect breaks client trust.

**Independent Test**: Open the construction dashboard for a known company with multiple
tender matches. For every match showing Internal match score, verify displayed total
equals the sum of all seven breakdown component points.

**Acceptance Scenarios**:

1. **Given** a construction company with permit history and open tender matches,
   **When** the user opens the internal match score tooltip on any tender card,
   **Then** the large total at the top equals the "Sum" row at the bottom of the
   breakdown.
2. **Given** any construction match with a visible breakdown, **When** the user adds
   keyword, category, specialization, location, value fit, reliability, and freshness
   points, **Then** the sum exactly equals the displayed total score.
3. **Given** a match where all breakdown components are zero, **When** the user views
   the total, **Then** the total is also zero—not a non-zero legacy value.

---

### User Story 2 - Transparent Component Explanations (Priority: P2)

The user expands the internal match breakdown and understands why each component
earned its points. Each component shows a human-readable detail string. Location
scoring reflects city or region alignment only—never street-level address matching.

**Why this priority**: Transparency requires both numeric consistency and understandable
reasons per component, consistent with the architecture dashboard experience.

**Independent Test**: Sample matches across high, medium, and low scores; confirm
each component has a detail string and location details reference city/region/municipality
terminology only.

**Acceptance Scenarios**:

1. **Given** a company whose service areas overlap a tender's city, **When** the user
   reads the location component detail, **Then** it describes city or regional fit
   without citing street addresses.
2. **Given** a match with strong keyword overlap, **When** the user reads the keywords
   component, **Then** points reflect that overlap and the detail names the matching
   signals.

---

### User Story 3 - Consistent Scores Across Refreshes (Priority: P3)

When the same construction company–tender pair is scored again without underlying
data changes, the user sees the same total and component breakdown as before.

**Why this priority**: Deterministic scoring supports auditability and prevents the
appearance of arbitrary score drift.

**Independent Test**: Score the same company–tender pair twice; compare totals and
each component's points.

**Acceptance Scenarios**:

1. **Given** unchanged company and tender records, **When** matches are loaded again,
   **Then** total score and all component points are identical to the prior view.
2. **Given** updated company permit counts or project types, **When** matches are
   rescored, **Then** component points reflect the updated inputs deterministically.

---

### Edge Cases

- What happens when company project-type or permit history is empty? Relevant components
  award 0 points with detail explaining insufficient history.
- What happens when tender location cannot be resolved to city/region? Location component
  awards 0; system MUST NOT fall back to street address for scoring.
- What happens when tender value or company scale data is missing? Value fit awards 0
  or neutral partial credit with explicit insufficient-data detail.
- What happens when tender deadline is expired or missing? Freshness applies defined
  deduction rules; detail states expired or unknown deadline.
- What happens when a cached legacy score exists in storage? Displayed total MUST come
  from the deterministic breakdown sum, not the legacy field alone.
- What happens for permit or contract-award opportunity types on the construction
  dashboard? This feature applies to **tender internal match scores** only; non-tender
  opportunity scoring is unchanged unless already covered by separate rule logic.

## Requirements *(mandatory)*

### Constitution Compliance *(mandatory for TenderScope)*

Reference: `.specify/memory/constitution.md`

- **CC-001**: Satisfied — total internal match score MUST equal the sum of explicitly
  defined component points shown in the breakdown.
- **CC-002**: Satisfied — no LLM MAY generate scores, component points, or rankings
  for construction internal match scoring. Narrative text (if any) MAY describe
  pre-computed results only.
- **CC-003**: Satisfied — location component MUST use city/region/municipality matching
  only; street addresses MUST NOT influence component points.
- **CC-004**: Satisfied — construction match API responses consumed by the dashboard
  MUST expose total score and structured breakdown in the existing response envelope
  without breaking unrelated endpoints.
- **CC-005**: Satisfied — all component calculations and total aggregation MUST be
  implemented in server-side Python scoring logic (reusing the same deterministic
  engine pattern established for architecture), not in frontend heuristics or prompts.

### Functional Requirements

#### Scoring Model

- **FR-001**: System MUST compute construction internal match scores on a 0–100 scale
  as the exact sum of seven components: keywords, category, specialization, location,
  value fit, reliability, and freshness.
- **FR-002**: System MUST expose each component with identifier, display label, points
  earned, and a detail string explaining the assignment.
- **FR-003**: System MUST enforce `total_score == sum(component.points)` before any
  match is returned to the construction dashboard or persisted for display.
- **FR-004**: System MUST NOT display or return a legacy total score field when it
  disagrees with the sum of the current breakdown components.

#### Component Definitions (Construction Tender Matches)

- **FR-005** — **Keywords (max weight per engine definition)**: Points based on overlap
  between company trade/project vocabulary and tender title, description, and category
  signals (permit history, project types, company name tokens).
- **FR-006** — **Category (max weight per engine definition)**: Points based on alignment
  between tender category/project type and company historical project categories.
- **FR-007** — **Specialization (max weight per engine definition)**: Points based on
  trade tags, dominant sector, capability profile, and specialty signals vs tender scope.
- **FR-008** — **Location (max weight per engine definition)**: Points based on city
  or BC region overlap between company service areas/neighborhoods and tender location
  signals. MUST NOT use street address or precise geocoding.
- **FR-009** — **Value fit (max weight per engine definition)**: Points based on fit
  between tender estimated value and company typical project scale (average project
  value, award value percentiles, permit value history).
- **FR-010** — **Reliability (max weight per engine definition)**: Points based on
  company track-record signals (project volume, award history, enrichment reliability
  score where available).
- **FR-011** — **Freshness (max weight per engine definition)**: Points based on tender
  deadline proximity; expired or missing deadlines reduce points per defined rules.

#### Dashboard & API Scope

- **FR-012**: This feature applies to the **construction dashboard** internal match
  score display and its backing data path only. Architecture dashboard behavior
  completed in feature `001-deterministic-ai-match` MUST remain unchanged.
- **FR-013**: The construction dashboard MUST read the deterministic total score from
  the same source as the breakdown (computed total), not a separate legacy score field.
- **FR-014**: System MUST reuse the existing deterministic scoring engine architecture
  (Python module pattern, breakdown JSON persistence, sum invariant) adapted for
  construction company and tender inputs (`companies`, federal/commercial tenders).
- **FR-015**: System MUST NOT change scraper logic, pipeline ingestion, or unrelated
  API endpoints outside the construction internal match scoring and display path.

#### Persistence & Caching

- **FR-016**: System MUST persist total score and structured breakdown together for
  construction company–tender pairs so cached results remain self-consistent.
- **FR-017**: When serving cached matches, system MUST return breakdown and total from
  stored deterministic results; legacy score-only rows MUST be rescored or mapped so
  displayed total matches breakdown.

#### Testing

- **FR-018**: System MUST include automated unit tests asserting `total == sum(components)`
  for construction match scoring across representative company/tender fixtures (empty
  history, strong fit, partial fit, missing location/value).

### Key Entities

- **Construction Company** (`companies`): Profile including project types, neighborhoods,
  permit counts, award history, trade tags, scale metrics, and reliability signals used
  as component inputs.
- **Construction Tender** (`tenders`, `commercial_tenders`): Federal or commercial
  opportunity with title, category, organization, value, deadline, and city/region
  signals.
- **Tender Match** (`tender_matches`, `company_kind=construction`): Cached company–
  tender pairing with deterministic total score, optional narrative text, and structured
  breakdown JSON.
- **Internal Match Breakdown**: Logical object with seven components, each with points
  and detail; total is derived exclusively from component sums.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of construction internal match scores shown in QA sampling have
  displayed total equal to the sum of visible breakdown component points (zero tolerance
  for mismatch).
- **SC-002**: Zero user-reported incidents of "total does not match sum" on the
  construction dashboard for 30 days post-release.
- **SC-003**: Automated unit test suite includes at least one construction fixture test
  that fails if total ≠ sum(components), and passes on all committed scoring logic.
- **SC-004**: Users can identify the top two scoring reasons from the breakdown alone
  in under 10 seconds without reading optional narrative text.
- **SC-005**: No regression in architecture dashboard match score integrity (architecture
  totals continue to equal breakdown sums).

## Assumptions

- Feature `001-deterministic-ai-match` provides the reference implementation pattern
  (Python scoring module, breakdown JSON on `tender_matches`, sum invariant, optional
  Claude narrative after scoring).
- Construction internal match scores are shown via the existing company intelligence
  dashboard tooltip (`Internal match score` / `Internal match breakdown`), fed by
  opportunity discovery and/or AI matching paths for `kind=construction`.
- The seven-component breakdown model already exists in the frontend sum display;
  this feature aligns backend totals with that model rather than inventing a new UI.
- Component maximum weights for construction will mirror or extend the established
  deterministic engine weights during planning; exact sub-tier thresholds are
  implementation details as long as they are deterministic and unit-tested.
- Hybrid/Discover scoring paths that previously stored Claude-generated construction
  scores will migrate to deterministic totals for display; Claude MAY remain for
  optional narrative only, consistent with constitution.
- Permit and contract-award cards on the construction dashboard use separate rule-based
  scoring and are out of scope unless they also show the broken total-vs-sum pattern
  for tender matches specifically.
