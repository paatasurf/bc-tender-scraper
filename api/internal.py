from __future__ import annotations

import hmac
import os
from typing import Annotated, Any, Literal

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request
from pydantic import BaseModel, Field

from config.env import env_flag
from db.connection import get_session
from db.closing_at_sync import backfill_all_tender_closing_at
from db.permit_source_status import backfill_permit_source_status
from pipeline.internal_steps import (
    assert_import_allowed,
    make_company_intelligence_worker,
    make_gated_import_worker,
    make_tender_scrape_worker,
    run_ai_scoring_step,
    run_arch_company_intelligence_step,
    run_import_contract_awards_step,
    run_odbus_import_step,
    run_orgbook_import_step,
    run_populate_project_contacts_step,
    run_populate_award_companies_step,
    run_registry_verification_match_step,
    run_construction_tiers_step,
)
from pipeline.run_coordinator import PipelineOrderError, get_run_state
from pipeline.lifecycle_resolver import resolve_tender_lifecycle
from pipeline.tender_data_pipeline import run_tender_data_pipeline
from pipeline.runs import (
    execute_tracked_step,
    find_in_flight_run,
    get_pipeline_run,
    list_recent_runs,
    list_runs_for_run_id,
    new_run_id,
    pipeline_run_to_dict,
    run_tracked_step,
    start_run,
)
from scraper.runners import (
    run_building_permits_scraper,
    run_commercial_scraper,
    run_federal_scraper,
    run_linkedin_scraper,
    run_merx_arch_scraper,
    run_merx_scraper,
    run_news_scraper,
    run_reddit_scraper,
    run_vancouver_early_signal_enrichment_scraper,
)

router = APIRouter(prefix="/internal", tags=["internal"])


class InternalRunRequest(BaseModel):
    run_id: str | None = Field(
        default=None,
        max_length=36,
        description="Optional shared run id for grouping steps in n8n orchestration.",
    )


class OdbusImportRequest(BaseModel):
    run_id: str | None = Field(default=None, max_length=36)
    csv_path: str = Field(
        ..., min_length=1, max_length=1000, description="Path to ODBus_v1.csv"
    )


class OrgbookImportRequest(BaseModel):
    run_id: str | None = Field(default=None, max_length=36)
    path: str = Field(
        ...,
        min_length=1,
        max_length=1000,
        description="Path to OrgBook CSV or JSONL export.",
    )


class VerificationHubImportRequest(BaseModel):
    run_id: str | None = Field(default=None, max_length=36)
    source: str = Field(
        ...,
        min_length=1,
        max_length=30,
        description="Provider source key (odbus, orgbook).",
    )
    path: str = Field(
        ...,
        min_length=1,
        max_length=1000,
        description="Path to provider reference export.",
    )


class RegistryVerificationMatchRequest(BaseModel):
    run_id: str | None = Field(default=None, max_length=36)
    company_ids: list[int] | None = Field(
        default=None,
        description="Optional canonical company ids; default all canonical rows.",
    )
    sources: list[str] | None = Field(
        default=None,
        description="Optional provider source keys; default all registered providers.",
    )
    include_review_tiers: bool = Field(
        default=False,
        description="When true, include T4 family and T5 fuzzy matches as review_pending links.",
    )


class ConstructionTiersRequest(BaseModel):
    run_id: str | None = Field(default=None, max_length=36)
    company_ids: list[int] | None = Field(
        default=None,
        description="Optional company ids; default all canonical + standalone rows.",
    )


class EnrichEarlySignalsRequest(BaseModel):
    run_id: str | None = Field(
        default=None,
        max_length=36,
        description="Optional shared run id for grouping steps in n8n orchestration.",
    )
    limit: int | None = Field(
        default=None,
        ge=1,
        le=25,
        description=(
            "Optional operational canary cap on candidates processed this run "
            "(1-25). Omitting it uses the scheduled default: an incremental "
            "keyset page of DEFAULT_PAGE_LIMIT (100) candidates, not the full "
            "backlog -- see refresh_all to opt into a full-table sweep."
        ),
    )
    since_id: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Optional manual override for the keyset pagination cursor "
            "(process only early_signal_events.id > since_id). Omitting it "
            "preserves the normal behavior: the run auto-continues from "
            "wherever the previous enrich-early-signals run left off."
        ),
    )
    refresh_all: bool = Field(
        default=False,
        description=(
            "Explicit opt-in for a full, unbounded, single-pass sweep of the "
            "whole table (only takes effect when limit is omitted). The "
            "default (false) keeps every scheduled/n8n run bounded to an "
            "incremental page so cost never scales with table size."
        ),
    )


class GoogleEnrichmentRunRequest(BaseModel):
    run_id: str | None = Field(
        default=None,
        max_length=36,
        description="Optional shared run id for pipeline_runs grouping.",
    )
    dry_run: bool = Field(
        default=False,
        description="When true, perform lookups and matching but do not write companies or reviews.",
    )
    batch_size: int | None = Field(
        default=None,
        ge=1,
        le=100,
        description="Override GOOGLE_ENRICHMENT_BATCH_SIZE for this run.",
    )
    company_ids: list[int] | None = Field(
        default=None,
        description="Optional explicit company ids (useful for dry_run smoke tests).",
    )


class EnrichmentRunRequest(BaseModel):
    trigger: Literal["profile_view", "agent", "manual"] = Field(
        default="manual",
        description="RFC S7 step 1. An unrecognized value is rejected by "
        "Pydantic at request-parsing time (422), before the route body "
        "runs -- not a bare ValueError from start_or_join_job()'s own "
        "trigger check, which would otherwise surface as an unhandled 500.",
    )


def _require_manual_pipeline() -> None:
    if os.getenv("ALLOW_MANUAL_PIPELINE", "false").lower() not in {"1", "true", "yes"}:
        raise HTTPException(status_code=403, detail="Manual pipeline runs are disabled")


def _require_internal_key(request: Request) -> None:
    expected = os.getenv("INTERNAL_API_KEY")
    if not expected:
        raise HTTPException(status_code=403, detail="Forbidden")
    key = request.headers.get("X-Internal-Key")
    if key is None or not hmac.compare_digest(key, expected):
        raise HTTPException(status_code=403, detail="Forbidden")


def _step_status_path(pipeline_run_id: int) -> str:
    return f"/internal/steps/{pipeline_run_id}"


def _enqueue_tender_scrape_step(
    background_tasks: BackgroundTasks,
    step: str,
    runner,
    run_id: str | None,
) -> dict[str, Any]:
    actual_run_id = run_id or new_run_id()
    return _enqueue_step(
        background_tasks,
        step,
        make_tender_scrape_worker(step, runner, actual_run_id),
        actual_run_id,
    )


def _enqueue_import_step(
    background_tasks: BackgroundTasks,
    run_id: str | None,
) -> dict[str, Any]:
    actual_run_id = _resolve_import_run_id(run_id)
    return _enqueue_step(
        background_tasks,
        "import-csvs",
        make_gated_import_worker(actual_run_id),
        actual_run_id,
    )


def _resolve_import_run_id(run_id: str | None) -> str:
    try:
        assert_import_allowed(run_id)
    except PipelineOrderError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if run_id:
        return run_id
    state = get_run_state()
    if state is None:
        raise HTTPException(
            status_code=409,
            detail="Import blocked: no active pipeline run is available for import.",
        )
    return state.run_id


def _run_status_path(run_id: str) -> str:
    return f"/internal/runs/{run_id}"


def _enqueue_step(
    background_tasks: BackgroundTasks,
    step: str,
    worker,
    run_id: str | None,
) -> dict[str, Any]:
    """Start `step` in the background under `run_id` (generating one if
    omitted), unless a run for this exact (step, run_id) is already in
    flight -- in which case no second worker is scheduled and the caller
    gets back the existing in-flight run's poll URLs instead. Without this
    check, a retried/duplicate trigger for the same explicit run_id (e.g.
    an n8n retry after a slow response) would call start_run() twice and
    schedule two concurrent workers against two independent "running"
    pipeline_runs rows that nothing ever reconciles -- see
    find_in_flight_run()'s docstring for the production incident this
    reproduces (pipeline_runs id 754/755, step="import-csvs",
    run_id="n8n-5564", 2026-08-05)."""
    actual_run_id = run_id or new_run_id()
    bootstrap = get_session()
    try:
        if run_id is not None:
            in_flight = find_in_flight_run(bootstrap, step, actual_run_id)
            if in_flight is not None:
                pipeline_run_id = in_flight.id
                return {
                    "status": "already_running",
                    "run_id": actual_run_id,
                    "step": step,
                    "pipeline_run_id": pipeline_run_id,
                    "poll_url": _step_status_path(pipeline_run_id),
                    "run_poll_url": _run_status_path(actual_run_id),
                }
        record = start_run(bootstrap, step, actual_run_id)
        pipeline_run_id = record.id
    finally:
        bootstrap.close()

    background_tasks.add_task(
        run_tracked_step,
        step,
        worker,
        run_id=actual_run_id,
        record_id=pipeline_run_id,
    )
    return {
        "status": "started",
        "run_id": actual_run_id,
        "step": step,
        "pipeline_run_id": pipeline_run_id,
        "poll_url": _step_status_path(pipeline_run_id),
        "run_poll_url": _run_status_path(actual_run_id),
    }


def _run_step_sync(
    step: str,
    worker,
    run_id: str | None,
) -> dict[str, Any]:
    actual_run_id = run_id or new_run_id()
    result = execute_tracked_step(step, worker, run_id=actual_run_id)
    return {
        "status": result.get("status", "success"),
        "run_id": actual_run_id,
        "step": step,
        "pipeline_run_id": result.get("id"),
        "poll_url": _step_status_path(result["id"]) if result.get("id") else None,
        "run_poll_url": _run_status_path(actual_run_id),
        "started_at": result.get("started_at"),
        "finished_at": result.get("finished_at"),
        "error": result.get("error"),
        "counts": result.get("counts", {}),
    }


@router.post("/scrape/federal")
def scrape_federal(
    background_tasks: BackgroundTasks,
    body: InternalRunRequest | None = None,
) -> dict[str, Any]:
    _require_manual_pipeline()
    return _enqueue_tender_scrape_step(
        background_tasks,
        "scrape-federal",
        run_federal_scraper,
        body.run_id if body else None,
    )


@router.post("/scrape/merx")
def scrape_merx(
    background_tasks: BackgroundTasks,
    body: InternalRunRequest | None = None,
) -> dict[str, Any]:
    """Refresh MERX BC open tenders and merge with existing federal rows in tenders.csv."""
    _require_manual_pipeline()
    return _enqueue_step(
        background_tasks,
        "scrape-merx",
        run_merx_scraper,
        body.run_id if body else None,
    )


@router.post("/scrape/merx-arch")
def scrape_merx_arch(
    background_tasks: BackgroundTasks,
    body: InternalRunRequest | None = None,
) -> dict[str, Any]:
    _require_manual_pipeline()
    return _enqueue_tender_scrape_step(
        background_tasks,
        "scrape-merx-arch",
        run_merx_arch_scraper,
        body.run_id if body else None,
    )


@router.post("/scrape/commercial")
def scrape_commercial(
    background_tasks: BackgroundTasks,
    body: InternalRunRequest | None = None,
) -> dict[str, Any]:
    _require_manual_pipeline()
    return _enqueue_tender_scrape_step(
        background_tasks,
        "scrape-commercial",
        run_commercial_scraper,
        body.run_id if body else None,
    )


@router.post("/scrape/building-permits")
def scrape_building_permits(
    background_tasks: BackgroundTasks,
    body: InternalRunRequest | None = None,
) -> dict[str, Any]:
    _require_manual_pipeline()
    return _enqueue_step(
        background_tasks,
        "scrape-building-permits",
        run_building_permits_scraper,
        body.run_id if body else None,
    )


@router.post("/scrape/reddit")
def scrape_reddit(
    background_tasks: BackgroundTasks,
    body: InternalRunRequest | None = None,
) -> dict[str, Any]:
    _require_manual_pipeline()
    return _enqueue_step(
        background_tasks,
        "scrape-reddit",
        run_reddit_scraper,
        body.run_id if body else None,
    )


@router.post("/scrape/news")
def scrape_news(
    background_tasks: BackgroundTasks,
    body: InternalRunRequest | None = None,
) -> dict[str, Any]:
    _require_manual_pipeline()
    return _enqueue_step(
        background_tasks,
        "scrape-news",
        run_news_scraper,
        body.run_id if body else None,
    )


@router.post("/scrape/linkedin")
def scrape_linkedin(
    background_tasks: BackgroundTasks,
    body: InternalRunRequest | None = None,
) -> dict[str, Any]:
    _require_manual_pipeline()
    return _enqueue_step(
        background_tasks,
        "scrape-linkedin",
        run_linkedin_scraper,
        body.run_id if body else None,
    )


@router.post("/enrich-early-signals")
def enrich_early_signals(
    request: Request,
    background_tasks: BackgroundTasks,
    body: EnrichEarlySignalsRequest | None = None,
) -> dict[str, Any]:
    _require_internal_key(request)
    payload = body or EnrichEarlySignalsRequest()

    def _worker() -> dict[str, Any]:
        return run_vancouver_early_signal_enrichment_scraper(
            limit=payload.limit,
            since_id=payload.since_id,
            refresh_all=payload.refresh_all,
        )

    return _enqueue_step(
        background_tasks,
        "enrich-early-signals",
        _worker,
        payload.run_id,
    )


@router.post("/populate-project-contacts")
def populate_project_contacts(
    request: Request,
    background_tasks: BackgroundTasks,
    body: InternalRunRequest | None = None,
) -> dict[str, Any]:
    _require_internal_key(request)
    return _enqueue_step(
        background_tasks,
        "populate-project-contacts",
        run_populate_project_contacts_step,
        body.run_id if body else None,
    )


@router.post("/scrape/contract-awards")
def scrape_contract_awards(
    background_tasks: BackgroundTasks,
    body: InternalRunRequest | None = None,
) -> dict[str, Any]:
    """Legacy alias for import-contract-awards."""
    _require_manual_pipeline()
    return _enqueue_step(
        background_tasks,
        "import-contract-awards",
        run_import_contract_awards_step,
        body.run_id if body else None,
    )


@router.post("/import/contract-awards")
def import_contract_awards_route(
    background_tasks: BackgroundTasks,
    body: InternalRunRequest | None = None,
) -> dict[str, Any]:
    _require_manual_pipeline()
    return _enqueue_step(
        background_tasks,
        "import-contract-awards",
        run_import_contract_awards_step,
        body.run_id if body else None,
    )


@router.post("/lifecycle/resolve")
def resolve_lifecycle(request: Request) -> dict[str, Any]:
    """Apply deterministic lifecycle transitions (P2-02). Nightly n8n trigger."""
    _require_internal_key(request)
    from db.connection import init_db

    init_db()
    session = get_session()
    try:
        return resolve_tender_lifecycle(session)
    finally:
        session.close()


@router.post("/lifecycle/backfill-permit-status")
def backfill_permit_status(request: Request) -> dict[str, Any]:
    """Reserved for future true lifecycle status backfill (PLPOS backlog; currently no-op)."""
    _require_internal_key(request)
    from db.connection import init_db

    init_db()
    session = get_session()
    try:
        return backfill_permit_source_status(session, only_empty=True)
    finally:
        session.close()


@router.post("/lifecycle/backfill-closing-at")
def backfill_closing_at(request: Request) -> dict[str, Any]:
    """One-time/idempotent backfill of closing_at from deadline strings (P2-06)."""
    _require_internal_key(request)
    from db.connection import init_db

    init_db()
    session = get_session()
    try:
        tables = backfill_all_tender_closing_at(session, only_null=True)
        return {
            "only_null": True,
            "tables": tables,
            "totals": {
                "updated": sum(item["updated"] for item in tables.values()),
                "after_set": sum(item["after_set"] for item in tables.values()),
                "after_null": sum(item["after_null"] for item in tables.values()),
            },
        }
    finally:
        session.close()


@router.post("/import")
def import_csvs(
    background_tasks: BackgroundTasks,
    body: InternalRunRequest | None = None,
    sync: Annotated[
        bool,
        Query(description="When true, run import synchronously and return counts."),
    ] = False,
) -> dict[str, Any]:
    _require_manual_pipeline()
    run_id = body.run_id if body else None
    if sync:
        actual_run_id = _resolve_import_run_id(run_id)
        return _run_step_sync(
            "import-csvs",
            make_gated_import_worker(actual_run_id),
            actual_run_id,
        )
    return _enqueue_import_step(background_tasks, run_id)


@router.post("/pipeline/tender-data")
def run_tender_data_pipeline_route(
    body: InternalRunRequest | None = None,
    sync: bool = Query(
        True,
        description="Run the full deterministic tender-data pipeline synchronously.",
    ),
) -> dict[str, Any]:
    """Run tender scrapers → CSV verify → import → DB verify in strict order."""
    _require_manual_pipeline()
    run_id = body.run_id if body else None
    if not sync:
        raise HTTPException(
            status_code=400,
            detail="Only sync=true is supported for /internal/pipeline/tender-data",
        )
    try:
        summary = run_tender_data_pipeline(run_id=run_id, trigger="manual")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    from pipeline.run_coordinator import assert_import_not_before_scrape

    return {
        "status": summary.get("status", "success"),
        "run_id": summary.get("run_id"),
        "ordering_audit": assert_import_not_before_scrape(),
        "phases": summary.get("phases", {}),
    }


@router.post("/ai-scoring")
def ai_scoring(
    background_tasks: BackgroundTasks,
    body: InternalRunRequest | None = None,
    sync: bool = Query(
        False,
        description="When true, run to completion and return counts instead of starting a background job.",
    ),
) -> dict[str, Any]:
    _require_manual_pipeline()
    run_id = body.run_id if body else None
    if sync:
        return _run_step_sync("ai-scoring", run_ai_scoring_step, run_id)
    return _enqueue_step(
        background_tasks,
        "ai-scoring",
        run_ai_scoring_step,
        run_id,
    )


@router.post("/company-intelligence")
def company_intelligence(
    background_tasks: BackgroundTasks,
    body: InternalRunRequest | None = None,
) -> dict[str, Any]:
    _require_manual_pipeline()
    # Resolved here (not left to _enqueue_step's own run_id-or-new_run_id()
    # fallback) so the SAME value can be captured into the worker closure
    # below and threaded through to ops_job_runs telemetry -- see
    # pipeline/company_intelligence_telemetry.py's module docstring.
    actual_run_id = (body.run_id if body else None) or new_run_id()
    return _enqueue_step(
        background_tasks,
        "company-intelligence",
        make_company_intelligence_worker(actual_run_id),
        actual_run_id,
    )


@router.post("/arch-company-intelligence")
def arch_company_intelligence(
    background_tasks: BackgroundTasks,
    body: InternalRunRequest | None = None,
) -> dict[str, Any]:
    _require_manual_pipeline()
    return _enqueue_step(
        background_tasks,
        "arch-company-intelligence",
        run_arch_company_intelligence_step,
        body.run_id if body else None,
    )


@router.post("/import/odbus")
def import_odbus_reference(
    background_tasks: BackgroundTasks,
    body: OdbusImportRequest,
    sync: bool = Query(
        False,
        description="When true, run import synchronously and return counts.",
    ),
) -> dict[str, Any]:
    _require_manual_pipeline()

    def _worker() -> dict[str, Any]:
        return run_odbus_import_step(body.csv_path)

    if sync:
        return _run_step_sync("import-odbus", _worker, body.run_id)
    return _enqueue_step(background_tasks, "import-odbus", _worker, body.run_id)


@router.post("/import/orgbook")
def import_orgbook_reference(
    background_tasks: BackgroundTasks,
    body: OrgbookImportRequest,
    sync: bool = Query(
        False,
        description="When true, run import synchronously and return counts.",
    ),
) -> dict[str, Any]:
    _require_manual_pipeline()

    def _worker() -> dict[str, Any]:
        return run_orgbook_import_step(body.path)

    if sync:
        return _run_step_sync("import-orgbook", _worker, body.run_id)
    return _enqueue_step(background_tasks, "import-orgbook", _worker, body.run_id)


@router.post("/verification-hub/import")
def verification_hub_import(
    background_tasks: BackgroundTasks,
    body: VerificationHubImportRequest,
    sync: bool = Query(
        False,
        description="When true, run import synchronously and return counts.",
    ),
) -> dict[str, Any]:
    _require_manual_pipeline()

    def _worker() -> dict[str, Any]:
        from db.connection import get_session, init_db
        from pipeline.registry_verification.hub import import_reference_data

        init_db()
        session = get_session()
        try:
            return import_reference_data(session, source=body.source, path=body.path)
        finally:
            session.close()

    if sync:
        return _run_step_sync("verification-hub-import", _worker, body.run_id)
    return _enqueue_step(
        background_tasks, "verification-hub-import", _worker, body.run_id
    )


@router.post("/registry-verification/match")
def registry_verification_match(
    background_tasks: BackgroundTasks,
    body: RegistryVerificationMatchRequest | None = None,
    sync: bool = Query(
        False,
        description="When true, run matching synchronously and return counts.",
    ),
) -> dict[str, Any]:
    _require_manual_pipeline()
    payload = body or RegistryVerificationMatchRequest()

    def _worker() -> dict[str, Any]:
        return run_registry_verification_match_step(
            company_ids=payload.company_ids,
            include_review_tiers=payload.include_review_tiers,
            sources=payload.sources,
        )

    if sync:
        return _run_step_sync("registry-verification-match", _worker, payload.run_id)
    return _enqueue_step(
        background_tasks,
        "registry-verification-match",
        _worker,
        payload.run_id,
    )


@router.post("/construction-tiers")
def construction_tiers(
    background_tasks: BackgroundTasks,
    body: ConstructionTiersRequest | None = None,
    sync: bool = Query(
        False,
        description="When true, run tier computation synchronously and return counts.",
    ),
) -> dict[str, Any]:
    _require_manual_pipeline()
    payload = body or ConstructionTiersRequest()

    def _worker() -> dict[str, Any]:
        return run_construction_tiers_step(company_ids=payload.company_ids)

    if sync:
        return _run_step_sync("construction-tiers", _worker, payload.run_id)
    return _enqueue_step(
        background_tasks, "construction-tiers", _worker, payload.run_id
    )


@router.get("/steps/{pipeline_run_id}")
def get_pipeline_step_status(pipeline_run_id: int) -> dict[str, Any]:
    """Poll a single pipeline step by database id (returned as pipeline_run_id from POST)."""
    session = get_session()
    try:
        record = get_pipeline_run(session, pipeline_run_id)
        if record is None:
            raise HTTPException(
                status_code=404,
                detail=f"No pipeline run found for id '{pipeline_run_id}'",
            )
        payload = pipeline_run_to_dict(record)
        payload["done"] = record.status in {
            "success",
            "failed",
            "skipped",
            "partial_success",
        }
        return payload
    finally:
        session.close()


@router.get("/runs")
def list_pipeline_runs(
    step: str | None = Query(None, max_length=100),
    limit: int = Query(20, ge=1, le=100),
) -> dict[str, Any]:
    session = get_session()
    try:
        records = list_recent_runs(session, step=step, limit=limit)
        return {
            "total": len(records),
            "limit": limit,
            "step": step,
            "data": [pipeline_run_to_dict(record) for record in records],
        }
    finally:
        session.close()


@router.get("/runs/{run_id}")
def get_runs_for_id(run_id: str) -> dict[str, Any]:
    session = get_session()
    try:
        records = list_runs_for_run_id(session, run_id)
        if not records:
            raise HTTPException(
                status_code=404, detail=f"No pipeline runs found for run_id '{run_id}'"
            )
        return {
            "run_id": run_id,
            "total": len(records),
            "data": [pipeline_run_to_dict(record) for record in records],
        }
    finally:
        session.close()


@router.get("/google-enrichment/metrics")
def google_enrichment_metrics(request: Request) -> dict[str, Any]:
    """Aggregated Google enrichment inventory + ops health (internal monitoring only)."""
    _require_internal_key(request)
    from db.connection import init_db
    from pipeline.google_enrichment.metrics import get_google_enrichment_metrics

    init_db()
    session = get_session()
    try:
        return get_google_enrichment_metrics(session)
    finally:
        session.close()


@router.post("/google-enrichment/run")
def google_enrichment_run(
    request: Request,
    body: GoogleEnrichmentRunRequest | None = None,
) -> dict[str, Any]:
    """Run Google enrichment batch (n8n daily trigger or manual dry_run)."""
    _require_internal_key(request)
    from db.connection import init_db
    from pipeline.google_enrichment.orchestrator import run_google_enrichment

    init_db()
    payload = body or GoogleEnrichmentRunRequest()

    def _worker() -> dict[str, Any]:
        session = get_session()
        try:
            return run_google_enrichment(
                session,
                run_id=payload.run_id,
                dry_run=payload.dry_run,
                batch_size=payload.batch_size,
                company_ids=payload.company_ids,
            )
        finally:
            session.close()

    return execute_tracked_step(
        "google-enrichment",
        _worker,
        run_id=payload.run_id,
    )


def _enrichment_enabled() -> bool:
    """RFC S10 master kill switch. Default false -- until an operator
    explicitly sets ENRICHMENT_ENABLED=true on a target environment (a
    separate, deliberate config change never bundled with a code deploy),
    the route below returns 503 before touching a session, a provider, or
    a company_enrichment_jobs row."""
    return env_flag("ENRICHMENT_ENABLED", default=False)


@router.post("/enrichment/company/{company_id}/run")
def enrichment_company_run(
    company_id: int,
    request: Request,
    background_tasks: BackgroundTasks,
    body: EnrichmentRunRequest | None = None,
) -> dict[str, Any]:
    """On-demand company enrichment (RFC Phase 2, docs/COMPANY_ON_DEMAND_ENRICHMENT_RFC.md
    S7/S8.2). Disabled by default -- see _enrichment_enabled(). When
    enabled: cache-check and in-flight dedup (RFC S7 steps 2-3) run
    synchronously (fast, local-DB-only) so the response is immediately
    accurate; the provider cascade itself (RFC S7 steps 5-7, real
    provider latency) always runs as a background task, never inline in
    this request handler -- matching this RFC's own "no request handler
    thread may ever call a provider inline" principle."""
    _require_internal_key(request)
    if not _enrichment_enabled():
        raise HTTPException(
            status_code=503,
            detail="Company enrichment is disabled (ENRICHMENT_ENABLED=false)",
        )

    from db.connection import init_db
    from db.models import Company
    from pipeline.company_enrichment.orchestrator import (
        check_cache,
        run_cascade_for_job,
        start_or_join_job,
    )

    init_db()
    payload = body or EnrichmentRunRequest()

    session = get_session()
    try:
        company = session.get(Company, company_id)
        if company is None:
            raise HTTPException(status_code=404, detail=f"Company {company_id} not found")
        company_name = company.name

        cached = check_cache(session, company_id)
        if cached is not None:
            return cached

        run_id, joined = start_or_join_job(session, company_id, trigger=payload.trigger)
    finally:
        session.close()

    if joined:
        return {"status": "already_running", "company_id": company_id, "run_id": run_id}

    def _worker() -> None:
        worker_session = get_session()
        try:
            run_cascade_for_job(worker_session, run_id, company_id, company_name)
        finally:
            worker_session.close()

    background_tasks.add_task(_worker)
    return {
        "status": "started",
        "company_id": company_id,
        "run_id": run_id,
        # No poll_url yet -- GET /api/companies/{id}/enrichment (RFC S8.1)
        # is Phase 4 scope, not implemented by this phase. Progress can
        # only be observed by querying company_enrichment_jobs directly.
    }


@router.get("/kg/validation-snapshot")
def kg_validation_snapshot(request: Request) -> dict[str, Any]:
    """Read-only P1/P2 staging gate metrics (X-Internal-Key required)."""
    _require_internal_key(request)
    from pipeline.kg_validation_snapshot import collect_kg_validation_snapshot

    session = get_session()
    try:
        return collect_kg_validation_snapshot(session)
    finally:
        session.close()


@router.post("/kg/populate-award-companies")
def kg_populate_award_companies(
    request: Request,
    dry_run: bool = Query(
        True, description="When true, no company inserts (shadow-safe)."
    ),
    sync: bool = Query(
        True, description="Must be true; sync run for staging validation."
    ),
    run_id: str | None = Query(None, max_length=36),
) -> dict[str, Any]:
    """Award population cycle for P2 shadow validation (X-Internal-Key required)."""
    _require_internal_key(request)
    if not sync:
        raise HTTPException(
            status_code=400, detail="Only sync=true is supported for this endpoint"
        )

    def _worker() -> dict[str, Any]:
        return run_populate_award_companies_step(dry_run=dry_run)

    return _run_step_sync("populate-award-companies", _worker, run_id)


@router.post("/send-alerts")
def send_alerts() -> dict[str, Any]:
    """
    Generate and send personalized email digests for all client profiles
    with alerts_enabled=true.  Sends Telegram notification when batch completes.
    """
    _require_manual_pipeline()
    from intelligence.email_alerts import send_all_alert_digests

    return send_all_alert_digests()
