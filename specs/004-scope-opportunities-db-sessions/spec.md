# Feature Specification: Scoped Database Sessions for Opportunities Discovery

**Feature Branch**: `004-scope-opportunities-db-sessions`

**Created**: 2026-06-16

**Status**: Draft

**Input**: User description: "Fix DB connection pool exhaustion (QueuePool limit reached) by scoping DB sessions to short DB-only phases in the opportunities code path. Root cause: a DB session stays checked out during slow discovery work (scoring hundreds of tenders, assembly). With limited pool capacity, concurrent/slow requests exhaust connections and the whole site hangs. Goal: open sessions only for DB reads/writes, run CPU work without a connection, preserve identical ranking and scores. Scope: opportunities endpoints only. Out of scope: raising pool limits as primary fix, scrapers, scoring quality, frontend."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Discover Opportunities Without Taking Down the Site (Priority: P1)

A construction or architecture user clicks **Discover opportunities** on a company
profile. The request completes and returns the same ranked opportunities as
before, while other users can still load permits, tenders, and health checks
without the entire application freezing.

**Why this priority**: Production currently exhausts database connections during
opportunities discovery; simple list endpoints fail even though they are unrelated.
This is a site-wide availability failure triggered by one heavy feature.

**Independent Test**: With several concurrent discover-requests in flight, confirm
`/api/companies/{id}/opportunities` and `/api/arch-companies/{id}/opportunities`
return successfully and lightweight endpoints (e.g., permits, tenders lists) still
respond within normal time.

**Acceptance Scenarios**:

1. **Given** a healthy production database and a company with a large candidate
   pool, **When** a user requests opportunities discovery, **Then** the response
   returns the same match list, scores, and ordering as the pre-fix baseline for
   that company and query parameters.
2. **Given** three or more simultaneous opportunities discovery requests for
   different companies, **When** each request runs, **Then** all complete without
   connection-pool timeout errors and without blocking unrelated API calls.
3. **Given** a user loads permits or tenders while another user runs opportunities
   discovery, **When** both actions occur at the same time, **Then** the list
   endpoints respond successfully without waiting for the discovery request to finish.

---

### User Story 2 - Construction and Architecture Parity (Priority: P1)

Users on both the construction company dashboard and the architecture company
dashboard can discover opportunities reliably under load.

**Why this priority**: Both code paths share the same session-holding pattern and
both have exhibited timeouts in production.

**Independent Test**: Run discovery for one construction company ID and one
architecture company ID under concurrent load; verify both succeed and match
baseline outputs.

**Acceptance Scenarios**:

1. **Given** a construction company profile, **When** opportunities are discovered,
   **Then** response shape, scores, breakdowns (where present), and match ordering
   are unchanged from the last known-good baseline.
2. **Given** an architecture company profile, **When** opportunities are discovered,
   **Then** response shape, scores, and match ordering are unchanged from the last
   known-good baseline.

---

### User Story 3 - Operator Confidence Under Deploy and Load (Priority: P2)

A platform operator can deploy a new API version and verify that opportunities
discovery no longer monopolizes database connections for minutes per request.

**Why this priority**: Operators need observable recovery after deploy and under
realistic concurrent usage without manual restarts.

**Independent Test**: After deploy, run a scripted burst of discovery requests
followed immediately by health and list-endpoint checks; confirm zero pool-exhaustion
errors in logs.

**Acceptance Scenarios**:

1. **Given** a freshly deployed API instance, **When** ten discovery requests are
   issued within one minute, **Then** no request fails with connection-pool
   exhaustion and health checks continue to pass.
2. **Given** discovery requests completing normally, **When** logs are reviewed,
   **Then** no single request holds a database connection for the full duration
   of CPU-heavy scoring or assembly phases.

---

### Edge Cases

- Discovery request fails mid-pipeline (database blip, company not found) — any
  opened session must be released so the connection returns to the pool.
- Company has zero rule candidates — response is still fast and does not hold a
  connection during empty assembly.
- Company has very large tender-match cache history — cache reads must not require
  holding a connection through subsequent CPU phases.
- Hybrid scoring persists new match rows — write phase uses a short session; failure
  during persist must not leak connections.
- Concurrent discovery for the **same** company ID — outputs remain correct; pool
  must not deadlock (no ranking change required, but no connection leak).
- Background database initialization running at container start — opportunities
  requests must not compound pool starvation (existing startup behavior preserved).

## Requirements *(mandatory)*

### Constitution Compliance *(mandatory for TenderScope)*

Reference: `.specify/memory/constitution.md`

- **CC-001**: N/A — no change to score decomposition or breakdown math; existing
  transparent scoring outputs MUST be preserved byte-for-byte in meaning
- **CC-002**: N/A — no change to LLM usage; discovery path remains deterministic
  Python scoring for inline hybrid (no new Claude scoring)
- **CC-003**: N/A — no location matching changes
- **CC-004**: Opportunities endpoints MUST return the same JSON response structure
  and field names as today; no breaking envelope changes
- **CC-005**: N/A — no change to scoring algorithms, weights, or thresholds
  (features 001/002 behavior preserved)

### Functional Requirements

- **FR-001**: The opportunities discovery pipeline MUST NOT keep a database
  connection checked out during CPU-only phases (rule scoring loops, candidate
  assembly, deterministic match scoring, response shaping).
- **FR-002**: Database sessions for opportunities discovery MUST be limited to
  discrete phases: (a) load required entities and candidate data, (b) optional
  hybrid match persistence, (c) final breakdown or cache reads needed for the
  returned result set — each phase opening and closing its own short-lived session.
- **FR-003**: After the read phase, ORM entities used for in-memory computation
  MUST be detached or converted to plain data structures so work can continue
  without an active session.
- **FR-004**: Ranking logic MUST remain identical: same rule scan scope, same
  hybrid top-20 selection, same threshold filtering, same assembly slots, same
  final ordering and scores for a given company and query parameters.
- **FR-005**: Both construction and architecture opportunities endpoints MUST
  follow the same session-scoping pattern.
- **FR-006**: Route handlers for opportunities MUST guarantee session cleanup on
  success, error, and timeout paths (no connection leak when a request aborts).
- **FR-007**: Unrelated API endpoints (permits, tenders, health, signals) MUST
  remain functional while opportunities discovery runs concurrently.
- **FR-008**: The fix MUST NOT rely on increasing connection pool size as the
  primary solution (pool tuning MAY be documented as optional mitigation only).

### Key Entities

- **Opportunities request**: A discover call for a company ID with `min_score`,
  `limit`, and kind (construction or architecture); produces a ranked `matches` list.
- **Discovery phase data**: In-memory structures (rule candidates, hybrid pairs,
  permit/award items, assembled top-N) passed between phases without a live session.
- **Database session**: A short-lived unit of work borrowing one pool connection
  for queries/commits only.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Under a test burst of at least five concurrent opportunities
  discovery requests, 100% complete successfully with no connection-pool exhaustion
  errors.
- **SC-002**: While one discovery request is in progress, lightweight list
  endpoints respond successfully in under 5 seconds for 95% of attempts in a
  concurrent test run.
- **SC-003**: For a fixed baseline company and query parameters, the returned
  match IDs, scores, and order are identical before and after the change (regression
  parity test).
- **SC-004**: No single opportunities request holds a database connection longer
  than 10 seconds cumulatively across all its phases in instrumented testing
  (CPU phases excluded).
- **SC-005**: Production operators observe zero `QueuePool limit reached` errors
  during a 24-hour window with normal discovery usage after release.

## Assumptions

- Baseline parity tests can use saved JSON responses or a pinned company ID from
  staging/production before the fix.
- Current pool defaults (small fixed size with bounded overflow) remain in place;
  this feature fixes hold time, not pool sizing.
- Opportunities discovery remains the primary long-running API path; other endpoints
  are comparatively fast.
- Hybrid inline scoring continues to persist match rows during discovery; write
  phases are in scope for short sessions.
- Frontend and Vercel proxy timeouts are unchanged; fixing pool exhaustion should
  allow requests to complete within existing limits.
- Features 001 (deterministic AI match) and 002 (construction deterministic score)
  define the scoring outputs that MUST NOT change.

## Out of Scope

- Raising `pool_size`, `max_overflow`, or `pool_timeout` as the primary fix
- Scraper pipelines, batch jobs, or scheduler session patterns (unless they share
  the same opportunities code path)
- Scoring algorithm changes, threshold tuning, or UI/dashboard redesign
- Architecture-specific product changes beyond session lifecycle in discovery
- Claude API integration changes (no new LLM calls in discover path)
