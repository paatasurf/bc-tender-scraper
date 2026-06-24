from __future__ import annotations

from pathlib import Path
from typing import Any

from config.env import env_flag
from db.import_contract_awards import import_contract_awards
from scraper.building_permits import scrape_building_permits
from scraper.commercial import scrape_commercial_tenders
from scraper.config import OUTPUT_CSV, OUTPUT_JSON
from scraper.federal import scrape_federal_tenders
from scraper.linkedin_signals import scrape_linkedin_signals
from scraper.merx_architecture import scrape_merx_architecture_tenders
from scraper.merx_open import scrape_merx_open_tenders
from scraper.news_signals import scrape_news_signals
from scraper.reddit_signals import scrape_reddit_signals
from scraper.tender_merge import load_tenders_from_csv, merge_tenders_by_url, split_tenders_by_source
from scraper.utils import create_session, save_tenders


def _merx_open_enabled() -> bool:
    return not env_flag("PIPELINE_SKIP_MERX")


def _scrape_merx_open_or_empty(session) -> tuple[list, str | None]:
    if not _merx_open_enabled():
        return [], "PIPELINE_SKIP_MERX=true"
    try:
        return scrape_merx_open_tenders(session), None
    except Exception as exc:
        print(f"[MERX Open] Failed: {exc}")
        return [], str(exc)


def run_federal_scraper() -> dict[str, Any]:
    """Scrape CanadaBuys federal tenders and merge MERX BC open tenders into tenders.csv."""
    session = create_session()
    federal_tenders = scrape_federal_tenders(session)
    merx_tenders, merx_error = _scrape_merx_open_or_empty(session)
    merged = merge_tenders_by_url(federal_tenders, merx_tenders)
    save_tenders(merged, OUTPUT_CSV, OUTPUT_JSON)

    result: dict[str, Any] = {
        "tenders_saved": len(merged),
        "federal_saved": len(federal_tenders),
        "merx_saved": len(merx_tenders),
    }
    if merx_error:
        result["merx_error"] = merx_error
    return result


def run_merx_scraper() -> dict[str, Any]:
    """Dedicated MERX open scrape: refresh provincial rows and merge with existing federal tenders.csv."""
    if not _merx_open_enabled():
        return {"skipped": True, "reason": "PIPELINE_SKIP_MERX=true"}

    session = create_session()
    csv_path = Path(OUTPUT_CSV)
    existing = load_tenders_from_csv(csv_path)
    federal_tenders, _old_provincial = split_tenders_by_source(existing)

    merx_tenders, merx_error = _scrape_merx_open_or_empty(session)
    merged = merge_tenders_by_url(federal_tenders, merx_tenders)
    save_tenders(merged, OUTPUT_CSV, OUTPUT_JSON)

    result: dict[str, Any] = {
        "tenders_saved": len(merged),
        "federal_preserved": len(federal_tenders),
        "merx_saved": len(merx_tenders),
    }
    if merx_error:
        result["merx_error"] = merx_error
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


def run_surrey_permits_scraper(*, days: int | None = None) -> dict[str, Any]:
    from scraper.surrey_permits import DEFAULT_INCREMENTAL_DAYS, scrape_surrey_permits

    return scrape_surrey_permits(
        days=DEFAULT_INCREMENTAL_DAYS if days is None else days,
        persist=True,
    )


def run_burnaby_permits_scraper(*, days: int | None = None) -> dict[str, Any]:
    from scraper.burnaby_permits import scrape_burnaby_permits

    return scrape_burnaby_permits(days=days, persist=True)


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
