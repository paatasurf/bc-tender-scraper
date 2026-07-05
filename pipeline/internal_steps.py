from __future__ import annotations

from typing import Any, Callable

from config.env import env_flag
from db.connection import get_session, init_db
from db.import_contract_awards import import_contract_awards
from db.import_csv import import_all_csvs
from pipeline.ai_scoring import score_unscored_tenders
from pipeline.arch_company_intelligence import run_arch_company_intelligence
from pipeline.company_intelligence import run_company_intelligence
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


def run_company_intelligence_step() -> dict[str, Any]:
    session = get_session()
    try:
        return run_company_intelligence(session)
    finally:
        session.close()


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


def run_odbus_import_step(csv_path: str) -> dict[str, Any]:
    init_db()
    session = get_session()
    try:
        from pipeline.registry_verification.hub import import_reference_data
        from db.registry_constants import REGISTRY_SOURCE_ODBUS

        return import_reference_data(session, source=REGISTRY_SOURCE_ODBUS, path=csv_path)
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


def run_construction_tiers_step(*, company_ids: list[int] | None = None) -> dict[str, Any]:
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


def make_tender_scrape_worker(step: str, runner: TenderScrapeRunner, run_id: str) -> Callable[[], dict[str, Any]]:
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
