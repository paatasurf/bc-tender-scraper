from __future__ import annotations

import logging
import os

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from pipeline.run import run_pipeline

logger = logging.getLogger(__name__)
_scheduler: BackgroundScheduler | None = None


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
        run_pipeline,
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
