from __future__ import annotations

from typing import Any

from config.env import env_flag
from db.import_contract_awards import import_contract_awards
from scraper.building_permits import scrape_building_permits
from scraper.commercial import scrape_commercial_tenders
from scraper.config import OUTPUT_CSV, OUTPUT_JSON
from scraper.federal import scrape_federal_tenders
from scraper.linkedin_signals import scrape_linkedin_signals
from scraper.news_signals import scrape_news_signals
from scraper.merx_architecture import scrape_merx_architecture_tenders
from scraper.reddit_signals import scrape_reddit_signals
from scraper.utils import create_session, save_tenders


def run_federal_scraper() -> dict[str, Any]:
    session = create_session()
    tenders = scrape_federal_tenders(session)
    save_tenders(tenders, OUTPUT_CSV, OUTPUT_JSON)
    return {"tenders_saved": len(tenders)}


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
