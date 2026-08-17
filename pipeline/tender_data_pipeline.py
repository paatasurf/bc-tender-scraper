from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from config.env import env_flag
from db.connection import get_session, init_db
from db.import_contract_awards import import_contract_awards
from db.import_csv import import_all_csvs
from pipeline.csv_verify import verify_tender_csvs
from pipeline.db_verify import count_table_rows, verify_database_counts
from pipeline.job_run import finish_job_run, start_job_run
from pipeline.job_run_telemetry import call_with_telemetry_session
from pipeline.run_coordinator import (
    begin_full_scrape,
    begin_import,
    begin_run,
    begin_tender_scrape,
    complete_full_scrape,
    complete_import,
    complete_tender_scrape,
    finish_run,
    mark_tender_scrape_step,
)
from pipeline.refresh_company_award_stats import refresh_company_award_stats
from pipeline.runs import new_run_id
from scraper.runners import (
    run_building_permits_scraper,
    run_commercial_scraper,
    run_federal_scraper,
    run_linkedin_scraper,
    run_merx_arch_scraper,
    run_news_scraper,
    run_reddit_scraper,
    run_vancouver_early_signal_events_scraper,
)

TENDER_SCRAPER_RUNNERS: tuple[tuple[str, str, Any], ...] = (
    ("scrape-federal", "Federal + MERX BC tenders", run_federal_scraper),
    ("scrape-merx-arch", "MERX architecture tenders", run_merx_arch_scraper),
    ("scrape-commercial", "Commercial tenders", run_commercial_scraper),
)

AUXILIARY_SCRAPER_RUNNERS: tuple[tuple[str, Any], ...] = (
    ("Building permits", run_building_permits_scraper),
    ("Vancouver early signal events", run_vancouver_early_signal_events_scraper),
    ("Reddit signals", run_reddit_scraper),
    ("News signals", run_news_scraper),
    ("LinkedIn signals", run_linkedin_scraper),
)

# (M3F foundation) The only two trigger values run_tender_data_pipeline()
# has real, honest production callers for today: the scheduled daily cron
# (pipeline/run.py, unchanged by this constant -- its bare call inherits
# the default below) and the manual full-pipeline admin endpoint
# (api/internal.py's POST /internal/pipeline/tender-data, which n8n also
# calls -- there is no distinct n8n-specific trigger path here, so "n8n"
# is deliberately not in this allowlist even though it's a valid value in
# the broader pipeline.job_run trigger schema used elsewhere).
_VALID_TENDER_DATA_PIPELINE_TRIGGERS = frozenset({"scheduler", "manual"})

BUILDING_PERMITS_JOB_RUN_TELEMETRY_FLAG = "ENABLE_BUILDING_PERMITS_JOB_RUN_TELEMETRY"
BUILDING_PERMITS_JOB_RUN_JOB_TYPE = "building_permits"
_BUILDING_PERMITS_TELEMETRY_LOG_LABEL = "Building permits telemetry"

# Flat int fields already present, unchanged, in
# run_building_permits_scraper()'s own return dict -- see
# scraper/building_permits.py::scrape_vancouver_permits(). Deliberately an
# explicit allowlist, not a blanket pass-through, same reasoning as every
# M3D-A/B/C count allowlist. `mode` (str), `csv_path` (a filesystem path
# string), and `source`/`city` (fixed string constants) are NEVER included
# -- validate_counts() rejects strings outright, and none of them are
# useful telemetry counts even if they weren't.
_BUILDING_PERMITS_COUNT_KEYS = ("permits_scraped", "permits_persisted", "days")


def building_permits_job_run_telemetry_enabled() -> bool:
    """Read-only feature-flag check, same "1"/"true"/"yes" convention as
    every other flag in this repo. False by default."""
    return env_flag(BUILDING_PERMITS_JOB_RUN_TELEMETRY_FLAG, default=False)


def _safe_building_permits_counts(result: dict) -> dict[str, int]:
    """Allowlisted counts only -- never `mode`, `csv_path`, or the fixed
    `source`/`city` string constants. None of those belong in
    ops_job_runs.counts (a flat numeric-only JSON object); this function
    only narrows further, and protects against the return dict growing an
    unexpected field in the future."""
    return {key: result[key] for key in _BUILDING_PERMITS_COUNT_KEYS if key in result}


def _building_permits_telemetry_start(*, trigger: str) -> str | None:
    def _do(session: object) -> str:
        return start_job_run(
            session,
            job_type=BUILDING_PERMITS_JOB_RUN_JOB_TYPE,
            trigger=trigger,
            source="permits",
        )

    return call_with_telemetry_session(
        _do,
        log_label=_BUILDING_PERMITS_TELEMETRY_LOG_LABEL,
        failure_message="failed to start job run tracking",
    )


def _building_permits_telemetry_finish(
    run_id: str,
    *,
    status: str,
    counts: dict[str, int] | None = None,
    raw_error: str | None = None,
) -> None:
    def _do(session: object) -> None:
        finish_job_run(
            session, run_id, status=status, counts=counts, raw_error=raw_error
        )

    call_with_telemetry_session(
        _do,
        log_label=_BUILDING_PERMITS_TELEMETRY_LOG_LABEL,
        failure_message="failed to finish job run tracking",
    )


def _run_building_permits_with_telemetry(runner, *, trigger: str) -> dict[str, Any]:
    """(M3F-1) Wraps run_building_permits_scraper() with optional
    ops_job_run telemetry -- started -> finished only. scrape_vancouver_
    permits() has no internal step boundaries to report (a single
    unwrapped fetch -> write -> persist sequence -- see the M3F audit),
    so there is no phase event to add and no honest partial_failure to
    distinguish -- only success/failed.

    Fail-open: a telemetry failure at any boundary never affects the
    scraper's own call or its exception propagation. On a raised
    exception, status="failed" is recorded and the exception is
    re-raised UNCHANGED, so the existing per-runner try/except in
    run_auxiliary_scrapers() below still performs its normal
    fail-and-continue handling -- this wrapper never swallows or alters
    what that loop already does.
    """
    telemetry_run_id = _building_permits_telemetry_start(trigger=trigger)
    try:
        counts = runner()
    except Exception as exc:
        if telemetry_run_id is not None:
            _building_permits_telemetry_finish(
                telemetry_run_id, status="failed", raw_error=str(exc)
            )
        raise
    else:
        if telemetry_run_id is not None:
            _building_permits_telemetry_finish(
                telemetry_run_id,
                status="success",
                counts=_safe_building_permits_counts(counts),
            )
        return counts


VANCOUVER_EARLY_SIGNAL_EVENTS_JOB_RUN_TELEMETRY_FLAG = (
    "ENABLE_VANCOUVER_EARLY_SIGNAL_EVENTS_JOB_RUN_TELEMETRY"
)
VANCOUVER_EARLY_SIGNAL_EVENTS_JOB_RUN_JOB_TYPE = "vancouver_early_signal_events"
_VANCOUVER_EARLY_SIGNAL_EVENTS_TELEMETRY_LOG_LABEL = (
    "Vancouver early signal events telemetry"
)

# Flat int fields already present, unchanged, in
# run_vancouver_early_signal_events_scraper()'s own return dict -- see
# scraper/vancouver_early_signal_events.py::scrape_vancouver_early_signal_events()
# (via _persist_records()). Deliberately an explicit allowlist, not a
# blanket pass-through, same reasoning as every other M3D/M3F count
# allowlist. `source`, `dataset`, and `municipality` (fixed string
# constants also present in that return dict) are NEVER included --
# validate_counts() rejects strings outright, and none of them are useful
# telemetry counts even if they weren't.
_VANCOUVER_EARLY_SIGNAL_EVENTS_COUNT_KEYS = (
    "events_scraped",
    "rezoning_applications",
    "development_permit_applications",
    "events_persisted",
)


def vancouver_early_signal_events_job_run_telemetry_enabled() -> bool:
    """Read-only feature-flag check, same "1"/"true"/"yes" convention as
    every other flag in this repo. False by default."""
    return env_flag(VANCOUVER_EARLY_SIGNAL_EVENTS_JOB_RUN_TELEMETRY_FLAG, default=False)


def _safe_vancouver_early_signal_events_counts(result: dict) -> dict[str, int]:
    """Allowlisted counts only -- never `source`, `dataset`, or
    `municipality` (fixed string constants). None of those belong in
    ops_job_runs.counts (a flat numeric-only JSON object); this function
    only narrows further, and protects against the return dict growing an
    unexpected field in the future."""
    return {
        key: result[key]
        for key in _VANCOUVER_EARLY_SIGNAL_EVENTS_COUNT_KEYS
        if key in result
    }


def _vancouver_early_signal_events_telemetry_start(*, trigger: str) -> str | None:
    def _do(session: object) -> str:
        return start_job_run(
            session,
            job_type=VANCOUVER_EARLY_SIGNAL_EVENTS_JOB_RUN_JOB_TYPE,
            trigger=trigger,
            source="vancouver_open_data",
        )

    return call_with_telemetry_session(
        _do,
        log_label=_VANCOUVER_EARLY_SIGNAL_EVENTS_TELEMETRY_LOG_LABEL,
        failure_message="failed to start job run tracking",
    )


def _vancouver_early_signal_events_telemetry_finish(
    run_id: str,
    *,
    status: str,
    counts: dict[str, int] | None = None,
    raw_error: str | None = None,
) -> None:
    def _do(session: object) -> None:
        finish_job_run(
            session, run_id, status=status, counts=counts, raw_error=raw_error
        )

    call_with_telemetry_session(
        _do,
        log_label=_VANCOUVER_EARLY_SIGNAL_EVENTS_TELEMETRY_LOG_LABEL,
        failure_message="failed to finish job run tracking",
    )


def _run_vancouver_early_signal_events_with_telemetry(
    runner, *, trigger: str
) -> dict[str, Any]:
    """(M3F-2) Wraps run_vancouver_early_signal_events_scraper() with
    optional ops_job_run telemetry -- started -> finished only.
    scrape_vancouver_early_signal_events() has no internal step
    boundaries to report (a single unwrapped fetch -> classify -> persist
    sequence -- see the M3F audit), so there is no phase event to add and
    no honest partial_failure to distinguish -- only success/failed.

    Fail-open: a telemetry failure at any boundary never affects the
    scraper's own call or its exception propagation. On a raised
    exception, status="failed" is recorded and the exception is
    re-raised UNCHANGED, so the existing per-runner try/except in
    run_auxiliary_scrapers() below still performs its normal
    fail-and-continue handling -- this wrapper never swallows or alters
    what that loop already does.

    This wraps only the base scrape (the scheduled auxiliary-scraper
    runner) -- the separate enrichment step
    (run_vancouver_early_signal_enrichment_scraper(), manual/n8n-only via
    POST /internal/enrich-early-signals) is untouched and out of scope.
    """
    telemetry_run_id = _vancouver_early_signal_events_telemetry_start(trigger=trigger)
    try:
        counts = runner()
    except Exception as exc:
        if telemetry_run_id is not None:
            _vancouver_early_signal_events_telemetry_finish(
                telemetry_run_id, status="failed", raw_error=str(exc)
            )
        raise
    else:
        if telemetry_run_id is not None:
            _vancouver_early_signal_events_telemetry_finish(
                telemetry_run_id,
                status="success",
                counts=_safe_vancouver_early_signal_events_counts(counts),
            )
        return counts


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def run_tender_scrapers(run_id: str) -> dict[str, Any]:
    """Run all tender scrapers sequentially and mark coordinator steps."""
    begin_tender_scrape(run_id)
    scrape_started_at = _utc_now()
    results: dict[str, Any] = {
        "scrape_started_at": scrape_started_at.isoformat(),
        "steps": {},
    }
    errors: list[str] = []

    print("[Pipeline] Phase 1/4: Tender scrapers (sequential)")
    for step, label, runner in TENDER_SCRAPER_RUNNERS:
        print(f"[Pipeline] Running {label}...")
        try:
            counts = runner()
            if counts.get("skipped"):
                print(f"[Pipeline] {label} skipped: {counts.get('reason', 'disabled')}")
            else:
                print(f"[Pipeline] {label} complete: {counts}")
            mark_tender_scrape_step(run_id, step)
            results["steps"][step] = counts
        except Exception as exc:
            errors.append(f"{label}: {exc}")
            print(f"[Pipeline] {label} failed: {exc}")

    if errors:
        finish_run(run_id, success=False, error="; ".join(errors))
        raise RuntimeError("Tender scrape phase failed: " + "; ".join(errors))

    complete_tender_scrape(run_id)
    results["scrape_finished_at"] = _utc_now().isoformat()
    return results


def run_auxiliary_scrapers(*, trigger: str = "scheduler") -> dict[str, Any]:
    """Run non-tender scrapers after tender CSVs are written (best-effort).

    (M3F foundation) ``trigger`` is a plain pass-through -- not
    re-validated here, run_tender_data_pipeline() is this function's only
    production caller and already validates it against
    _VALID_TENDER_DATA_PIPELINE_TRIGGERS before calling in.

    (M3F-1) The "Building permits" runner optionally persists run history
    to ops_job_runs/ops_job_run_events (migration 033, pipeline/job_run.py),
    gated by ENABLE_BUILDING_PERMITS_JOB_RUN_TELEMETRY (default false).
    With the flag off, run_building_permits_scraper() is called with the
    exact same zero-argument signature as before this change -- no
    telemetry writes of any kind. Uses the ``trigger`` value passed into
    this function (never hardcoded) -- honest whether this run came from
    the scheduled cron or the manual full-pipeline endpoint.

    (M3F-2) The "Vancouver early signal events" runner optionally persists
    run history the same way, gated by
    ENABLE_VANCOUVER_EARLY_SIGNAL_EVENTS_JOB_RUN_TELEMETRY (default
    false), with the same flag-off byte-equivalence guarantee. This wraps
    only the base scrape (this auxiliary-scraper entry) -- the separate
    manual/n8n-only enrichment step is untouched.

    The remaining three auxiliary scrapers (Reddit signals, News signals,
    LinkedIn signals) are untouched by this change and remain plain
    ``runner()`` calls.
    """
    print("[Pipeline] Running auxiliary scrapers (permits, signals)...")
    results: dict[str, Any] = {"errors": []}

    for label, runner in AUXILIARY_SCRAPER_RUNNERS:
        try:
            if (
                label == "Building permits"
                and building_permits_job_run_telemetry_enabled()
            ):
                counts = _run_building_permits_with_telemetry(runner, trigger=trigger)
            elif (
                label == "Vancouver early signal events"
                and vancouver_early_signal_events_job_run_telemetry_enabled()
            ):
                counts = _run_vancouver_early_signal_events_with_telemetry(
                    runner, trigger=trigger
                )
            else:
                counts = runner()
            if counts.get("skipped"):
                print(f"[Pipeline] {label} skipped: {counts.get('reason', 'disabled')}")
            else:
                print(f"[Pipeline] {label} complete: {counts}")
            results[label] = counts
        except Exception as exc:
            error = f"{label}: {exc}"
            results["errors"].append(error)
            print(f"[Pipeline] {label} failed: {exc}")

    if results["errors"]:
        print(
            "[Pipeline] Auxiliary scrapers completed with errors "
            f"({len(results['errors'])}); continuing to CSV verification"
        )
    return results


def run_tender_data_pipeline(
    *, run_id: str | None = None, trigger: str = "scheduler"
) -> dict[str, Any]:
    """
    Deterministic tender data path:
      1. Tender scrapers (sequential)
      2. Auxiliary scrapers
      3. CSV verification
      4. Import all CSVs + contract awards
      5. Database count verification

    (M3F foundation) ``trigger`` defaults to "scheduler" -- the honest
    default for the overwhelming majority real caller, the daily
    APScheduler cron (pipeline/run.py, unchanged by this parameter: its
    existing bare call inherits this default). The manual full-pipeline
    admin endpoint (api/internal.py's POST /internal/pipeline/tender-data,
    which n8n also calls) passes trigger="manual" explicitly. Validated
    against _VALID_TENDER_DATA_PIPELINE_TRIGGERS before any coordinator
    state is touched -- an invalid value raises ValueError immediately,
    before begin_run()/begin_full_scrape() ever run. Threaded straight
    into run_auxiliary_scrapers(trigger=trigger) -- no telemetry is added
    by this change; trigger is pure plumbing for a later per-source
    telemetry change to read.
    """
    if trigger not in _VALID_TENDER_DATA_PIPELINE_TRIGGERS:
        raise ValueError(
            f"trigger must be one of {sorted(_VALID_TENDER_DATA_PIPELINE_TRIGGERS)}, "
            f"got {trigger!r}"
        )

    actual_run_id = run_id or new_run_id()
    begin_run(actual_run_id)
    begin_full_scrape(actual_run_id)

    summary: dict[str, Any] = {"run_id": actual_run_id, "phases": {}}

    try:
        tender_scrape = run_tender_scrapers(actual_run_id)
        summary["phases"]["tender_scrape"] = tender_scrape

        auxiliary = run_auxiliary_scrapers(trigger=trigger)
        summary["phases"]["auxiliary_scrape"] = auxiliary
        complete_full_scrape(actual_run_id)

        scrape_started = datetime.fromisoformat(tender_scrape["scrape_started_at"])
        print("[Pipeline] Phase 2/4: Verify tender CSV artifacts")
        csv_results = verify_tender_csvs(not_before=scrape_started)
        summary["phases"]["csv_verification"] = csv_results

        print("[Pipeline] Phase 3/4: Import all CSVs into PostgreSQL")
        init_db()
        session = get_session()
        try:
            previous_counts = count_table_rows(session)
            begin_import(actual_run_id)
            import_counts = import_all_csvs(session)
            awards_imported = import_contract_awards(session)
            import_counts["contract_awards"] = awards_imported
            refresh_company_award_stats(session)
            complete_import(actual_run_id)
            summary["phases"]["import"] = import_counts

            print("[Pipeline] Phase 4/4: Verify database counts")
            db_results = verify_database_counts(
                session,
                import_counts,
                previous_counts=previous_counts,
            )
            summary["phases"]["db_verification"] = db_results
        finally:
            session.close()

        finish_run(actual_run_id, success=True)
        summary["status"] = "success"
        print("[Pipeline] Tender data pipeline finished successfully")
        return summary
    except Exception as exc:
        finish_run(actual_run_id, success=False, error=str(exc))
        summary["status"] = "failed"
        summary["error"] = str(exc)
        raise
