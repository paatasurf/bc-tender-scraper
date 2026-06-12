"""Daily pipeline orchestration.

Scheduled path (production, no manual intervention):
  APScheduler (api.main lifespan)
    → pipeline.scheduler._scheduled_pipeline_run()
    → pipeline.executor.start_pipeline_subprocess()
    → run_pipeline.py (file lock)
    → pipeline.run.run_pipeline()
       1. scraper.main.run() — federal/MERX/commercial tenders, permits, news, …
       2. db.import_csv.import_all_csvs()
       3. db.import_contract_awards.import_contract_awards()
       4. pipeline.ai_scoring.score_unscored_tenders() (optional)
       5. pipeline.company_intelligence.run_company_intelligence()
       6. pipeline.arch_company_intelligence.run_arch_company_intelligence()
"""

from config.env import env_flag
from db.connection import get_session, init_db
from db.import_csv import import_all_csvs
from db.import_contract_awards import import_contract_awards
from pipeline.ai_scoring import score_unscored_tenders
from pipeline.arch_company_intelligence import run_arch_company_intelligence
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
        print("[Pipeline] Importing contract awards...")
        import_contract_awards(session)
        print("[Pipeline] Refreshing company award stats...")
        from pipeline.refresh_company_award_stats import refresh_company_award_stats

        refresh_company_award_stats(session)
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

    print("[Pipeline] Running architecture company intelligence...")
    session = get_session()
    try:
        run_arch_company_intelligence(session)
    except Exception as exc:
        print(f"[Pipeline] Architecture company intelligence failed: {exc}")
    finally:
        session.close()

    return scrape_status
