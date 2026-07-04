"""Run Scenario B parsed-identity canonical merge (dry-run / apply / post-audit).

Examples:
  python scripts/run_parsed_identity_canonical_merge.py --report exports/parsed_identity_merge_report.json --review-md docs/audits/PARSED_IDENTITY_MERGE_REVIEW.md
  python scripts/run_parsed_identity_canonical_merge.py --apply
  python scripts/run_parsed_identity_canonical_merge.py --post-audit --merge-run-id 2
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
from db.db_safety import (
    add_production_safety_args,
    guard_destructive_db_from_args,
    guard_readonly_db_from_args,
)
from db.merge_dry_run_provenance import attach_dry_run_provenance, verify_dry_run_artifact
from pipeline.parsed_identity_canonical_merge import (
    apply_parsed_identity_merge_plan,
    build_parsed_identity_merge_plan,
    build_post_apply_audit,
    format_parsed_identity_merge_summary,
    write_parsed_identity_review_markdown,
)

_SCRIPT = Path(__file__).name
_DEFAULT_REPORT = "exports/parsed_identity_merge_report.json"
_DEFAULT_REVIEW = "docs/audits/PARSED_IDENTITY_MERGE_REVIEW.md"


def main() -> None:
    parser = argparse.ArgumentParser(description="Scenario B parsed-identity canonical merge")
    add_production_safety_args(parser)
    parser.add_argument(
        "--report",
        metavar="PATH",
        nargs="?",
        const=_DEFAULT_REPORT,
        help=f"Write merge report JSON (default: {_DEFAULT_REPORT})",
    )
    parser.add_argument(
        "--review-md",
        metavar="PATH",
        nargs="?",
        const=_DEFAULT_REVIEW,
        help=f"Write full human review markdown (default: {_DEFAULT_REVIEW})",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply merge plan (requires fresh dry-run artifact)",
    )
    parser.add_argument(
        "--post-audit",
        action="store_true",
        help="Run post-apply conservation audit for a merge run",
    )
    parser.add_argument(
        "--merge-run-id",
        type=int,
        help="Merge run id for --post-audit",
    )
    args = parser.parse_args()

    if args.post_audit:
        guard_readonly_db_from_args(args, script_name=_SCRIPT)
    elif args.apply:
        guard_destructive_db_from_args(
            args,
            script_name=_SCRIPT,
            operation="parsed identity merge apply",
            nominal_class=SafetyClass.C,
        )
    elif getattr(args, "use_production", False):
        guard_readonly_db_from_args(args, script_name=_SCRIPT)
    else:
        guard_destructive_db_from_args(
            args,
            script_name=_SCRIPT,
            operation="parsed identity merge dry-run",
            nominal_class=SafetyClass.C,
        )

    if args.post_audit:
        if args.merge_run_id is None:
            parser.error("--post-audit requires --merge-run-id")
    elif not args.apply and not args.report and not args.review_md:
        parser.error("Specify --report, --review-md, --apply, or --post-audit")

    needs_init_db = args.apply or not getattr(args, "use_production", False)
    if needs_init_db:
        init_db()
    session = get_session()
    try:
        if args.post_audit:
            if args.merge_run_id is None:
                parser.error("--post-audit requires --merge-run-id")
            audit = build_post_apply_audit(session, merge_run_id=args.merge_run_id)
            out = Path("exports/parsed_identity_merge_post_audit.json")
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(audit, indent=2, default=str), encoding="utf-8")
            print(json.dumps(audit, indent=2, default=str))
            print(f"\nPost-audit written: {out.resolve()}")
            return

        plan = build_parsed_identity_merge_plan(session)
        print(format_parsed_identity_merge_summary(plan))

        report_path = Path(args.report or _DEFAULT_REPORT)
        report_path.parent.mkdir(parents=True, exist_ok=True)

        if args.apply:
            verify_dry_run_artifact(session=session, report_path=report_path)
            run = apply_parsed_identity_merge_plan(session, plan)
            print(f"\nApplied parsed identity merge run id={run.id} status={run.status}")
            payload = attach_dry_run_provenance(plan.to_report_dict(), session)
            report_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
            print(f"Report written: {report_path.resolve()}")
        else:
            payload = attach_dry_run_provenance(plan.to_report_dict(), session)
            report_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
            print(f"\nReport written: {report_path.resolve()}")

        if args.review_md is not False and (args.review_md or not args.apply):
            review_path = write_parsed_identity_review_markdown(
                plan,
                Path(args.review_md or _DEFAULT_REVIEW),
            )
            print(f"Full review list written: {review_path.resolve()} ({len(plan.group_reports)} groups)")

        if not args.apply:
            print("\nDry run only — no database changes. Re-run with --apply after review.")
    finally:
        session.close()


if __name__ == "__main__":
    main()
