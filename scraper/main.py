from __future__ import annotations

from collections.abc import Callable
from typing import Any

from scraper.runners import (
    run_building_permits_scraper,
    run_commercial_scraper,
    run_federal_scraper,
    run_linkedin_scraper,
    run_merx_arch_scraper,
    run_news_scraper,
    run_reddit_scraper,
)


SCRAPER_STEPS: tuple[tuple[str, Callable[[], dict[str, Any]]], ...] = (
    ("Federal + MERX BC tenders", run_federal_scraper),
    ("MERX architecture tenders", run_merx_arch_scraper),
    ("Commercial tenders", run_commercial_scraper),
    ("Building permits", run_building_permits_scraper),
    ("Reddit signals", run_reddit_scraper),
    ("News signals", run_news_scraper),
    ("LinkedIn signals", run_linkedin_scraper),
)


def run_with_summary() -> dict[str, Any]:
    errors: list[str] = []
    step_results: dict[str, dict[str, Any]] = {}

    print("Starting BC construction data scrapers")
    print("=" * 60)

    for label, runner in SCRAPER_STEPS:
        try:
            counts = runner()
            if counts.get("skipped"):
                step_results[label] = {**counts, "status": "skipped"}
                print(f"[{label}] Skipped: {counts.get('reason', 'disabled')}")
            else:
                step_results[label] = {**counts, "status": "success"}
                print(f"[{label}] Complete: {counts}")
        except Exception as exc:
            errors.append(f"{label}: {exc}")
            step_results[label] = {"status": "failed", "error": str(exc)}
            print(f"[{label}] Failed: {exc}")

    print("=" * 60)
    if errors:
        print("Completed with errors:")
        for error in errors:
            print(f"  - {error}")
        return {
            "status": "failed",
            "steps": step_results,
            "errors": errors,
            "failed_steps": len(errors),
        }

    print("All scrapers completed successfully.")
    return {
        "status": "success",
        "steps": step_results,
        "errors": [],
        "failed_steps": 0,
    }


def run() -> int:
    summary = run_with_summary()
    if summary["status"] == "failed":
        return 1
    return 0


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
