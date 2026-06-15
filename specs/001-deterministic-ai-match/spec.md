# Feature Specification: Deterministic AI Match Scoring (Architecture Dashboard)

**Feature Branch**: `001-deterministic-ai-match`

**Created**: 2026-06-14

**Status**: Draft

**Input**: User description: "Redesign the AI Match scoring system for TenderScope architecture dashboard. Replace disconnected Claude-generated scores with a deterministic engine where Total Score = sum of 5 weighted components. Claude writes explanation text only."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Trustworthy Match Score on Architecture Dashboard (Priority: P1)

An architecture firm user opens the TenderScope architecture dashboard, selects their
company, and requests AI-matched tenders. Each match shows a total score (0–100) and
a five-part breakdown. The total always equals the sum of the breakdown points shown.
The user can see why a tender scored highly or poorly without guessing.

**Why this priority**: The current experience shows a total score (e.g., 83) while all
breakdown components display 0, which immediately erodes trust. Restoring score/breakdown
integrity is the core fix.

**Independent Test**: Request matches for a known architecture company and verify that
for every returned match, displayed total score equals the sum of the five component
scores, and each component shows a non-empty explanation of how points were assigned.

**Acceptance Scenarios**:

1. **Given** a company with strong residential project history, **When** the user views
   matches for a residential architecture tender, **Then** the project type component
   contributes a meaningful share of the total (not 0 while total is high).
2. **Given** any returned match, **When** the user adds the five component point values,
   **Then** the sum exactly equals the displayed total score.
3. **Given** a match result, **When** the user expands the score breakdown, **Then** each
   of the five components shows points (0–max), max weight, and a human-readable detail
   string explaining the assignment.

---

### User Story 2 - Understand Match Fit via Narrative Explanation (Priority: P2)

After viewing the numeric breakdown, the user reads a short narrative explanation that
summarizes why the tender is or is not a good fit. The narrative reflects the computed
breakdown and does not contradict the numbers.

**Why this priority**: Users still want plain-language guidance for prioritization, but
the explanation must describe deterministic results—not invent the score.

**Independent Test**: For a match with a known breakdown, verify the narrative references
the dominant scoring factors and does not introduce scores or rankings not present in the
breakdown.

**Acceptance Scenarios**:

1. **Given** a match with high region and specialization scores, **When** the user reads
   the explanation, **Then** it mentions geographic and specialization alignment.
2. **Given** the scoring service is unavailable for narrative generation, **When** matches
   are returned, **Then** users still receive the full numeric breakdown and a fallback
   explanation derived from the top contributing components.

---

### User Story 3 - Consistent Cached Match Results (Priority: P3)

When the same company–tender pair is matched again within the cache window, the user
sees the same total score and component breakdown as the prior request (unless underlying
company or tender data changed).

**Why this priority**: Predictable scores support comparison over time and prevent the
appearance of arbitrary AI drift.

**Independent Test**: Run matching twice for the same company within the cache period
and compare stored scores and breakdowns.

**Acceptance Scenarios**:

1. **Given** a freshly scored company–tender pair, **When** the user requests matches
   again within the cache window, **Then** the returned score and breakdown match the
   cached values.
2. **Given** cached match data exists, **When** the company’s project type counts or
   service regions are updated in the database, **Then** a new scoring run produces
   updated component values reflecting the new data.

---

### Edge Cases

- What happens when company project-type history is empty? Project type component awards
  0 points with detail explaining no matching experience.
- What happens when tender category or project type cannot be normalized? Category
  component awards 0 or partial credit using best-effort fuzzy match; detail states
  the ambiguity.
- What happens when neither company nor tender has a usable city/region? Region
  component awards 0; matching MUST NOT fall back to street address.
- What happens when tender value or company scale data is missing? Budget fit component
  awards 0 or neutral partial score with explicit “insufficient data” detail.
- What happens when tender deadline is expired or missing? Freshness component applies
  full deduction per rules; detail states expired or unknown deadline.
- What happens when Claude is unavailable? Numeric scoring still completes; explanation
  falls back to template text built from breakdown details.
- What happens when no tenders meet the minimum score threshold? User receives an empty
  match list with a clear message—not partial or misleading scores.

## Requirements *(mandatory)*

### Constitution Compliance *(mandatory for TenderScope)*

Reference: `.specify/memory/constitution.md`

- **CC-001**: Satisfied — total score MUST equal sum of five weighted components,
  each exposed in API and dashboard.
- **CC-002**: Satisfied — Claude MUST NOT generate scores or numeric breakdown values;
  it MAY generate narrative explanation text only, using pre-computed component results
  as input.
- **CC-003**: Satisfied — region matching uses city/district/municipality level only;
  street addresses MUST NOT influence the region component.
- **CC-004**: Satisfied — redesigned match response MUST use the existing consistent
  JSON envelope for `POST /api/ai-matching` sync responses, extended with structured
  breakdown (backward-compatible where possible).
- **CC-005**: Satisfied — all five component calculations and weight application MUST
  be implemented in server-side Python scoring logic, not in prompts.

### Functional Requirements

#### Scoring Model

- **FR-001**: System MUST compute match scores on a 0–100 scale as the exact sum of five
  components with fixed maximum weights totaling 100.
- **FR-002**: System MUST expose each component with: identifier, display name, points
  earned, maximum points, weight, and a detail string describing the assignment.
- **FR-003**: System MUST validate after calculation that `total_score == sum(component.points)`
  and reject or correct any mismatch before returning results.

#### Component Definitions (Architecture Matches Only)

- **FR-004** — **Project type experience (max 40 pts)**: Award points based on the
  company’s historical project count for the tender’s project type (derived from
  architecture company profile data such as project types, permit/project counts, and
  portfolio signals). Higher experience for the matching type yields higher points;
  no matching history yields 0.
- **FR-005** — **Specialization / category match (max 25 pts)**: Award points based on
  alignment between the tender category and the company’s declared specializations
  (website specializations, Houzz project types, trade tags, dominant sector).
- **FR-006** — **Region match (max 15 pts)**: Award points when the tender’s city or
  BC region aligns with the company’s service areas (neighborhoods, Houzz/website service
  areas, geographic reach). MUST NOT use street address or precise geocoding for scoring.
- **FR-007** — **Budget / project value fit (max 10 pts)**: Award points based on fit
  between tender estimated value and company scale (e.g., average project value, value
  percentiles, total portfolio value). Mismatched scale (tender far above/below typical
  company projects) reduces points.
- **FR-008** — **Deadline freshness (max 10 pts)**: Award points for tenders with valid
  upcoming deadlines; deduct fully or partially for expired deadlines or missing deadline
  data per defined freshness rules.

#### API & Persistence

- **FR-009**: System MUST redesign the existing synchronous architecture path of
  `POST /api/ai-matching` (`kind=architecture`, `sync=true`) to use deterministic
  scoring instead of Claude-generated scores.
- **FR-010**: System MUST persist total score, narrative explanation, and structured
  breakdown for each company–tender pair in `tender_matches` (or equivalent storage)
  so cached results include component data—not score alone.
- **FR-011**: System MUST read company data from `arch_companies` and tender data from
  `arch_tenders` when computing architecture matches.
- **FR-012**: System MUST return matches sorted by total score descending, filtered by
  configurable minimum score and result limit (preserving existing request parameters).

#### Narrative Explanation

- **FR-013**: System MAY call Claude to produce a 2–3 sentence narrative explanation
  AFTER all component scores are computed, passing the breakdown as read-only context.
- **FR-014**: Claude MUST NOT be invoked to produce numeric scores, component points,
  or rankings.
- **FR-015**: System MUST provide a deterministic fallback explanation when Claude is
  unavailable, synthesized from the top contributing components.

#### Scope Boundaries

- **FR-016**: This feature applies to the **architecture dashboard** matching flow only.
  Construction company matching (`kind=construction`) is out of scope for this release
  unless explicitly extended in a follow-up feature.
- **FR-017**: Tender candidate discovery (which tenders to evaluate) MAY continue using
  existing rule-based or catalog filtering; this feature replaces **scoring** only.

### Key Entities

- **Architecture Company** (`arch_companies`): Firm profile including project types,
  project counts, specializations, service areas/regions, scale metrics (total/average
  project value), and enrichment fields used for component inputs.
- **Architecture Tender** (`arch_tenders`): Opportunity with title, category, issuing
  organization, value, deadline, status, and location signals derivable at city/region
  level.
- **Tender Match** (`tender_matches`): Cached company–tender pairing with total score,
  narrative reasoning, structured breakdown JSON, company kind, and timestamps.
- **Match Score Breakdown**: Logical object attached to each match containing five
  components, each with points, max points, weight, and detail text.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of match results displayed in the architecture dashboard have a
  total score equal to the sum of their five visible component scores (zero tolerance
  for mismatch in QA sampling).
- **SC-002**: In user testing, at least 90% of participants rate score trustworthiness
  as “clear” or “very clear” when asked immediately after viewing a match breakdown
  (replacing the prior broken state where breakdown showed all zeros).
- **SC-003**: Users can identify the top two reasons a tender matched (e.g., project
  type, region) from the breakdown alone in under 10 seconds without reading the
  narrative explanation.
- **SC-004**: Match requests for a single company return ranked results within acceptable
  interactive wait time for dashboard use (target: under 15 seconds for typical catalog
  size, excluding optional narrative generation delay).
- **SC-005**: Zero production incidents of “score contradicts breakdown” reported for
  30 days post-launch on architecture matches.

## Assumptions

- Architecture company “project type experience” maps to permit/project counts and type
  tags already present on `arch_companies` (e.g., `project_types`, `total_projects`,
  Houzz/website project type fields, capability profile data)—not a new data collection
  pipeline.
- Tender city/region can be derived from existing tender fields (organization name,
  category metadata, or normalized location fields) at city/district granularity;
  perfect geocoding is not required for v1.
- The architecture dashboard already consumes `POST /api/ai-matching` sync responses;
  UI updates to render the new five-component breakdown are included in this feature’s
  delivery scope.
- Existing request parameters (`company_id`, `min_score`, `limit`, `max_tenders`,
  `kind=architecture`, `sync=true`) remain supported.
- `tender_matches` will gain or reuse a JSON column for breakdown storage; migration is
  acceptable as part of implementation planning.
- Construction matching continues on the legacy Claude-scoring path until a separate
  feature migrates it to the same deterministic engine.
- Component sub-scoring within each 0–max band uses tiered or proportional rules defined
  during planning; exact thresholds are implementation details as long as they are
  deterministic, documented, and unit-testable.
