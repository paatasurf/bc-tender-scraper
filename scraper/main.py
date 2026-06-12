from __future__ import annotations

from scraper.runners import (
    run_building_permits_scraper,
    run_commercial_scraper,
    run_federal_scraper,
    run_linkedin_scraper,
    run_merx_arch_scraper,
    run_news_scraper,
    run_reddit_scraper,
)


def run() -> int:
    errors: list[str] = []

    print("Starting BC construction data scrapers")
    print("=" * 60)

    steps = (
        ("Federal tenders", run_federal_scraper),
        ("MERX architecture tenders", run_merx_arch_scraper),
        ("Commercial tenders", run_commercial_scraper),
        ("Building permits", run_building_permits_scraper),
        ("Reddit signals", run_reddit_scraper),
        ("News signals", run_news_scraper),
        ("LinkedIn signals", run_linkedin_scraper),
    )

    for label, runner in steps:
        try:
            counts = runner()
            if counts.get("skipped"):
                print(f"[{label}] Skipped: {counts.get('reason', 'disabled')}")
            else:
                print(f"[{label}] Complete: {counts}")
        except Exception as exc:
            errors.append(f"{label}: {exc}")
            print(f"[{label}] Failed: {exc}")

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
