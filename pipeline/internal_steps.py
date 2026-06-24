from __future__ import annotations

from typing import Any

from config.env import env_flag
from db.connection import get_session, init_db
from db.import_contract_awards import import_contract_awards
from db.import_csv import import_all_csvs
from pipeline.ai_scoring import score_unscored_tenders
from pipeline.arch_company_intelligence import run_arch_company_intelligence
from pipeline.company_intelligence import run_company_intelligence


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


def run_arch_google_places_step() -> dict[str, Any]:
    from pipeline.arch_company_intelligence import enrich_arch_companies_google
    from pipeline.scrape_arch_companies_google import scrape_arch_companies_google

    session = get_session()
    try:
        scraped = scrape_arch_companies_google(session)
        enriched = enrich_arch_companies_google(session)
        return {"scraped": scraped, "enriched": enriched}
    finally:
        session.close()
