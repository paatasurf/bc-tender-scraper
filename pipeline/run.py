"""Daily pipeline orchestration.

Scheduled path (production, no manual intervention):
  APScheduler (api.main lifespan)
    → pipeline.scheduler._scheduled_pipeline_run()
    → pipeline.executor.start_pipeline_subprocess()
    → run_pipeline.py (file lock)
    → pipeline.run.run_pipeline()
       1. pipeline.tender_data_pipeline.run_tender_data_pipeline()
          — tender scrapers → CSV verify → import → DB verify
       2. pipeline.ai_scoring.score_unscored_tenders() (optional)
       3. pipeline.company_intelligence.run_company_intelligence()
       4. pipeline.arch_company_intelligence.run_arch_company_intelligence()
"""

from config.env import env_flag
from db.connection import get_session, init_db
from pipeline.ai_scoring import score_unscored_tenders
from pipeline.arch_company_intelligence import run_arch_company_intelligence
from pipeline.company_intelligence import run_company_intelligence
from pipeline.tender_data_pipeline import run_tender_data_pipeline


def run_pipeline() -> int:
    print("[Pipeline] Starting deterministic tender data pipeline...")
    try:
        summary = run_tender_data_pipeline()
    except Exception as exc:
        print(f"[Pipeline] Tender data pipeline failed: {exc}")
        return 1

    print(f"[Pipeline] Tender data pipeline summary: {summary.get('status')}")

    print("[Pipeline] Post-import enrichment...")
    init_db()
    session = get_session()
    try:
        if env_flag("PIPELINE_SKIP_AI_SCORING"):
            print("[Pipeline] Skipping AI scoring (PIPELINE_SKIP_AI_SCORING=true)")
        else:
            score_unscored_tenders(session)
    finally:
        session.close()

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

    print("[Pipeline] Complete")
    return 0
