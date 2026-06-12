from __future__ import annotations

from pathlib import Path
from typing import Any

from config.env import env_flag
from db.import_contract_awards import import_contract_awards
from scraper.bcbid import scrape_bcbid_tenders
from scraper.bcbid_auth import BcbidSessionExpiredError
from scraper.building_permits import scrape_building_permits
from scraper.commercial import scrape_commercial_tenders
from scraper.config import OUTPUT_CSV, OUTPUT_JSON
from scraper.federal import scrape_federal_tenders
from scraper.linkedin_signals import scrape_linkedin_signals
from scraper.merx_architecture import scrape_merx_architecture_tenders
from scraper.news_signals import scrape_news_signals
from scraper.reddit_signals import scrape_reddit_signals
from scraper.tender_merge import load_tenders_from_csv, merge_tenders_by_url, split_tenders_by_source
from scraper.utils import create_session, save_tenders


def _bcbid_enabled() -> bool:
    return not env_flag("PIPELINE_SKIP_BCBID")


def _scrape_bcbid_or_empty(session) -> tuple[list, str | None]:
    if not _bcbid_enabled():
        return [], "PIPELINE_SKIP_BCBID=true"
    try:
        return scrape_bcbid_tenders(session), None
    except BcbidSessionExpiredError as exc:
        print(f"[BC Bid] Failed: {exc}")
        return [], f"session_expired: {exc.reason}"
    except Exception as exc:
        print(f"[BC Bid] Failed: {exc}")
        return [], str(exc)


def run_federal_scraper() -> dict[str, Any]:
    """Scrape CanadaBuys federal tenders and merge BC Bid provincial tenders into tenders.csv."""
    session = create_session()
    federal_tenders = scrape_federal_tenders(session)
    bcbid_tenders, bcbid_error = _scrape_bcbid_or_empty(session)
    merged = merge_tenders_by_url(federal_tenders, bcbid_tenders)
    save_tenders(merged, OUTPUT_CSV, OUTPUT_JSON)

    result: dict[str, Any] = {
        "tenders_saved": len(merged),
        "federal_saved": len(federal_tenders),
        "bcbid_saved": len(bcbid_tenders),
    }
    if bcbid_error:
        result["bcbid_error"] = bcbid_error
    return result


def run_bcbid_scraper() -> dict[str, Any]:
    """Dedicated BC Bid scrape: refresh provincial rows and merge with existing federal tenders.csv."""
    if not _bcbid_enabled():
        return {"skipped": True, "reason": "PIPELINE_SKIP_BCBID=true"}

    session = create_session()
    csv_path = Path(OUTPUT_CSV)
    existing = load_tenders_from_csv(csv_path)
    federal_tenders, _old_bcbid = split_tenders_by_source(existing)

    bcbid_tenders, bcbid_error = _scrape_bcbid_or_empty(session)
    merged = merge_tenders_by_url(federal_tenders, bcbid_tenders)
    save_tenders(merged, OUTPUT_CSV, OUTPUT_JSON)

    result: dict[str, Any] = {
        "tenders_saved": len(merged),
        "federal_preserved": len(federal_tenders),
        "bcbid_saved": len(bcbid_tenders),
    }
    if bcbid_error:
        result["bcbid_error"] = bcbid_error
        result["federal_only_fallback"] = len(federal_tenders) > 0
    return result


def run_merx_arch_scraper() -> dict[str, Any]:
    session = create_session()
    tenders = scrape_merx_architecture_tenders(session)
    return {"tenders_saved": len(tenders)}


def run_commercial_scraper() -> dict[str, Any]:
    session = create_session()
    tenders = scrape_commercial_tenders(session)
    return {"tenders_saved": len(tenders)}


def run_building_permits_scraper() -> dict[str, Any]:
    if env_flag("PIPELINE_SKIP_BUILDING_PERMITS"):
        return {"skipped": True, "reason": "PIPELINE_SKIP_BUILDING_PERMITS=true"}
    permits_saved = scrape_building_permits()
    return {"permits_saved": permits_saved}


def run_reddit_scraper() -> dict[str, Any]:
    signals = scrape_reddit_signals()
    return {"signals_saved": len(signals)}


def run_news_scraper() -> dict[str, Any]:
    signals = scrape_news_signals()
    return {"signals_saved": len(signals)}


def run_linkedin_scraper() -> dict[str, Any]:
    signals = scrape_linkedin_signals()
    return {"signals_saved": len(signals)}


def run_contract_awards_scraper() -> dict[str, Any]:
    from db.connection import get_session, init_db

    init_db()
    session = get_session()
    try:
        return import_contract_awards(session)
    finally:
        session.close()
