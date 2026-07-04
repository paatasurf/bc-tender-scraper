"""Run deterministic company canonical merge (report / apply / rollback).

Examples:
  python scripts/run_company_canonical_merge.py --report exports/company_merge_report.json
  python scripts/run_company_canonical_merge.py --apply
  python scripts/run_company_canonical_merge.py --rollback 1
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db.classification import SafetyClass
from db.connection import get_session, init_db
from db.db_safety import add_production_safety_args, guard_destructive_db_from_args
from db.merge_dry_run_provenance import attach_dry_run_provenance, verify_dry_run_artifact
from pipeline.company_canonical_merge import (
    apply_merge_plan,
    build_merge_plan,
    format_merge_report_summary,
    rollback_merge_run,
    write_merge_report,
)

_SCRIPT = Path(__file__).name
_DEFAULT_REPORT = "exports/company_canonical_merge_report.json"


def main() -> None:
    parser = argparse.ArgumentParser(description="Deterministic company canonical merge")
    add_production_safety_args(parser)
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

    if args.rollback is not None:
        guard_destructive_db_from_args(args, script_name=_SCRIPT, operation="merge rollback")
    else:
        guard_destructive_db_from_args(
            args,
            script_name=_SCRIPT,
            operation="merge apply" if args.apply else "merge dry-run",
            nominal_class=SafetyClass.C,
        )

    init_db()
    session = get_session()
    try:
        if args.rollback is not None:
            result = rollback_merge_run(session, args.rollback)
            print(f"Rollback complete: {result}")
            return

        plan = build_merge_plan(session)
        print(format_merge_report_summary(plan))

        report_path = Path(args.report or _DEFAULT_REPORT)
        report_file = report_path
        report_file.parent.mkdir(parents=True, exist_ok=True)

        if args.apply:
            verify_dry_run_artifact(session=session, report_path=report_file)
            run = apply_merge_plan(session, plan)
            print(f"\nApplied merge run id={run.id} status={run.status}")
            print("Rollback with: python scripts/run_company_canonical_merge.py --rollback", run.id)
            payload = attach_dry_run_provenance(plan.to_report_dict(), session)
            report_file.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
            print(f"\nReport written: {report_file.resolve()}")
        else:
            payload = attach_dry_run_provenance(plan.to_report_dict(), session)
            report_file.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
            print(f"\nReport written: {report_file.resolve()}")
            print("\nDry run only — no database changes. Re-run with --apply to mutate.")
    finally:
        session.close()


if __name__ == "__main__":
    main()
