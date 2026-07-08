#!/usr/bin/env python3
"""Run first 500-company authenticated LinkedIn validation (resumable batches of 50)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.linkedin.batch_runner import run_authenticated_batch  # noqa: E402
from research.linkedin.paths import VALIDATION_500_JSON, VALIDATION_500_MD  # noqa: E402
from research.linkedin.session import ProfileExpiredError, SessionExpiredError, profile_is_initialized  # noqa: E402
from research.linkedin.validation_500_report import (  # noqa: E402
    generate_validation_500_report,
    write_validation_500_report,
)


def run_batches(
    *,
    sample_size: int = 500,
    chunk_size: int = 50,
    delay: float = 2.0,
    headless: bool = True,
    refresh: bool = False,
) -> None:
    processed = 0
    while processed < sample_size:
        limit = min(chunk_size, sample_size - processed)
        print(f"[validation-500] batch offset={processed} limit={limit}", flush=True)
        report = run_authenticated_batch(
            offset=processed,
            limit=limit,
            refresh=refresh,
            delay_seconds=delay,
            headless=headless,
        )
        batch_ok = report.get("scraped_ok", 0)
        print(
            f"[validation-500] chunk ok={batch_ok} cached={report.get('cached_skipped', 0)} "
            f"failed={report.get('failed_permanent', 0) + report.get('failed_transient', 0)}",
            flush=True,
        )
        processed += limit


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample-size", type=int, default=500)
    parser.add_argument("--chunk-size", type=int, default=50)
    parser.add_argument("--delay", type=float, default=2.0)
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--report-only", action="store_true", help="Skip scrape; generate report from cache.")
    parser.add_argument("--headless", action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args()

    if not args.report_only:
        if not profile_is_initialized():
            print(
                "[validation-500] No browser profile. Run:\n"
                "  python research/linkedin/scripts/login_profile.py\n"
                "Then re-run this command.",
                flush=True,
            )
            report = generate_validation_500_report(sample_size=args.sample_size)
            write_validation_500_report(report)
            print(f"[validation-500] wrote {VALIDATION_500_JSON}", flush=True)
            print(f"[validation-500] wrote {VALIDATION_500_MD}", flush=True)
            return 1

        try:
            run_batches(
                sample_size=args.sample_size,
                chunk_size=args.chunk_size,
                delay=args.delay,
                headless=args.headless,
                refresh=args.refresh,
            )
        except (ProfileExpiredError, SessionExpiredError):
            report = generate_validation_500_report(sample_size=args.sample_size)
            write_validation_500_report(report)
            return 1
        except RuntimeError as exc:
            print(f"[validation-500] {exc}", flush=True)
            return 1

    report = generate_validation_500_report(sample_size=args.sample_size)
    write_validation_500_report(report)
    m = report.get("metrics") or {}
    print(
        f"[validation-500] verified={m.get('linkedin_pages_found')} "
        f"success_rate={m.get('success_rate_sample_pct')}% "
        f"avg_gain=+{m.get('avg_completeness_gain')}",
        flush=True,
    )
    print(f"[validation-500] wrote {VALIDATION_500_JSON}", flush=True)
    print(f"[validation-500] wrote {VALIDATION_500_MD}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
