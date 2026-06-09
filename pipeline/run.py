from __future__ import annotations

import config.env  # noqa: F401  # ensure env is loaded for scheduler/background runs

from config.env import env_flag
from db.connection import get_session, init_db
from db.import_csv import import_all_csvs
from pipeline.ai_scoring import score_unscored_tenders
from pipeline.company_intelligence import run_company_intelligence
from scraper.main import run as run_scrapers


def run_pipeline() -> int:
    print("[Pipeline] Running scrapers...")
    scrape_status = run_scrapers()

    print("[Pipeline] Importing CSV data into database...")
    init_db()
    session = get_session()
    try:
        import_all_csvs(session)
        if env_flag("PIPELINE_SKIP_AI_SCORING"):
            print("[Pipeline] Skipping AI scoring (PIPELINE_SKIP_AI_SCORING=true)")
        else:
            score_unscored_tenders(session)
    finally:
        session.close()

    print("[Pipeline] Complete")

    print("[Pipeline] Running company intelligence...")
    session = get_session()
    try:
        run_company_intelligence(session)
    except Exception as exc:
        print(f"[Pipeline] Company intelligence failed: {exc}")
    finally:
        session.close()

    return scrape_status
