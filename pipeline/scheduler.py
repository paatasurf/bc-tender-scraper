from __future__ import annotations

import logging
import os

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from pipeline.executor import start_pipeline_subprocess
from pipeline.lock import is_pipeline_running

logger = logging.getLogger(__name__)
_scheduler: BackgroundScheduler | None = None


def _scheduled_pipeline_run() -> None:
    if is_pipeline_running():
        logger.info("Skipping scheduled pipeline run: already in progress")
        return
    result = start_pipeline_subprocess()
    logger.info("Scheduled pipeline started: %s", result)


def scheduler_status() -> dict[str, str | int | bool | None]:
    enabled = os.getenv("SCHEDULER_ENABLED", "true").lower() in {"1", "true", "yes"}
    timezone = os.getenv("SCHEDULE_TIMEZONE", "America/Vancouver")
    hour = int(os.getenv("SCHEDULE_HOUR", "6"))
    minute = int(os.getenv("SCHEDULE_MINUTE", "0"))

    status: dict[str, str | int | bool | None] = {
        "enabled": enabled,
        "running": bool(_scheduler and _scheduler.running),
        "job_id": "daily_scrape_import",
        "timezone": timezone,
        "schedule": f"{hour:02d}:{minute:02d}",
        "next_run_at": None,
    }

    if _scheduler and _scheduler.running:
        job = _scheduler.get_job("daily_scrape_import")
        if job and job.next_run_time:
            status["next_run_at"] = job.next_run_time.isoformat()

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
    _scheduler.start()
    logger.info("Scheduler started: daily at %02d:%02d %s", hour, minute, timezone)
    return _scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")
    _scheduler = None
