#!/usr/bin/env python3
"""Run a resumable authenticated LinkedIn batch (default: 50 companies)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.linkedin.batch_runner import run_authenticated_batch  # noqa: E402
from research.linkedin.paths import BATCH_REPORT_JSON, BATCH_REPORT_MD, PROGRESS_JSON  # noqa: E402
from research.linkedin.session import ProfileExpiredError, SessionExpiredError  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Authenticated LinkedIn batch scrape with cache + resume (research only)."
    )
    parser.add_argument(
        "--offset",
        type=int,
        default=None,
        help="Start index in queue (default: auto-resume from progress.json).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Companies per batch (default: 50).",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Re-scrape even when per-company cache exists.",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=2.0,
        help="Seconds between scrape requests (default: 2.0).",
    )
    parser.add_argument(
        "--headless",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Run browser headless (default: true). Use --no-headless to debug.",
    )
    parser.add_argument(
        "--use-session-fallback",
        action="store_true",
        help="Force storageState session.json instead of persistent profile.",
    )
    parser.add_argument(
        "--include-all-odbus",
        action="store_true",
        help="Include all ODB BC rows, not only NAICS-23 construction.",
    )
    args = parser.parse_args()

    try:
        report = run_authenticated_batch(
            offset=args.offset,
            limit=args.limit,
            refresh=args.refresh,
            delay_seconds=args.delay,
            headless=args.headless,
            force_session=args.use_session_fallback,
            bc_construction_only=not args.include_all_odbus,
        )
    except (ProfileExpiredError, SessionExpiredError):
        return 1
    except RuntimeError as exc:
        print(f"[batch] {exc}", flush=True)
        return 1

    if report.get("message"):
        print(f"[batch] {report['message']}", flush=True)
        return 0

    print(
        "[batch] "
        f"processed={report.get('processed')} "
        f"ok={report.get('scraped_ok')} "
        f"cached={report.get('cached_skipped')} "
        f"failed={report.get('failed_permanent', 0) + report.get('failed_transient', 0)}",
        flush=True,
    )
    print(f"[batch] next_offset={report.get('next_offset')} remaining={report.get('remaining')}", flush=True)
    print(f"[batch] wrote {PROGRESS_JSON}", flush=True)
    print(f"[batch] wrote {BATCH_REPORT_JSON}", flush=True)
    print(f"[batch] wrote {BATCH_REPORT_MD}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
