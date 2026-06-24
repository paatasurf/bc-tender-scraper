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
from pipeline.arch_company_intelligence import run_arch_company_intelligence
from pipeline.company_intelligence import run_company_intelligence
from pipeline.internal_steps import (
    run_ai_scoring_step,
    run_import_contract_awards_step,
    run_import_step,
)
from pipeline.runs import execute_tracked_step, new_run_id
from scraper.main import run_with_summary as run_scrapers


def _step_failed(result: dict) -> bool:
    return result.get("status") == "failed"


def _print_step_failure(step: str, result: dict) -> None:
    print(f"[Pipeline] {step} failed: {result.get('error') or 'unknown error'}")


def _run_refresh_company_award_stats_step() -> dict:
    init_db()
    session = get_session()
    try:
        from pipeline.refresh_company_award_stats import refresh_company_award_stats

        return refresh_company_award_stats(session)
    finally:
        session.close()


def run_pipeline() -> int:
    run_id = new_run_id()

    print("[Pipeline] Running scrapers...")
    scrape_result = execute_tracked_step("daily-scrapers", run_scrapers, run_id=run_id)
    scrape_status = 1 if _step_failed(scrape_result) else 0

    print("[Pipeline] Importing CSV data into database...")
    import_result = execute_tracked_step("import-csvs", run_import_step, run_id=run_id)
    if _step_failed(import_result):
        _print_step_failure("Importing CSV data", import_result)
        return 1

    print("[Pipeline] Importing contract awards...")
    awards_result = execute_tracked_step(
        "import-contract-awards",
        run_import_contract_awards_step,
        run_id=run_id,
    )
    if _step_failed(awards_result):
        _print_step_failure("Importing contract awards", awards_result)
        return 1

    print("[Pipeline] Refreshing company award stats...")
    stats_result = execute_tracked_step(
        "refresh-company-award-stats",
        _run_refresh_company_award_stats_step,
        run_id=run_id,
    )
    if _step_failed(stats_result):
        _print_step_failure("Refreshing company award stats", stats_result)
        return 1

    if env_flag("PIPELINE_SKIP_AI_SCORING"):
        print("[Pipeline] Skipping AI scoring (PIPELINE_SKIP_AI_SCORING=true)")
    ai_result = execute_tracked_step("ai-scoring", run_ai_scoring_step, run_id=run_id)
    if _step_failed(ai_result):
        _print_step_failure("AI scoring", ai_result)
        return 1

    print("[Pipeline] Complete")

    print("[Pipeline] Running company intelligence...")
    session = get_session()
    try:
        company_result = execute_tracked_step(
            "company-intelligence",
            lambda: run_company_intelligence(session),
            run_id=run_id,
        )
        if _step_failed(company_result):
            _print_step_failure("Company intelligence", company_result)
    except Exception as exc:
        print(f"[Pipeline] Company intelligence failed: {exc}")
    finally:
        session.close()

    print("[Pipeline] Running architecture company intelligence...")
    session = get_session()
    try:
        arch_result = execute_tracked_step(
            "arch-company-intelligence",
            lambda: run_arch_company_intelligence(session),
            run_id=run_id,
        )
        if _step_failed(arch_result):
            _print_step_failure("Architecture company intelligence", arch_result)
    except Exception as exc:
        print(f"[Pipeline] Architecture company intelligence failed: {exc}")
    finally:
        session.close()

    return scrape_status
