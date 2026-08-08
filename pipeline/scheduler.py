from __future__ import annotations

import logging
import os

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from config.env import env_flag
from pipeline.executor import start_pipeline_subprocess
from pipeline.job_run import (
    finish_job_run,
    heartbeat_job_run,
    record_job_step,
    start_job_run,
)
from pipeline.job_run_telemetry import call_with_telemetry_session
from pipeline.lock import is_pipeline_running
from pipeline.surrey_identity_scheduler import (
    SURREY_SCHEDULER_FLAG,
    map_surrey_result_to_job_run_outcome,
    run_surrey_identity_import_once,
    surrey_scheduler_enabled,
)

logger = logging.getLogger(__name__)
_scheduler: BackgroundScheduler | None = None

SURREY_JOB_ID = "surrey_identity_import"

# (M3C) Observation-only: persists Surrey identity scheduler run history
# to ops_job_runs/ops_job_run_events (pipeline/job_run.py, migration 033,
# already applied in production and still completely inert). Default
# false -- with the flag off, none of the _surrey_job_run_telemetry_*
# helpers below are ever called, so behavior is functionally identical to
# before this change: no extra get_session() calls, no ops_job_run*
# writes. Never uses an idempotency_key -- this is pure observation, not
# a change to the scheduler's own retry/dedup semantics.
SURREY_JOB_RUN_TELEMETRY_FLAG = "ENABLE_SURREY_JOB_RUN_TELEMETRY"
SURREY_JOB_RUN_JOB_TYPE = "surrey_identity_scheduler"


def surrey_job_run_telemetry_enabled() -> bool:
    """Read-only feature-flag check, same "1"/"true"/"yes" convention as
    every other flag in this repo. False by default."""
    return env_flag(SURREY_JOB_RUN_TELEMETRY_FLAG, default=False)


_SURREY_TELEMETRY_LOG_LABEL = "Surrey identity scheduler telemetry"


def _surrey_job_run_telemetry_start() -> str | None:
    """Best-effort start_job_run() call on its OWN session (never the
    Surrey import's own session/transaction), via the shared fail-open
    wrapper (pipeline/job_run_telemetry.py, M3D-A) -- returns None on ANY
    failure, including get_session() itself raising, which every other
    telemetry helper below treats as "telemetry unavailable for this
    run", not an error. Behavior and log text are unchanged from before
    this helper delegated to the shared wrapper (M3C)."""

    def _do(session: object) -> str:
        return start_job_run(
            session,
            job_type=SURREY_JOB_RUN_JOB_TYPE,
            trigger="scheduler",
            source="surrey",
        )

    return call_with_telemetry_session(
        _do,
        log_label=_SURREY_TELEMETRY_LOG_LABEL,
        failure_message="failed to start job run tracking",
    )


def _surrey_job_run_telemetry_phase(run_id: str, phase: str) -> None:
    """Best-effort milestone event + heartbeat for one plan/validate/apply
    boundary, on its own session. Never raises -- including if
    get_session() itself raises."""

    def _do(session: object) -> None:
        record_job_step(session, run_id, event_type="step_completed", step=phase)
        heartbeat_job_run(session, run_id)

    call_with_telemetry_session(
        _do,
        log_label=_SURREY_TELEMETRY_LOG_LABEL,
        failure_message=f"failed to record phase={phase}",
    )


def _surrey_job_run_telemetry_finish(
    run_id: str,
    *,
    status: str,
    counts: dict[str, int] | None = None,
    raw_error: str | None = None,
) -> None:
    """Best-effort finish_job_run() call on its own session. Never raises
    -- including if get_session() itself raises. raw_error, if given, is
    only ever handed to finish_job_run()'s own safe classifier
    (pipeline.error_classification.classify_run_error) -- never logged or
    stored verbatim here or anywhere else in this function."""

    def _do(session: object) -> None:
        finish_job_run(
            session, run_id, status=status, counts=counts, raw_error=raw_error
        )

    call_with_telemetry_session(
        _do,
        log_label=_SURREY_TELEMETRY_LOG_LABEL,
        failure_message="failed to finish job run tracking",
    )


def _scheduled_pipeline_run() -> None:
    if is_pipeline_running():
        logger.info("Skipping scheduled pipeline run: already in progress")
        return
    result = start_pipeline_subprocess()
    logger.info("Scheduled pipeline started: %s", result)


def _scheduled_surrey_identity_run() -> None:
    """Independent Surrey identity-aware import job. Fetches the full
    currently-published Surrey window (days=None -- no fixed lookback,
    so a run after downtime still reconciles the entire official source
    rather than losing rows older than some cutoff), then plans and
    applies it as one atomic, caller-owned transaction (see
    pipeline.surrey_identity_scheduler.run_surrey_identity_import_once).
    Never touches the generic importer
    (db.permit_import.upsert_city_permits) and never calls
    scrape_surrey_permits(persist=True) -- that is the pre-existing
    generic-importer path used by the unrelated manual scrape endpoint.

    (M3C) When ENABLE_SURREY_JOB_RUN_TELEMETRY is unset/false, every
    telemetry call below is skipped entirely -- this function is then
    functionally identical to before M3C. When enabled, telemetry is
    strictly observational: every _surrey_job_run_telemetry_* call is
    best-effort on its OWN session (never the Surrey import's session/
    transaction) and never raises, so a telemetry failure can never
    change whether this job's real scrape/plan/apply work succeeds,
    and the real work's own exception always still propagates exactly
    as it did before this change -- only str(exc) is additionally
    handed to finish_job_run() for safe classification, never logged or
    stored verbatim anywhere.
    """
    from db.connection import get_session
    from scraper.surrey_permits import iter_surrey_permits

    telemetry_run_id = (
        _surrey_job_run_telemetry_start()
        if surrey_job_run_telemetry_enabled()
        else None
    )

    try:
        rows = list(iter_surrey_permits(days=None))
        session = get_session()
    except Exception as exc:
        if telemetry_run_id is not None:
            _surrey_job_run_telemetry_finish(
                telemetry_run_id, status="failed", raw_error=str(exc)
            )
        raise

    try:
        try:
            if telemetry_run_id is not None:

                def on_phase(phase: str) -> None:
                    _surrey_job_run_telemetry_phase(telemetry_run_id, phase)

                result = run_surrey_identity_import_once(
                    session, rows=rows, on_phase=on_phase
                )
            else:
                # Flag off (or telemetry start failed): call with the exact
                # pre-M3C signature -- no on_phase kwarg at all -- so this
                # path is byte-for-byte the same call as before M3C.
                result = run_surrey_identity_import_once(session, rows=rows)
        except Exception as exc:
            if telemetry_run_id is not None:
                _surrey_job_run_telemetry_finish(
                    telemetry_run_id, status="failed", raw_error=str(exc)
                )
            raise

        logger.info("Surrey identity scheduler run: %s", result.as_dict())

        if telemetry_run_id is not None:
            status, counts = map_surrey_result_to_job_run_outcome(result)
            _surrey_job_run_telemetry_finish(
                telemetry_run_id, status=status, counts=counts
            )
    finally:
        session.close()


def scheduler_status() -> dict[str, str | int | bool | None]:
    enabled = os.getenv("SCHEDULER_ENABLED", "true").lower() in {"1", "true", "yes"}
    timezone = os.getenv("SCHEDULE_TIMEZONE", "America/Vancouver")
    hour = int(os.getenv("SCHEDULE_HOUR", "6"))
    minute = int(os.getenv("SCHEDULE_MINUTE", "0"))
    surrey_enabled = surrey_scheduler_enabled()
    surrey_hour = int(os.getenv("SURREY_SCHEDULE_HOUR", "5"))
    surrey_minute = int(os.getenv("SURREY_SCHEDULE_MINUTE", "30"))

    status: dict[str, str | int | bool | None] = {
        "enabled": enabled,
        "running": bool(_scheduler and _scheduler.running),
        "job_id": "daily_scrape_import",
        "timezone": timezone,
        "schedule": f"{hour:02d}:{minute:02d}",
        "next_run_at": None,
        "surrey_identity_scheduler_enabled": surrey_enabled,
        "surrey_identity_schedule": f"{surrey_hour:02d}:{surrey_minute:02d}",
        "surrey_identity_next_run_at": None,
    }

    if _scheduler and _scheduler.running:
        job = _scheduler.get_job("daily_scrape_import")
        if job and job.next_run_time:
            status["next_run_at"] = job.next_run_time.isoformat()

        surrey_job = _scheduler.get_job(SURREY_JOB_ID)
        if surrey_job and surrey_job.next_run_time:
            status["surrey_identity_next_run_at"] = surrey_job.next_run_time.isoformat()

    return status


def start_scheduler() -> BackgroundScheduler | None:
    global _scheduler

    if os.getenv("SCHEDULER_ENABLED", "true").lower() not in {"1", "true", "yes"}:
        logger.info("Scheduler disabled via SCHEDULER_ENABLED")
        return None

    if _scheduler and _scheduler.running:
        return _scheduler

    timezone = os.getenv("SCHEDULE_TIMEZONE", "America/Vancouver")
    hour = int(os.getenv("SCHEDULE_HOUR", "6"))
    minute = int(os.getenv("SCHEDULE_MINUTE", "0"))

    _scheduler = BackgroundScheduler(timezone=timezone)
    _scheduler.add_job(
        _scheduled_pipeline_run,
        trigger=CronTrigger(hour=hour, minute=minute, timezone=timezone),
        id="daily_scrape_import",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    if surrey_scheduler_enabled():
        surrey_hour = int(os.getenv("SURREY_SCHEDULE_HOUR", "5"))
        surrey_minute = int(os.getenv("SURREY_SCHEDULE_MINUTE", "30"))
        _scheduler.add_job(
            _scheduled_surrey_identity_run,
            trigger=CronTrigger(
                hour=surrey_hour, minute=surrey_minute, timezone=timezone
            ),
            id=SURREY_JOB_ID,
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        logger.info(
            "Surrey identity scheduler enabled: daily at %02d:%02d %s",
            surrey_hour,
            surrey_minute,
            timezone,
        )
    else:
        logger.info("Surrey identity scheduler disabled via %s", SURREY_SCHEDULER_FLAG)

    _scheduler.start()
    logger.info("Scheduler started: daily at %02d:%02d %s", hour, minute, timezone)
    return _scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")
    _scheduler = None
