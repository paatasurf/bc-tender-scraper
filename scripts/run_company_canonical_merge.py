"""Run deterministic company canonical merge (report / apply / rollback).

Examples:
  python scripts/run_company_canonical_merge.py --report exports/company_merge_report.json
  python scripts/run_company_canonical_merge.py --apply
  python scripts/run_company_canonical_merge.py --rollback 1
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db.connection import get_session, init_db
from pipeline.company_canonical_merge import (
    apply_merge_plan,
    build_merge_plan,
    format_merge_report_summary,
    rollback_merge_run,
    write_merge_report,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Deterministic company canonical merge")
    parser.add_argument(
        "--report",
        metavar="PATH",
        help="Write merge report JSON to PATH (default: dry-run only, no DB writes)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply merge plan to local database (creates rollback snapshots)",
    )
    parser.add_argument(
        "--rollback",
        type=int,
        metavar="RUN_ID",
        help="Rollback a previously applied merge run",
    )
    args = parser.parse_args()

    if not args.apply and not args.report and args.rollback is None:
        parser.error("Specify --report PATH, --apply, or --rollback RUN_ID")

    init_db()
    session = get_session()
    try:
        if args.rollback is not None:
            result = rollback_merge_run(session, args.rollback)
            print(f"Rollback complete: {result}")
            return

        plan = build_merge_plan(session)
        print(format_merge_report_summary(plan))

        if args.apply:
            run = apply_merge_plan(session, plan)
            print(f"\nApplied merge run id={run.id} status={run.status}")
            print("Rollback with: python scripts/run_company_canonical_merge.py --rollback", run.id)
            report_path = args.report or "exports/company_canonical_merge_report.json"
            report_file = Path(report_path)
            report_file.parent.mkdir(parents=True, exist_ok=True)
            write_merge_report(plan, str(report_file))
            print(f"\nReport written: {report_file.resolve()}")
        else:
            report_path = args.report or "exports/company_canonical_merge_report.json"
            report_file = Path(report_path)
            report_file.parent.mkdir(parents=True, exist_ok=True)
            write_merge_report(plan, str(report_file))
            print(f"\nReport written: {report_file.resolve()}")
            print("\nDry run only — no database changes. Re-run with --apply to mutate.")
    finally:
        session.close()


if __name__ == "__main__":
    main()
