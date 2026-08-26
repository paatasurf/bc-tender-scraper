"""Read-only data-shaping logic for the M3E-A ops jobs API (api/ops.py's
/api/ops/jobs and /api/ops/jobs/{run_id} routes).

No FastAPI imports here on purpose -- mirrors pipeline/ops_read_model.py's
separation of pure/DB-access logic from the routing layer, so normalization
rules stay unit-testable without spinning up routes or a real database.

Reads ops_job_runs / ops_job_run_events (migration 033, M3B schema;
pipeline/job_run.py is the only writer). This module never writes,
migrates, or calls init_db() -- it is a pure reader, generic across every
job_type, even though the only real instrumented writer as of M3E-A is the
Surrey identity scheduler (pipeline/surrey_identity_scheduler.py, gated by
ENABLE_SURREY_JOB_RUN_TELEMETRY -- see pipeline/scheduler.py).

Hard rules:
  - "active" vs "stale" is a READ-MODEL interpretation of status='running',
    never a stored value (see pipeline/job_run.py's module docstring):
    active = status='running' AND lease_expires_at > now();
    stale  = status='running' AND lease_expires_at <= now().
    success/failed/partial_failure pass through unchanged -- they are
    already unambiguous terminal outcomes.
  - ops_job_runs.error_summary (a fixed label, never raw text -- see
    pipeline/error_classification.py) and error_present are the only error
    signal ever returned. Nothing here ever reads or forwards a raw
    exception string, and ops_job_runs has no column that could carry one.
  - counts / counts_delta are returned exactly as stored: pipeline/job_run.py
    already enforces (validate_counts()) that every value written is a
    flat int/float/bool/None -- never a string, nested object, or array --
    so nothing here needs to re-sanitize them, only pass them through.
  - Every DB-touching function degrades to False/None/empty instead of
    raising when the ops_job_run schema doesn't exist yet (a fresh
    environment migration 033 hasn't been applied to) or the query fails
    for any other reason -- callers (api/ops.py) must never let this
    module's failures become an unhandled 500.
  - pipeline_runs (the legacy, unleased table pipeline/ops_read_model.py
    already reads) is a completely different table and is never read here
    -- this module only ever touches ops_job_runs / ops_job_run_events.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from db.ops_job_run_tables import ops_job_run_events, ops_job_runs

_SCHEMA_CHECK_SQL = text(
    "SELECT to_regclass('public.ops_job_runs') IS NOT NULL "
    "AND to_regclass('public.ops_job_run_events') IS NOT NULL"
)

# What GET /api/ops/jobs?status= accepts. "active"/"stale" are computed
# (see normalize_ops_job_run_status()); the other three are the raw
# ops_job_runs.status DB values, passed straight through.
VALID_JOB_STATUS_FILTERS = frozenset(
    {"active", "stale", "success", "failed", "partial_failure"}
)

LIST_DEFAULT_LIMIT = 50
LIST_MAX_LIMIT = 100


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def ops_job_run_schema_available(session: Session) -> bool:
    """True only if BOTH ops_job_runs and ops_job_run_events exist. Never
    raises -- a missing schema (migration 033 not applied on this
    environment) or any query failure both report False, exactly like
    pipeline/ops_read_model.py::coordinator_schema_available()."""
    try:
        return bool(session.execute(_SCHEMA_CHECK_SQL).scalar_one())
    except Exception:
        return False


def normalize_ops_job_run_status(*, status: str, lease_expires_at: datetime) -> str:
    """Map a raw ops_job_runs.status value (plus its lease) to the
    contract's normalized_status.

    status='running': "active" if the lease has not expired, else "stale"
    -- a stale run is NEVER reported as active, no matter how recently it
    heartbeat before the lease lapsed (see pipeline/job_run.py's module
    docstring: a stale run cannot be resurrected by a late heartbeat).
    success/failed/partial_failure: passed through unchanged -- the DB
    CHECK constraint (ck_ops_job_runs_status) guarantees status is always
    one of these 4 values, so there is no other case, but an unrecognized
    value still falls through to "unknown" rather than raising, matching
    every other normalizer in this codebase.
    """
    if status == "running":
        now = _utc_now()
        expires = lease_expires_at
        if expires is not None and expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        return "active" if expires is not None and expires > now else "stale"
    if status in ("success", "failed", "partial_failure"):
        return status
    return "unknown"


def build_job_run_payload(row: Any) -> dict[str, Any]:
    """Shape one ops_job_runs row into the safe API payload. `row` needs
    run_id/job_type/source/trigger/status/started_at/heartbeat_at/
    finished_at/lease_expires_at/counts/error_present/error_summary --
    a SQLAlchemy RowMapping (session.execute(select(ops_job_runs)).mappings())
    satisfies this, and so does any plain mapping with the same keys,
    keeping this unit-testable with a lightweight stand-in.

    Deliberately excludes: id (internal surrogate PK), idempotency_key
    (M3C never sets one, and it is otherwise an internal dedup detail, not
    an observability signal), created_at/updated_at (redundant with
    started_at/heartbeat_at/finished_at for this read model). Never
    includes plan_digest/result_digest/raw error text/permit or company
    data -- none of those are columns on this table at all (see
    db/ops_job_run_tables.py); this function only ever echoes what
    pipeline/job_run.py already validated before writing.
    """
    normalized_status = normalize_ops_job_run_status(
        status=row["status"], lease_expires_at=row["lease_expires_at"]
    )
    return {
        "run_id": row["run_id"],
        "job_type": row["job_type"],
        "source": row["source"],
        "trigger": row["trigger"],
        "normalized_status": normalized_status,
        "started_at": _iso(row["started_at"]),
        "heartbeat_at": _iso(row["heartbeat_at"]),
        "finished_at": _iso(row["finished_at"]),
        "lease_expires_at": _iso(row["lease_expires_at"]),
        "counts": row["counts"] or {},
        "error_present": row["error_present"],
        "error_summary": row["error_summary"],
    }


def build_job_event_payload(row: Any) -> dict[str, Any]:
    """Shape one ops_job_run_events row. event_type/step/counts_delta are
    stored exactly as pipeline/job_run.py wrote them (already-fixed enum
    label, optional step name, optional flat-int counts_delta) -- nothing
    to redact here."""
    return {
        "event_type": row["event_type"],
        "step": row["step"],
        "counts_delta": row["counts_delta"],
        "occurred_at": _iso(row["occurred_at"]),
    }


def list_ops_job_runs(
    session: Session,
    *,
    job_type: str | None,
    status: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    """List ops_job_runs rows, newest first (started_at desc, id desc as a
    stable tie-break). Unlike pipeline_runs (which needs a Python-side
    normalize-then-filter pass because "active" depends on a separate
    coordinator table), every input to normalize_ops_job_run_status() --
    status and lease_expires_at -- already lives on this row, so the
    active/stale split can be pushed straight into SQL alongside the
    job_type filter, no overfetch-and-truncate needed.

    Callers are responsible for validating `status` against
    VALID_JOB_STATUS_FILTERS before calling this (see api/ops.py) --
    this function trusts its inputs and applies no filter at all for a
    status value it doesn't recognize (never raises).
    """
    query = select(ops_job_runs)
    if job_type:
        query = query.where(ops_job_runs.c.job_type == job_type)

    if status == "active":
        query = query.where(
            ops_job_runs.c.status == "running",
            ops_job_runs.c.lease_expires_at > _utc_now(),
        )
    elif status == "stale":
        query = query.where(
            ops_job_runs.c.status == "running",
            ops_job_runs.c.lease_expires_at <= _utc_now(),
        )
    elif status in ("success", "failed", "partial_failure"):
        query = query.where(ops_job_runs.c.status == status)

    query = query.order_by(
        ops_job_runs.c.started_at.desc(), ops_job_runs.c.id.desc()
    ).limit(limit)

    rows = session.execute(query).mappings().all()
    return [build_job_run_payload(r) for r in rows]


def get_ops_job_run_detail(session: Session, run_id: str) -> dict[str, Any] | None:
    """GET /api/ops/jobs/{run_id}. Returns None (caller maps to 404) if no
    ops_job_runs row matches run_id -- never raises. Events are returned
    oldest-first (occurred_at asc, id asc) -- an append-only timeline in
    the order they actually happened, matching how
    pipeline/surrey_identity_scheduler.py's plan/validate/apply boundaries
    and pipeline/job_run.py's started/finished milestones were recorded.
    """
    run_row = (
        session.execute(select(ops_job_runs).where(ops_job_runs.c.run_id == run_id))
        .mappings()
        .first()
    )
    if run_row is None:
        return None

    events_query = (
        select(ops_job_run_events)
        .where(ops_job_run_events.c.run_id == run_id)
        .order_by(ops_job_run_events.c.occurred_at.asc(), ops_job_run_events.c.id.asc())
    )
    events = session.execute(events_query).mappings().all()

    payload = build_job_run_payload(run_row)
    payload["events"] = [build_job_event_payload(e) for e in events]
    return payload


def get_job_type_summary(session: Session, job_type: str) -> dict[str, Any]:
    """The `job_types.<job_type>` block of GET /api/ops/summary -- the
    single most recent ops_job_runs row for this job_type, or an honest
    all-null shape if none exists yet (never a fabricated "healthy").
    last_status is normalized the same way as everywhere else in this
    module (active/stale, not raw 'running'), so a still-in-progress run
    can never be mistaken for success. Assumes the schema exists --
    callers must check ops_job_run_schema_available() first (see
    api/ops.py), same division of responsibility as
    coordinator_schema_available()/get_coordinator_summary() in
    pipeline/ops_read_model.py.
    """
    row = (
        session.execute(
            select(ops_job_runs)
            .where(ops_job_runs.c.job_type == job_type)
            .order_by(ops_job_runs.c.started_at.desc(), ops_job_runs.c.id.desc())
            .limit(1)
        )
        .mappings()
        .first()
    )
    if row is None:
        return {"last_run_at": None, "last_status": None, "counts": None}

    return {
        "last_run_at": _iso(row["started_at"]),
        "last_status": normalize_ops_job_run_status(
            status=row["status"], lease_expires_at=row["lease_expires_at"]
        ),
        "counts": row["counts"] or {},
    }


def surrey_identity_scheduler_telemetry_capability(
    *, telemetry_enabled: bool, schema_available: bool
) -> dict[str, Any]:
    """The `capabilities.surrey_identity_scheduler_telemetry` block of GET
    /api/ops/summary. Replaces the old M2B static
    `{"available": False, "reason": "run_history_not_persisted"}` constant
    (pipeline/ops_read_model.py, pre-M3C) -- that was accurate before the
    Surrey identity scheduler had any writer at all, but M3C instrumented
    it, so a fixed `available=False` is now simply wrong and would show
    Mission Control an honest-looking "Not yet instrumented" capability
    for a feature that is, in fact, instrumented and (once the flag is on
    and the schema is applied) actually recording runs.

    `telemetry_enabled` must come from the one existing, already-audited,
    non-secret helper for this flag --
    pipeline.scheduler.surrey_job_run_telemetry_enabled() -- never a raw
    os.environ read here or in any caller. No other environment variable
    is read or exposed by this function.

    Three states only, in priority order:
      - flag off:                    {"available": False, "reason": "telemetry_disabled"}
      - flag on, schema unavailable: {"available": False, "reason": "schema_unavailable"}
      - flag on, schema available:   {"available": True,  "reason": None}
    "schema unavailable" also covers "couldn't check" (e.g. the database
    itself is down) -- callers pass schema_available=False for that case
    too (see api/ops.py's _load_ops_job_run_read_model()), which is the
    conservative, honest choice: this function never reports
    available=True on anything less than a confirmed schema check.
    """
    if not telemetry_enabled:
        return {"available": False, "reason": "telemetry_disabled"}
    if not schema_available:
        return {"available": False, "reason": "schema_unavailable"}
    return {"available": True, "reason": None}
