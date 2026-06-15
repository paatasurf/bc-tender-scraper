<!--
Sync Impact Report
==================
Version change: (none) → 1.0.0
Modified principles: N/A (initial ratification)
Added sections:
  - Core Principles (5 principles)
  - Technology Stack
  - Development & Compliance
  - Governance
Removed sections: N/A
Templates:
  - .specify/templates/plan-template.md ✅ updated
  - .specify/templates/spec-template.md ✅ updated
  - .specify/templates/tasks-template.md ✅ updated
  - .cursor/rules/specify-rules.mdc ✅ updated
Follow-up TODOs: none
-->

# TenderScope Constitution

TenderScope is a BC construction market intelligence SaaS platform. This
constitution defines non-negotiable architectural and product rules that
supersede individual feature preferences and implementation shortcuts.

## Core Principles

### I. Transparent AI Scoring

Every opportunity or entity score MUST be fully decomposable and auditable.

- Total Score MUST equal the sum of explicitly defined, weighted components.
- Each component MUST expose its raw value, weight, and weighted contribution.
- UI and API responses MUST surface the breakdown; opaque composite scores are
  forbidden.
- Score changes MUST be traceable to specific component deltas.

**Rationale**: Construction professionals must trust and verify scoring. Hidden
or black-box scoring undermines adoption and regulatory confidence.

### II. Claude API Scope (Text Only)

The Claude API MUST be used exclusively for human-readable text generation.

- Claude MUST NOT generate scores, numeric ratings, rankings, or thresholds.
- Claude MUST NOT parse or classify data in ways that affect scoring outcomes.
- Permitted uses: summaries, explanations, narrative insights, and user-facing
  copy that describe pre-computed Python results.
- Any feature requiring numeric output MUST compute it in Python before (or
  without) invoking Claude.

**Rationale**: Separating computation from explanation keeps scoring deterministic,
testable, and constitution-compliant.

### III. Location Matching Granularity

Geographic matching MUST operate at city or regional level only.

- Matching logic MUST use normalized city names, municipalities, or BC regions.
- Street addresses, postal codes used as precise locators, lat/long pinpoints,
  and building-level geocoding MUST NOT drive match scores or filters.
- Location data MAY be stored for display, but MUST NOT influence scoring or
  matching beyond city/region boundaries.

**Rationale**: Street-level matching creates false precision, privacy risk, and
noisy results for a regional construction intelligence product.

### IV. Consistent API Response Structure

All API endpoints MUST return predictable, uniform JSON envelopes.

- Success and error responses MUST follow a shared schema (e.g., consistent
  top-level keys, error object shape, pagination metadata).
- Field naming MUST be consistent across endpoints (camelCase or snake_case —
  pick one project-wide convention and enforce it).
- Breaking response shape changes MUST be versioned or migrated with explicit
  deprecation.

**Rationale**: A stable API contract enables reliable React clients, third-party
integrations, and automated testing.

### V. Python-Native Scoring Logic

All scoring rules, weights, thresholds, and business logic MUST live in Python.

- Scoring algorithms MUST be implemented in testable Python modules — not in
  LLM prompts, prompt templates, or frontend code.
- Prompts MAY reference pre-computed scores for explanation but MUST NOT define
  how scores are calculated.
- Weight changes MUST be code changes with review, not prompt edits.

**Rationale**: Python scoring is unit-testable, version-controlled, and
independent of model behavior drift.

## Technology Stack

The following stack is the canonical deployment target for TenderScope:

| Layer | Technology | Hosting |
|-------|------------|---------|
| Backend API | Python, FastAPI | Railway |
| Database | PostgreSQL | Railway |
| Frontend | React | Vercel |

- New services or frameworks MUST justify deviation in the Complexity Tracking
  table of the feature plan.
- Data persistence for scoring configuration and audit trails MUST use
  PostgreSQL unless explicitly exempted.

## Development & Compliance

- Every feature spec MUST declare constitution impacts in its requirements.
- Every implementation plan MUST pass the Constitution Check gates before Phase 0
  research and re-check after Phase 1 design.
- Pull requests touching scoring, location logic, API responses, or Claude
  integration MUST include explicit compliance notes in the description.
- Scoring changes MUST include unit tests proving component sums equal total
  score and that weights are applied correctly.
- API changes MUST include contract tests or schema validation against the
  shared response envelope.

## Governance

- This constitution supersedes ad-hoc conventions, inline comments, and prompt
  instructions when conflicts arise.
- Amendments require: (1) documented rationale, (2) version bump per semver
  rules below, (3) template sync per the Sync Impact Report process, and
  (4) team acknowledgment before merge.
- **Version policy**: MAJOR = principle removal or redefinition; MINOR = new
  principle or material expansion; PATCH = clarifications and non-semantic edits.
- Compliance reviews SHOULD occur at plan approval, PR review, and release
  gates for features affecting scoring, location, or API surfaces.
- Runtime development guidance: `.specify/memory/constitution.md` (this file)
  and the active feature plan at `specs/<feature>/plan.md`.

**Version**: 1.0.0 | **Ratified**: 2026-06-14 | **Last Amended**: 2026-06-14
