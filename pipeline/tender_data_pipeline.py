from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from db.connection import get_session, init_db
from db.import_contract_awards import import_contract_awards
from db.import_csv import import_all_csvs
from pipeline.csv_verify import verify_tender_csvs
from pipeline.db_verify import count_table_rows, verify_database_counts
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

    (M3F foundation) ``trigger`` is a plain pass-through, not re-validated
    here -- run_tender_data_pipeline() is this function's only production
    caller and already validates it against
    _VALID_TENDER_DATA_PIPELINE_TRIGGERS before calling in. Currently
    unused inside this function body -- no telemetry is added by this
    change; this parameter exists purely so a future per-source telemetry
    change (Building Permits / Vancouver Early Signal Events / News
    Signals) can read an honest trigger value instead of assuming
    "scheduler" unconditionally.
    """
    print("[Pipeline] Running auxiliary scrapers (permits, signals)...")
    results: dict[str, Any] = {"errors": []}

    for label, runner in AUXILIARY_SCRAPER_RUNNERS:
        try:
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
