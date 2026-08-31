from __future__ import annotations

from typing import Any, Callable

from config.env import env_flag
from db.connection import get_session, init_db
from db.import_contract_awards import import_contract_awards
from db.import_csv import import_all_csvs
from pipeline.ai_scoring import score_unscored_tenders
from pipeline.arch_company_intelligence import run_arch_company_intelligence
from pipeline.company_intelligence import run_company_intelligence
from pipeline.company_intelligence_telemetry import (
    company_intelligence_job_run_telemetry_enabled,
    finish_company_intelligence_telemetry,
    record_company_intelligence_phase,
    safe_company_intelligence_counts,
    start_company_intelligence_telemetry,
)
from pipeline.construction_tier import compute_construction_tiers
from pipeline.project_intelligence import rebuild_project_contacts
from pipeline.run_coordinator import (
    assert_ready_for_import,
    begin_import,
    begin_run,
    begin_tender_scrape,
    complete_import,
    get_run_state,
    mark_tender_scrape_step,
)

TenderScrapeRunner = Callable[[], dict[str, Any]]


def run_import_step() -> dict[str, Any]:
    init_db()
    session = get_session()
    try:
        return import_all_csvs(session)
    finally:
        session.close()


def run_import_contract_awards_step() -> dict[str, Any]:
    init_db()
    session = get_session()
    try:
        return import_contract_awards(session)
    finally:
        session.close()


def run_ai_scoring_step() -> dict[str, Any]:
    if env_flag("PIPELINE_SKIP_AI_SCORING"):
        return {"skipped": True, "reason": "PIPELINE_SKIP_AI_SCORING=true"}

    session = get_session()
    try:
        return score_unscored_tenders(session)
    finally:
        session.close()


def run_company_intelligence_step(*, run_id: str | None = None) -> dict[str, Any]:
    """`run_id`, when given, is api.internal._enqueue_step's own
    pipeline_runs.run_id for this invocation -- passed through so
    ops_job_runs telemetry (when ENABLE_COMPANY_INTELLIGENCE_JOB_RUN_
    TELEMETRY is on) can be correlated 1:1 with the pipeline_runs row via
    the SAME run_id, matching what the scheduled path
    (pipeline/run.py::run_pipeline()) already does for its own telemetry.
    See pipeline/company_intelligence_telemetry.py's module docstring.

    Unlike the scheduled path, this function's own contract (used as the
    `worker` callable inside pipeline.runs._execute_tracked_worker) is
    that an exception MUST propagate -- callers must never see this
    silently swallow a failure, since pipeline_runs.status="failed"
    depends on it. Telemetry failure is recorded (fail-open, per
    finish_company_intelligence_telemetry's own contract) but never
    changes whether or how this function raises.

    Backward compatible: called with no run_id (or with the telemetry
    flag off), this is the exact same call as before this instrumentation
    existed -- no telemetry call of any kind, byte-for-byte."""
    session = get_session()
    try:
        if run_id is None or not company_intelligence_job_run_telemetry_enabled():
            return run_company_intelligence(session)

        telemetry_run_id = start_company_intelligence_telemetry(
            trigger="manual", run_id=run_id
        )
        try:
            if telemetry_run_id is not None:

                def on_phase(phase: str) -> None:
                    record_company_intelligence_phase(telemetry_run_id, phase)

                result = run_company_intelligence(session, on_phase=on_phase)
            else:
                # Telemetry start failed (fail-open): call with the exact
                # pre-instrumentation signature -- no on_phase kwarg at
                # all -- so this path is byte-for-byte the same call as
                # when the flag is off.
                result = run_company_intelligence(session)
        except Exception as exc:
            if telemetry_run_id is not None:
                finish_company_intelligence_telemetry(
                    telemetry_run_id, status="failed", raw_error=str(exc)
                )
            raise
        else:
            if telemetry_run_id is not None:
                finish_company_intelligence_telemetry(
                    telemetry_run_id,
                    status="success",
                    counts=safe_company_intelligence_counts(result),
                )
            return result
    finally:
        session.close()


def make_company_intelligence_worker(run_id: str) -> Callable[[], dict[str, Any]]:
    """Closure form for api.internal._enqueue_step, mirroring
    make_gated_import_worker's existing pattern -- captures the
    already-resolved pipeline_runs run_id so run_company_intelligence_step
    can correlate its own ops_job_runs telemetry against it."""

    def worker() -> dict[str, Any]:
        return run_company_intelligence_step(run_id=run_id)

    return worker


def run_arch_company_intelligence_step() -> dict[str, Any]:
    session = get_session()
    try:
        return run_arch_company_intelligence(session)
    finally:
        session.close()


def run_populate_project_contacts_step() -> dict[str, Any]:
    init_db()
    session = get_session()
    try:
        return rebuild_project_contacts(session)
    finally:
        session.close()


def run_populate_award_companies_step(*, dry_run: bool = True) -> dict[str, Any]:
    init_db()
    session = get_session()
    try:
        from pipeline.populate_companies_from_awards import (
            populate_companies_from_awards,
        )

        return populate_companies_from_awards(session, dry_run=dry_run)
    finally:
        session.close()


def run_odbus_import_step(csv_path: str) -> dict[str, Any]:
    init_db()
    session = get_session()
    try:
        from pipeline.registry_verification.hub import import_reference_data
        from db.registry_constants import REGISTRY_SOURCE_ODBUS

        return import_reference_data(
            session, source=REGISTRY_SOURCE_ODBUS, path=csv_path
        )
    finally:
        session.close()


def run_orgbook_import_step(path: str) -> dict[str, Any]:
    init_db()
    session = get_session()
    try:
        from pipeline.registry_verification.hub import import_reference_data
        from db.registry_constants import REGISTRY_SOURCE_ORGBOOK

        return import_reference_data(session, source=REGISTRY_SOURCE_ORGBOOK, path=path)
    finally:
        session.close()


def run_registry_verification_match_step(
    *,
    company_ids: list[int] | None = None,
    include_review_tiers: bool = False,
    sources: list[str] | None = None,
) -> dict[str, Any]:
    init_db()
    session = get_session()
    try:
        from pipeline.registry_verification.hub import batch_match

        return batch_match(
            session,
            sources=sources,
            company_ids=company_ids,
            include_review_tiers=include_review_tiers,
        )
    finally:
        session.close()


def run_construction_tiers_step(
    *, company_ids: list[int] | None = None
) -> dict[str, Any]:
    init_db()
    session = get_session()
    try:
        return compute_construction_tiers(session, company_ids=company_ids)
    finally:
        session.close()


def run_cip_backfill_step(
    *,
    dry_run: bool = True,
    sample_size: int | None = None,
    company_ids: list[int] | None = None,
) -> dict[str, Any]:
    from pipeline.cip_backfill import backfill_company_cips

    session = get_session()
    try:
        return backfill_company_cips(
            session,
            dry_run=dry_run,
            sample_size=sample_size,
            company_ids=company_ids,
        )
    finally:
        session.close()


def ensure_run_started(run_id: str) -> None:
    state = get_run_state()
    if state is None or state.run_id != run_id:
        begin_run(run_id)
        begin_tender_scrape(run_id)


def make_tender_scrape_worker(
    step: str, runner: TenderScrapeRunner, run_id: str
) -> Callable[[], dict[str, Any]]:
    def worker() -> dict[str, Any]:
        ensure_run_started(run_id)
        begin_tender_scrape(run_id)
        result = runner()
        mark_tender_scrape_step(run_id, step)
        return result

    return worker


def make_gated_import_worker(run_id: str) -> Callable[[], dict[str, Any]]:
    def worker() -> dict[str, Any]:
        assert_ready_for_import(run_id)
        begin_import(run_id)
        try:
            return run_import_step()
        finally:
            complete_import(run_id)

    return worker


def assert_import_allowed(run_id: str | None) -> None:
    """Raise PipelineOrderError when import is requested before scrapes finish."""
    assert_ready_for_import(run_id)
