"""Tests for the Surrey identity-aware job registration in
pipeline.scheduler (PR-EN1G-1). Never starts a real APScheduler
background thread against production; jobs are inspected via the
BackgroundScheduler instance and immediately shut down."""

from __future__ import annotations

import pipeline.scheduler as scheduler_module


def _shutdown(instance):
    if instance is not None:
        instance.shutdown(wait=False)
    scheduler_module._scheduler = None


# --- default disabled: nothing Surrey-related runs ----------------------


def test_surrey_job_not_registered_when_flag_unset(monkeypatch):
    monkeypatch.delenv("ENABLE_SURREY_PERMITS_SCHEDULER", raising=False)
    monkeypatch.setenv("SCHEDULER_ENABLED", "true")
    scheduler_module._scheduler = None

    instance = scheduler_module.start_scheduler()
    try:
        assert instance is not None
        assert instance.get_job(scheduler_module.SURREY_JOB_ID) is None
    finally:
        _shutdown(instance)


def test_surrey_job_not_registered_when_flag_explicitly_false(monkeypatch):
    monkeypatch.setenv("ENABLE_SURREY_PERMITS_SCHEDULER", "false")
    monkeypatch.setenv("SCHEDULER_ENABLED", "true")
    scheduler_module._scheduler = None

    instance = scheduler_module.start_scheduler()
    try:
        assert instance.get_job(scheduler_module.SURREY_JOB_ID) is None
    finally:
        _shutdown(instance)


def test_scheduler_status_reports_surrey_disabled_by_default(monkeypatch):
    monkeypatch.delenv("ENABLE_SURREY_PERMITS_SCHEDULER", raising=False)
    scheduler_module._scheduler = None
    status = scheduler_module.scheduler_status()
    assert status["surrey_identity_scheduler_enabled"] is False
    assert status["surrey_identity_next_run_at"] is None


# --- other scheduled sources unchanged ----------------------------------


def test_main_daily_job_registered_identically_regardless_of_surrey_flag(monkeypatch):
    monkeypatch.setenv("SCHEDULER_ENABLED", "true")
    monkeypatch.setenv("SCHEDULE_HOUR", "6")
    monkeypatch.setenv("SCHEDULE_MINUTE", "0")

    monkeypatch.delenv("ENABLE_SURREY_PERMITS_SCHEDULER", raising=False)
    scheduler_module._scheduler = None
    disabled_instance = scheduler_module.start_scheduler()
    disabled_job = disabled_instance.get_job("daily_scrape_import")
    disabled_trigger = str(disabled_job.trigger)
    _shutdown(disabled_instance)

    monkeypatch.setenv("ENABLE_SURREY_PERMITS_SCHEDULER", "true")
    scheduler_module._scheduler = None
    enabled_instance = scheduler_module.start_scheduler()
    enabled_job = enabled_instance.get_job("daily_scrape_import")
    enabled_trigger = str(enabled_job.trigger)
    _shutdown(enabled_instance)

    assert disabled_job is not None
    assert enabled_job is not None
    assert disabled_trigger == enabled_trigger
    assert disabled_job.func is scheduler_module._scheduled_pipeline_run
    assert enabled_job.func is scheduler_module._scheduled_pipeline_run


def test_scheduler_disabled_master_switch_prevents_both_jobs(monkeypatch):
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")
    monkeypatch.setenv("ENABLE_SURREY_PERMITS_SCHEDULER", "true")
    scheduler_module._scheduler = None

    instance = scheduler_module.start_scheduler()
    assert instance is None
    scheduler_module._scheduler = None


# --- enabled path: Surrey job registered with the identity-aware job ----


def test_surrey_job_registered_when_flag_enabled(monkeypatch):
    monkeypatch.setenv("ENABLE_SURREY_PERMITS_SCHEDULER", "true")
    monkeypatch.setenv("SCHEDULER_ENABLED", "true")
    monkeypatch.setenv("SURREY_SCHEDULE_HOUR", "5")
    monkeypatch.setenv("SURREY_SCHEDULE_MINUTE", "30")
    scheduler_module._scheduler = None

    instance = scheduler_module.start_scheduler()
    try:
        job = instance.get_job(scheduler_module.SURREY_JOB_ID)
        assert job is not None
        assert job.func is scheduler_module._scheduled_surrey_identity_run
        assert job.max_instances == 1
    finally:
        _shutdown(instance)


def test_scheduler_status_reports_surrey_enabled_and_schedule(monkeypatch):
    monkeypatch.setenv("ENABLE_SURREY_PERMITS_SCHEDULER", "true")
    monkeypatch.setenv("SURREY_SCHEDULE_HOUR", "5")
    monkeypatch.setenv("SURREY_SCHEDULE_MINUTE", "30")
    scheduler_module._scheduler = None
    status = scheduler_module.scheduler_status()
    assert status["surrey_identity_scheduler_enabled"] is True
    assert status["surrey_identity_schedule"] == "05:30"


# --- _scheduled_surrey_identity_run: only the identity-aware adapter ----


class _FakeSession:
    def __init__(self):
        self.closed = False
        self.commits = 0
        self.rollbacks = 0

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


def test_scheduled_run_uses_only_the_identity_aware_adapter(monkeypatch):
    """Patch db.permit_import.upsert_city_permits to explode if called at
    all -- the scheduled Surrey job must never reach it."""
    session = _FakeSession()
    captured = {}

    def exploding_generic_import(*_args, **_kwargs):
        raise AssertionError(
            "generic importer (upsert_city_permits) must never be called"
        )

    monkeypatch.setattr(
        "db.permit_import.upsert_city_permits", exploding_generic_import
    )

    def fake_iter_surrey_permits(*, days):
        captured["days"] = days
        return iter(
            [
                {"external_id": "26-000001-001-00/AB", "applicant": "X"},
                {"external_id": "26-000002-001-00/CD", "applicant": "Y"},
            ]
        )

    monkeypatch.setattr(
        "scraper.surrey_permits.iter_surrey_permits", fake_iter_surrey_permits
    )
    monkeypatch.setattr("db.connection.get_session", lambda: session)

    def fake_run_once(_session, *, rows):
        captured["rows"] = rows
        from pipeline.surrey_identity_scheduler import SurreyIdentitySchedulerResult

        return SurreyIdentitySchedulerResult(
            source_rows=len(rows),
            updated=2,
            inserted=0,
            errors=0,
            plan_digest="a" * 64,
            result_digest="b" * 64,
        )

    monkeypatch.setattr(
        scheduler_module, "run_surrey_identity_import_once", fake_run_once
    )

    scheduler_module._scheduled_surrey_identity_run()

    assert len(captured["rows"]) == 2
    assert captured["days"] is None  # full window, no fixed lookback
    assert session.closed is True


def test_scheduled_run_fetches_the_full_surrey_window_not_a_fixed_lookback():
    """After downtime, the scheduler must reconcile the entire currently
    published Surrey source, not just the last N days -- no fixed
    lookback constant should exist on the module at all."""
    import inspect

    assert not hasattr(scheduler_module, "SURREY_LOOKBACK_DAYS")
    source = inspect.getsource(scheduler_module._scheduled_surrey_identity_run)
    assert "iter_surrey_permits(days=None)" in source


def test_scheduled_run_source_has_no_reference_to_scrape_persist_true():
    """The scheduled job must fetch raw rows (iter_surrey_permits), never
    call scrape_surrey_permits(persist=True) -- that is the pre-existing
    generic-importer path used by the unrelated manual scrape endpoint.
    Only the function's own docstring mentions the forbidden calls, by
    name, to document that they are deliberately never used."""
    import inspect
    import re

    source = inspect.getsource(scheduler_module._scheduled_surrey_identity_run)
    code_only = re.sub(r'""".*?"""', "", source, flags=re.DOTALL)
    assert "iter_surrey_permits" in code_only
    assert "scrape_surrey_permits" not in code_only
    assert "persist=True" not in code_only
    assert "upsert_city_permits" not in code_only
