from __future__ import annotations

import logging
import os

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from pipeline.executor import start_pipeline_subprocess
from pipeline.lock import is_pipeline_running
from pipeline.surrey_identity_scheduler import (
    SURREY_SCHEDULER_FLAG,
    run_surrey_identity_import_once,
    surrey_scheduler_enabled,
)

logger = logging.getLogger(__name__)
_scheduler: BackgroundScheduler | None = None

SURREY_JOB_ID = "surrey_identity_import"


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
    generic-importer path used by the unrelated manual scrape endpoint."""
    from db.connection import get_session
    from scraper.surrey_permits import iter_surrey_permits

    rows = list(iter_surrey_permits(days=None))
    session = get_session()
    try:
        result = run_surrey_identity_import_once(session, rows=rows)
        logger.info("Surrey identity scheduler run: %s", result.as_dict())
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
