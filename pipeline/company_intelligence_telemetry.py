"""Shared ops_job_runs/ops_job_run_events telemetry for company-intelligence,
used identically by BOTH trigger paths:

  - the scheduled pipeline (pipeline/run.py::run_pipeline(), trigger=
    "scheduler") -- the only path that wrote here before this module
    existed (M3D-B).
  - the manual/n8n HTTP endpoint (pipeline/internal_steps.py::
    run_company_intelligence_step(), trigger="manual") -- previously had
    NO ops_job_runs telemetry at all; only the generic pipeline_runs
    tracker (pipeline/runs.py) saw these runs.

Both paths now call the SAME three functions below instead of each
maintaining its own copy, so a company-intelligence run's telemetry is
directly comparable across triggers (same job_type, same counts shape,
same event lifecycle) -- previously the manual path was invisible to any
query against ops_job_runs, which is exactly what made confirming or
ruling out cross-trigger overlap impossible during the "cursor already
closed" audit (see PR #158/#159/#160).

Correlation with pipeline_runs: the manual path passes its own
pipeline_runs.run_id in as `run_id` here (NOT auto-generated), and again
as `idempotency_key` -- so ops_job_runs.run_id == pipeline_runs.run_id
for that invocation, joinable directly with no time-window guessing, and
a second telemetry-start attempt for the same run_id is a safe no-op
(see start_company_intelligence_telemetry's docstring) rather than a
second ops_job_runs row. The scheduled path has no pipeline_runs row of
its own (it calls run_company_intelligence() directly, never through
pipeline.runs.execute_tracked_step/run_tracked_step) -- it continues to
pass run_id=None here and get a freshly generated UUID, exactly as
before this module existed.

This module changes no retry, session, or connection-pool behavior --
every function it calls (job_run_telemetry.call_with_telemetry_session)
already opens and closes its own short-lived session, entirely separate
from the caller's real-work session, unchanged.
"""

from __future__ import annotations

import logging
from typing import Any

from config.env import env_flag
from pipeline.job_run import (
    finish_job_run,
    heartbeat_job_run,
    record_job_step,
    start_job_run,
)
from pipeline.job_run_telemetry import call_with_telemetry_session

logger = logging.getLogger(__name__)

JOB_TYPE = "company_intelligence"
JOB_RUN_TELEMETRY_FLAG = "ENABLE_COMPANY_INTELLIGENCE_JOB_RUN_TELEMETRY"
_LOG_LABEL = "Company intelligence telemetry"


def company_intelligence_job_run_telemetry_enabled() -> bool:
    """Read-only feature-flag check, same "1"/"true"/"yes" convention as
    every other flag in this repo. False by default. One flag gates BOTH
    trigger paths -- there is no separate flag for the manual path."""
    return env_flag(JOB_RUN_TELEMETRY_FLAG, default=False)


# Flat int fields already present, unchanged, in run_company_intelligence()'s
# own return dict -- see pipeline/company_intelligence.py. Identical to the
# allowlist pipeline/run.py maintained before this module existed.
COUNT_KEYS = (
    "companies_populated",
    "companies_google_enriched",
    "companies_ai_analyzed",
    "companies_classified",
    "construction_tiers_updated",
)


def safe_company_intelligence_counts(result: dict[str, Any]) -> dict[str, int]:
    """Allowlisted counts only -- never a prompt, an AI response, or a
    company/tender record. None of those exist in
    run_company_intelligence()'s return value in the first place; this
    function only narrows further, and protects against the return dict
    growing an unexpected field in the future."""
    return {key: result[key] for key in COUNT_KEYS if key in result}


def start_company_intelligence_telemetry(
    *,
    trigger: str,
    run_id: str | None = None,
    source: str | None = "permits",
) -> str | None:
    """Starts an ops_job_runs row. Fail-open like every telemetry helper in
    this codebase: returns None (never raises) on ANY failure, including
    get_session() itself failing, an invalid trigger, or -- when `run_id`
    is given -- a genuine duplicate start for that run_id (the partial
    unique index on (job_type, idempotency_key) raises IntegrityError,
    which call_with_telemetry_session catches and turns into this
    function returning None; no second ops_job_runs row is ever created,
    and the failed INSERT is rolled back by the session's own close()).

    `run_id`, when given, becomes BOTH ops_job_runs.run_id and
    ops_job_runs.idempotency_key -- the correlation key back to whatever
    the caller's own tracking (e.g. pipeline_runs.run_id) already uses,
    and the guard against a duplicate start for that same logical run.
    When omitted (the scheduled-pipeline path, which has no run_id of its
    own to correlate against), start_job_run() generates a fresh UUID as
    before this module existed -- unchanged behavior for that caller.
    """

    def _do(session: object) -> str:
        return start_job_run(
            session,
            job_type=JOB_TYPE,
            trigger=trigger,
            source=source,
            idempotency_key=run_id,
            run_id=run_id,
        )

    return call_with_telemetry_session(
        _do,
        log_label=_LOG_LABEL,
        failure_message="failed to start job run tracking",
    )


def record_company_intelligence_phase(run_id: str, phase: str) -> None:
    """Records a step_completed event and heartbeats the run. Fail-open
    and silently a no-op if the run is no longer 'running' or its lease
    has expired (record_job_step()'s own contract) -- never raises,
    never affects the caller's actual work."""

    def _do(session: object) -> None:
        record_job_step(session, run_id, event_type="step_completed", step=phase)
        heartbeat_job_run(session, run_id)

    call_with_telemetry_session(
        _do,
        log_label=_LOG_LABEL,
        failure_message=f"failed to record phase={phase}",
    )


def finish_company_intelligence_telemetry(
    run_id: str,
    *,
    status: str,
    counts: dict[str, int] | None = None,
    raw_error: str | None = None,
) -> None:
    """Terminal transition. Fail-open, and a safe no-op (per
    finish_job_run()'s own contract) if the run is already terminal, or
    the run_id doesn't exist -- e.g. because
    start_company_intelligence_telemetry() itself failed open earlier and
    returned None, in which case callers should not (and, per this
    module's two call sites, do not) invoke this at all."""

    def _do(session: object) -> None:
        finish_job_run(
            session, run_id, status=status, counts=counts, raw_error=raw_error
        )

    call_with_telemetry_session(
        _do,
        log_label=_LOG_LABEL,
        failure_message="failed to finish job run tracking",
    )
