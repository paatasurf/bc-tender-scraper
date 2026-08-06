"""Writer API for the ops job run schema (migration 033, M3B).

A single, small set of functions -- start_job_run, heartbeat_job_run,
record_job_step, finish_job_run -- meant to become the ONE writer path
future scheduler/manual/n8n callers all share, the same way
pipeline/runs.py::execute_tracked_step()/run_tracked_step() are already
the one writer path api/internal.py's ~15 endpoints and
pipeline/scheduler.py both use for pipeline_runs. M3B ships this module
with NO real caller wired to it yet -- see the M3A architecture audit for
the phased rollout (M3C instruments the Surrey identity scheduler first;
M3D instruments AI scoring / company intelligence / arch-company-
intelligence after that is verified).

Hard rules:
  - "stale" is never a status this module writes. status is always one of
    running/success/failed/partial_failure -- a stuck run whose
    lease_expires_at has passed is a READ-MODEL interpretation (future
    M3E work, mirroring pipeline/ops_read_model.py's
    get_coordinator_summary() treatment of pipeline_coordinator_runs),
    never something this writer mutates a row into.
  - heartbeat_job_run() touches ONLY heartbeat_at/lease_expires_at (and
    updated_at) on ops_job_runs -- it never inserts an ops_job_run_events
    row. Do not call it once per processed item in a tight loop (e.g. once
    per AI-scored tender) -- call it between batches/steps instead. Event
    rows are for started/step_started/step_completed/step_failed/finished
    milestones only.
  - A stale run (status='running' but lease_expires_at has already
    passed) can never be resurrected by a late heartbeat or step event --
    heartbeat_job_run() and record_job_step() both require
    lease_expires_at > now() in addition to status='running'. Only
    finish_job_run() is exempt from the lease check: the worker that
    started a run is still the authoritative source of its own outcome
    even if its lease technically lapsed moments before it reports in --
    it is a single-writer-per-run_id model (unlike
    pipeline_coordinator_runs, nothing else ever contends for the same
    run_id), so there is no "someone else already reclaimed this" case to
    guard against here the way there is for heartbeat/step events, which
    exist specifically to extend or narrate an in-progress claim on the
    run.
  - finish_job_run() and record_job_step() are both idempotent no-ops --
    never an error, never a duplicate event -- when the run is already
    terminal, already stale, or does not exist at all. Both rely on the
    UPDATE/SELECT's own row match (rowcount, or a locked row read)
    to decide whether to write an event, rather than a separate
    check-then-act query pair that could race with a concurrent writer.
  - Raw exception text is never stored. finish_job_run()'s raw_error
    parameter is classified through
    pipeline.error_classification.classify_run_error() and discarded --
    only error_present (bool) and error_summary (one of a fixed label
    set) are ever persisted to ops_job_runs.error_summary.
  - counts/counts_delta payloads are validated (validate_counts()) before
    being written: must be a flat JSON object of int/float/bool/None
    values only -- never a string (which could carry a stack trace or a
    secret fragment), and never a nested object or array.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from db.ops_job_run_tables import ops_job_run_events, ops_job_runs
from pipeline.error_classification import classify_run_error

__all__ = [
    "DEFAULT_LEASE_TTL",
    "InvalidCountsError",
    "validate_counts",
    "start_job_run",
    "heartbeat_job_run",
    "record_job_step",
    "finish_job_run",
]

DEFAULT_LEASE_TTL = timedelta(minutes=30)

_VALID_TRIGGERS = frozenset({"scheduler", "manual", "n8n"})
_TERMINAL_STATUSES = frozenset({"success", "failed", "partial_failure"})

# The complete event_type enum ck_ops_job_run_events_event_type allows at
# the database level. "started" and "finished" are reserved -- only
# start_job_run() and finish_job_run() ever write them, each exactly
# once per run. record_job_step() accepts only the narrower
# _VALID_STEP_EVENT_TYPES below, so a future caller can never create a
# second "started"/"finished" row and corrupt a run's timeline.
_ALL_EVENT_TYPES = frozenset(
    {"started", "step_started", "step_completed", "step_failed", "finished"}
)
_VALID_STEP_EVENT_TYPES = frozenset({"step_started", "step_completed", "step_failed"})


class InvalidCountsError(ValueError):
    """Raised when a counts/counts_delta payload isn't a flat JSON object
    of int/float/bool/None values. The rejected value's own content is
    never included in the exception message beyond its Python type name
    -- a rejected string could itself be a stack trace or secret
    fragment, and this exception's text can end up in logs."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def validate_counts(counts: Any) -> dict[str, Any]:
    """Validate a counts/counts_delta payload before it is ever written.

    Must be a dict; every value must be int, float, bool, or None --
    never str, list, dict, or any other type. Returns the same dict
    unchanged on success (never mutates it). Raises InvalidCountsError
    otherwise, without echoing the rejected value itself.
    """
    if not isinstance(counts, dict):
        raise InvalidCountsError(
            f"counts must be a JSON object (dict), got {type(counts).__name__}"
        )
    for key, value in counts.items():
        if not isinstance(key, str):
            raise InvalidCountsError(
                f"counts keys must be strings, got {type(key).__name__}"
            )
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            continue
        if value is None:
            continue
        raise InvalidCountsError(
            "counts values must be numbers, booleans, or null; "
            f"got {type(value).__name__}"
        )
    return counts


def start_job_run(
    session: Session,
    *,
    job_type: str,
    trigger: str,
    source: str | None = None,
    idempotency_key: str | None = None,
    counts: dict[str, Any] | None = None,
    lease_ttl: timedelta = DEFAULT_LEASE_TTL,
    run_id: str | None = None,
) -> str:
    """Create a new ops_job_runs row (status='running') and record its
    'started' event, in one transaction. Returns the run_id (a fresh
    UUID4 if not supplied).

    trigger must be one of "scheduler" | "manual" | "n8n". A duplicate
    (job_type, idempotency_key) raises sqlalchemy.exc.IntegrityError from
    the database's partial unique index -- this function does not catch
    or reinterpret that; M3B ships no caller yet, so no idempotent-retry
    policy has been decided (a real M3C/M3D caller should decide
    deliberately whether a conflict means "already started, treat as a
    no-op" or something else, not have that guessed here).
    """
    if trigger not in _VALID_TRIGGERS:
        raise ValueError(
            f"trigger must be one of {sorted(_VALID_TRIGGERS)}, got {trigger!r}"
        )

    validated_counts = validate_counts(counts) if counts is not None else {}
    actual_run_id = run_id or str(uuid.uuid4())
    now = _utc_now()

    session.execute(
        ops_job_runs.insert().values(
            run_id=actual_run_id,
            job_type=job_type,
            source=source,
            trigger=trigger,
            status="running",
            started_at=now,
            heartbeat_at=now,
            finished_at=None,
            lease_expires_at=now + lease_ttl,
            counts=validated_counts,
            error_present=False,
            error_summary=None,
            idempotency_key=idempotency_key,
            created_at=now,
            updated_at=now,
        )
    )
    session.execute(
        ops_job_run_events.insert().values(
            run_id=actual_run_id,
            event_type="started",
            step=None,
            counts_delta=None,
            occurred_at=now,
        )
    )
    session.commit()
    return actual_run_id


def heartbeat_job_run(
    session: Session,
    run_id: str,
    *,
    lease_ttl: timedelta = DEFAULT_LEASE_TTL,
) -> None:
    """Extend the lease: updates ONLY heartbeat_at, lease_expires_at, and
    updated_at. Never inserts an ops_job_run_events row -- call this
    between batches/steps of work, not once per processed item (see the
    module docstring).

    Scoped to status='running' AND lease_expires_at > now() -- a silent
    no-op otherwise. This is deliberately stricter than just checking
    status: a run whose lease has already expired is exactly the "stale"
    case (see the module docstring) -- a heartbeat arriving after that
    point must never revive it by pushing lease_expires_at back into the
    future, since something else may already be treating it as
    reclaimable.
    """
    now = _utc_now()
    session.execute(
        update(ops_job_runs)
        .where(
            ops_job_runs.c.run_id == run_id,
            ops_job_runs.c.status == "running",
            ops_job_runs.c.lease_expires_at > now,
        )
        .values(heartbeat_at=now, lease_expires_at=now + lease_ttl, updated_at=now)
    )
    session.commit()


def record_job_step(
    session: Session,
    run_id: str,
    *,
    event_type: str,
    step: str | None = None,
    counts_delta: dict[str, Any] | None = None,
) -> None:
    """Append one ops_job_run_events row for a mid-run milestone -- NOT a
    heartbeat, and NOT "started"/"finished" (those are reserved for
    start_job_run()/finish_job_run() -- see _ALL_EVENT_TYPES's comment).
    event_type must be one of step_started/step_completed/step_failed.
    counts_delta, if given, is validated the same way as
    ops_job_runs.counts (validate_counts()).

    A short row-locking transaction (SELECT ... FOR UPDATE, then the
    INSERT, then commit -- never held across the caller's actual work)
    confirms the run is still status='running' AND lease_expires_at >
    now() before writing the event; otherwise this is a silent no-op --
    never an error, and never an event row for a terminal, stale, or
    nonexistent run_id. The row lock (rather than a plain SELECT) is what
    makes this safe against a concurrent finish_job_run() call on the
    same run_id: whichever of the two commits first determines what the
    other one sees.
    """
    if event_type not in _VALID_STEP_EVENT_TYPES:
        raise ValueError(
            f"event_type must be one of {sorted(_VALID_STEP_EVENT_TYPES)}, "
            f"got {event_type!r} -- 'started'/'finished' are reserved for "
            "start_job_run()/finish_job_run()"
        )
    validated_delta = (
        validate_counts(counts_delta) if counts_delta is not None else None
    )

    now = _utc_now()
    locked = session.execute(
        select(ops_job_runs.c.status, ops_job_runs.c.lease_expires_at)
        .where(ops_job_runs.c.run_id == run_id)
        .with_for_update()
    ).first()

    if locked is None or locked.status != "running" or locked.lease_expires_at <= now:
        session.commit()  # release the row lock (if any); nothing to write
        return

    session.execute(
        ops_job_run_events.insert().values(
            run_id=run_id,
            event_type=event_type,
            step=step,
            counts_delta=validated_delta,
            occurred_at=now,
        )
    )
    session.commit()


def finish_job_run(
    session: Session,
    run_id: str,
    *,
    status: str,
    counts: dict[str, Any] | None = None,
    raw_error: str | None = None,
) -> None:
    """Terminal transition to success/failed/partial_failure (never
    "running", and never "stale" -- see the module docstring). Only
    mutates a row currently in status='running' (a stale-but-still-
    'running' row is a valid target -- see the module docstring on why
    finish is exempt from the lease check heartbeat/step events enforce).
    Calling this twice, on an already-terminal run, or on a run_id that
    does not exist, is a silent no-op -- no error, and no duplicate/
    orphaned 'finished' event -- decided by the UPDATE's own rowcount
    rather than a separate check-then-act query.

    raw_error, if given, is classified through
    pipeline.error_classification.classify_run_error() and discarded --
    the original text never reaches this table or any return value here.
    """
    if status not in _TERMINAL_STATUSES:
        raise ValueError(
            f"status must be one of {sorted(_TERMINAL_STATUSES)}, got {status!r} "
            "(this function only records terminal outcomes)"
        )

    validated_counts = validate_counts(counts) if counts is not None else {}
    error_present, error_summary = classify_run_error(raw_error)
    now = _utc_now()

    result = session.execute(
        update(ops_job_runs)
        .where(ops_job_runs.c.run_id == run_id, ops_job_runs.c.status == "running")
        .values(
            status=status,
            finished_at=now,
            counts=validated_counts,
            error_present=error_present,
            error_summary=error_summary,
            updated_at=now,
        )
    )

    if result.rowcount == 1:
        session.execute(
            ops_job_run_events.insert().values(
                run_id=run_id,
                event_type="finished",
                step=None,
                counts_delta=None,
                occurred_at=now,
            )
        )
    session.commit()
