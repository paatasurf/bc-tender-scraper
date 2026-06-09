from __future__ import annotations

import config.env  # noqa: F401  # ensure env is loaded for scheduler/background runs

from db.connection import get_session, init_db
from db.import_csv import import_all_csvs
from pipeline.ai_scoring import score_unscored_tenders
from scraper.main import run as run_scrapers


def run_pipeline() -> int:
    print("[Pipeline] Running scrapers...")
    scrape_status = run_scrapers()

    print("[Pipeline] Importing CSV data into database...")
    init_db()
    session = get_session()
    try:
        import_all_csvs(session)
        score_unscored_tenders(session)
    finally:
        session.close()

    print("[Pipeline] Complete")
    return scrape_status
