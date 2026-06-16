# Feature Specification: Non-Blocking API Startup for Reliable Deploys

**Feature Branch**: `003-fix-railway-startup`

**Created**: 2026-06-15

**Status**: Draft

**Input**: User description: "Fix the Railway startup hang so deploys pass the /api/health healthcheck. Add connection timeout so DB init cannot block forever. Make startup non-blocking so Uvicorn binds the port and healthcheck can pass even if DB init is slow. Scope: startup/DB-connection path only — no scoring, frontend, or feature work changes."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Deploy Passes Healthcheck (Priority: P1)

A platform operator deploys a new version of the TenderScope API to the hosted
environment. The deployment completes successfully: the process starts, the HTTP
port becomes reachable within the platform healthcheck window, and the health
endpoint responds so the deploy is marked **healthy** rather than **failed**.

**Why this priority**: Production deploys currently fail because the application
never finishes startup when database initialization blocks indefinitely. No code
changes reach users until this is fixed.

**Independent Test**: Trigger a deploy on the hosting platform; confirm the
deploy status becomes healthy and the health endpoint returns a response within
the configured healthcheck timeout (e.g., five minutes or less).

**Acceptance Scenarios**:

1. **Given** a normal deploy with the database available within a reasonable
   startup window, **When** the new container starts, **Then** the platform
   healthcheck succeeds and the deploy is marked healthy.
2. **Given** a deploy where the database is temporarily slow to accept connections,
   **When** the container starts, **Then** the HTTP server still becomes reachable
   within the healthcheck window and the health endpoint responds (even if it
   reports degraded database status).
3. **Given** a deploy where the database is entirely unreachable, **When** the
   container starts, **Then** the process does not hang silently forever; the HTTP
   server becomes reachable and the health endpoint responds with a clear degraded
   or unavailable database indication within a bounded time.

---

### User Story 2 - Fast-Fail on Stalled Database Connections (Priority: P1)

When the database cannot be reached during startup initialization, the system
abandons the connection attempt within a bounded time and records a clear failure
instead of freezing with no log output.

**Why this priority**: Infinite blocking on the first database connection is the
root cause of silent startup failure; operators cannot diagnose or recover when
the process never progresses.

**Independent Test**: Start the API with an invalid or unreachable database host;
confirm startup completes (port bound) within seconds to minutes—not indefinitely—
and logs or health status indicate connection failure.

**Acceptance Scenarios**:

1. **Given** database credentials point to an unreachable host, **When** startup
   initialization runs, **Then** the connection attempt fails within a configured
   maximum wait (not unbounded) and the outcome is visible in logs or health status.
2. **Given** the database becomes reachable after initial failure, **When** a later
   health check or request occurs, **Then** the system can recover connectivity
   without requiring a full process restart (existing retry behavior preserved where
   applicable).

---

### User Story 3 - Unchanged Runtime Behavior When Database Is Healthy (Priority: P2)

Operators and API consumers who rely on scoring, scheduling, and data endpoints
see no change in behavior when the database is available and migrations complete
normally.

**Why this priority**: This fix is infrastructure-only; business features must not
regress.

**Independent Test**: With a healthy database, exercise representative API flows
(opportunities, health, scheduler status) and confirm responses match pre-fix
behavior.

**Acceptance Scenarios**:

1. **Given** a fully initialized database, **When** a user requests company
   opportunities or other existing endpoints, **Then** responses are unchanged
   in structure and correctness compared to the last working deploy.
2. **Given** a fully initialized database, **When** the health endpoint is queried,
   **Then** it reports connected/healthy database status as it did before.

---

### Edge Cases

- Database is in recovery mode briefly at container start (existing retry logic
  should still apply, but must not block port binding indefinitely).
- Database migrations take longer than the healthcheck window but the database
  is reachable — the app must still pass healthcheck (degraded OK) while migrations
  finish in the background or on first use.
- Multiple rapid deploy restarts while migrations hold table locks — startup must
  not deadlock the entire fleet; bounded waits and degraded mode preferred over
  silent hang.
- Scheduler starts before database init completes — scheduled jobs must not crash
  the process; existing graceful handling preserved.
- Health endpoint called before background init finishes — must return a valid
  JSON response indicating database not yet ready or degraded, not timeout or
  connection refused from the load balancer.

## Requirements *(mandatory)*

### Constitution Compliance *(mandatory for TenderScope)*

Reference: `.specify/memory/constitution.md`

- **CC-001**: N/A — no scoring changes
- **CC-002**: N/A — no LLM changes
- **CC-003**: N/A — no location matching changes
- **CC-004**: Health endpoint MUST continue to return the existing JSON shape;
  new fields (e.g., init status) MAY be added only if backward-compatible
- **CC-005**: N/A — no scoring logic changes

### Functional Requirements

- **FR-001**: Database connection attempts during startup initialization MUST
  have a bounded maximum wait time and MUST NOT block the process indefinitely.
- **FR-002**: Application lifespan startup MUST complete and allow the HTTP server
  to bind its port even when database initialization has not yet succeeded.
- **FR-003**: The health endpoint MUST be reachable and return a valid response
  as soon as the HTTP server is bound, without waiting for full database migration
  completion.
- **FR-004**: When the database is unavailable at startup, the health endpoint
  MUST indicate degraded or disconnected database status in a way operators can
  diagnose (existing `database_connected` field or equivalent).
- **FR-005**: When the database becomes available after a deferred or background
  initialization, the system MUST eventually reach the same initialized state as
  the current synchronous path (schema migrations applied, same as today).
- **FR-006**: Existing retry behavior for transient database errors (recovery
  mode, connection refused) MUST be preserved where already implemented.
- **FR-007**: The background scheduler MUST start as it does today without being
  blocked by database initialization completion.
- **FR-008**: System MUST NOT modify match scoring logic, opportunity discovery
  scoring, AI matching scoring, or any feature 001/002 behavior.
- **FR-009**: System MUST NOT modify frontend applications or dashboard code.
- **FR-010**: Changes MUST be limited to the application startup lifecycle and
  database connection/initialization path.

### Key Entities

- **Startup state**: Whether the HTTP server is listening, whether database
  initialization has started, completed, or failed, and optional last error message
  for operators.
- **Health response**: Existing health payload including overall status and
  database connectivity indicator; may reflect degraded state during background init.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of deploy attempts reach a responding health endpoint within
  the platform healthcheck timeout window (300 seconds or configured limit), even
  when the database is slow or temporarily unavailable at container start.
- **SC-002**: When the database host is unreachable, startup completes and the
  health endpoint responds within 60 seconds (not infinite hang).
- **SC-003**: When the database is healthy, deploy healthcheck passes on the
  first attempt in the same manner as the last known good deploy (c49010b era).
- **SC-004**: Zero regression in existing API endpoint behavior for opportunities,
  scoring, and scheduler when database is available (verified by smoke tests).
- **SC-005**: Operators can distinguish "app up, DB down" from "app not started"
  via health endpoint response after deploy.

## Assumptions

- Hosting platform (Railway) healthcheck hits `/api/health` on the bound HTTP port;
  the check fails if the port never opens or the request times out.
- Current healthcheck timeout is up to 300 seconds per `railway.toml`; the fix
  must succeed well inside that window under normal conditions.
- PostgreSQL remains the database; connection timeout is acceptable for Railway
  internal networking.
- Schema migrations (`create_all`, column migrations) remain necessary but may run
  after the HTTP server is live, provided endpoints degrade gracefully until complete.
- No change to migration content or schema design is required—only when and how
  migrations run relative to port binding.
- Single-container deploy model; no multi-instance migration coordination beyond
  existing `IF NOT EXISTS` idempotent migrations.

## Out of Scope

- Match scoring quality or deterministic scoring features (001/002)
- Scraper pipelines, architecture dashboard, construction dashboard frontend
- New API endpoints unrelated to health/startup observability
- Database infrastructure provisioning or Railway Postgres configuration changes
- Lazy-import refactors of pipeline modules (optional future optimization, not
  required for this feature)
