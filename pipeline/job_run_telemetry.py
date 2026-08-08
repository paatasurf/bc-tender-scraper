"""Shared fail-open session wrapper for writing to ops_job_runs /
ops_job_run_events (migration 033, pipeline/job_run.py) from any scheduled
job's own telemetry helpers.

Generalized (M3D-A) from the fail-open pattern pipeline/scheduler.py
originally wrote bespoke for the Surrey identity scheduler (M3C) --
Surrey's own _surrey_job_run_telemetry_start/_phase/_finish now delegate
to call_with_telemetry_session() below instead of duplicating this
try/except/finally shape, but every writer call (start_job_run,
record_job_step, heartbeat_job_run, finish_job_run) is still invoked from
inside the CALLER's own module (via the `fn` closure passed in), never
from here directly -- this module only owns the session lifecycle and the
fail-open swallow, never the specific ops_job_run write itself. That is
deliberate: it keeps each caller's own writer-function names patchable at
the caller's own module (e.g. `pipeline.scheduler.start_job_run`) exactly
as before this module existed, so Surrey's existing tests
(tests/unit/test_surrey_job_run_telemetry.py) needed zero changes to keep
proving Surrey's behavior is unchanged.

Guarantees, identical to the original Surrey-only implementation:
  - Acquires its OWN session (get_session() inside try) -- never the
    caller's real-work session/transaction, so a telemetry write can never
    prematurely commit or roll back the actual job's own in-progress work.
  - Never raises, including when get_session() itself raises.
  - Closes the session in a finally, only if one was actually acquired.
  - Logs a single fixed "{log_label}: {failure_message}" warning on any
    failure -- never the underlying exception's text, a prompt, an AI
    response, or any record-level data.
"""

from __future__ import annotations

import logging
from typing import Callable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


def close_telemetry_session(session: object | None) -> None:
    """Close a telemetry session if (and only if) one was actually
    acquired -- never raises itself, so a close() failure can't mask
    whatever the caller already decided to do."""
    if session is not None:
        try:
            session.close()
        except Exception:
            pass


def call_with_telemetry_session(
    fn: Callable[[object], T],
    *,
    log_label: str,
    failure_message: str,
) -> T | None:
    """Acquire a fresh session, call fn(session), close it in finally.
    Returns fn(session)'s result on success, or None on ANY failure
    (including get_session() itself raising) -- this is the single
    fail-open boundary every job-type-specific telemetry helper should be
    built on top of, so a telemetry outage can never affect the real
    job's own outcome.
    """
    from db.connection import get_session

    session = None
    try:
        session = get_session()
        return fn(session)
    except Exception:
        logger.warning("%s: %s", log_label, failure_message)
        return None
    finally:
        close_telemetry_session(session)
