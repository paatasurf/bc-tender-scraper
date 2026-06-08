from __future__ import annotations

from scraper.bcbid_architecture import scrape_bcbid_architecture_tenders
from scraper.building_permits import scrape_building_permits
from scraper.config import OUTPUT_CSV, OUTPUT_JSON
from scraper.contract_awards import scrape_contract_awards
from scraper.federal import scrape_federal_tenders
from scraper.job_bank import scrape_job_bank_jobs
from scraper.reddit_signals import scrape_reddit_signals
from scraper.utils import create_session, save_tenders


def run() -> int:
    session = create_session()
    errors: list[str] = []

    print("Starting BC construction data scrapers")
    print("=" * 60)

    try:
        federal_tenders = scrape_federal_tenders(session)
        save_tenders(federal_tenders, OUTPUT_CSV, OUTPUT_JSON)
        print(f"[Federal] Saved {len(federal_tenders)} tenders to {OUTPUT_CSV}")
    except Exception as exc:
        errors.append(f"Federal tenders: {exc}")
        print(f"[Federal] Failed: {exc}")

    try:
        scrape_building_permits()
    except Exception as exc:
        errors.append(f"Building permits: {exc}")
        print(f"[Building Permits] Failed: {exc}")

    try:
        scrape_reddit_signals()
    except Exception as exc:
        errors.append(f"Reddit signals: {exc}")
        print(f"[Reddit] Failed: {exc}")

    try:
        scrape_contract_awards(session)
    except Exception as exc:
        errors.append(f"Contract awards: {exc}")
        print(f"[Contract Awards] Failed: {exc}")

    try:
        scrape_job_bank_jobs(session)
    except Exception as exc:
        errors.append(f"Job Bank jobs: {exc}")
        print(f"[Job Bank] Failed: {exc}")

    try:
        scrape_bcbid_architecture_tenders(session)
    except Exception as exc:
        errors.append(f"BC Bid architecture tenders: {exc}")
        print(f"[BC Bid Architecture] Failed: {exc}")

    print("=" * 60)
    if errors:
        print("Completed with errors:")
        for error in errors:
            print(f"  - {error}")
        return 1

    print("All scrapers completed successfully.")
    return 0


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
