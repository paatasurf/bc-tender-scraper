#!/usr/bin/env python3
"""Class D cleanup — remove Jul 3 test pollution (companies 572934, 572936–572950)."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config.env  # noqa: F401

from db.classification import SafetyClass
from db.connection import get_session
from db.db_safety import add_production_safety_args, guard_destructive_db_from_args, guard_readonly_db_from_args
from db.merge_dry_run_provenance import attach_dry_run_provenance, get_git_commit_sha, verify_dry_run_artifact
from pipeline.test_pollution_cleanup import apply_test_pollution_cleanup_plan, build_test_pollution_cleanup_plan

_SCRIPT = Path(__file__).name
DEFAULT_DRY_RUN_ARTIFACT = ROOT / "exports" / "test_pollution_cleanup_dryrun_class_d.json"


def _build_dry_run_report(session, *, artifact_path: Path) -> dict:
    plan = build_test_pollution_cleanup_plan(session)
    payload = {
        "operation": "test_pollution_cleanup",
        "class": "D",
        "dry_run": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git_commit_sha": get_git_commit_sha(),
        "artifact_path": str(artifact_path),
        "planned_mutations": {
            "destructive_delete": True,
            "blocked": plan.blocked,
            "validation_errors": plan.validation_errors,
        },
        "plan": plan.to_report_dict(),
        "apply_command_preview": (
            "python scripts/run_test_pollution_cleanup.py --use-production --apply --allow-production"
        ),
    }
    return attach_dry_run_provenance(payload, session)


def main() -> None:
    parser = argparse.ArgumentParser(description="Remove test pollution companies (Class D)")
    add_production_safety_args(parser)
    parser.add_argument(
        "--dry-run",
        nargs="?",
        const=str(DEFAULT_DRY_RUN_ARTIFACT),
        metavar="ARTIFACT",
        help="Write dry-run artifact JSON (default path if flag given without value).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply cleanup (requires fresh dry-run artifact).",
    )
    args = parser.parse_args()

    if args.apply and args.dry_run is not None:
        parser.error("Use either --dry-run or --apply, not both.")

    if args.apply:
        guard_destructive_db_from_args(
            args,
            script_name=_SCRIPT,
            operation="test pollution cleanup",
            nominal_class=SafetyClass.D,
        )
        session = get_session()
        try:
            verify_dry_run_artifact(session=session, report_path=DEFAULT_DRY_RUN_ARTIFACT)
            plan = build_test_pollution_cleanup_plan(session)
            if plan.blocked:
                print(json.dumps(plan.to_report_dict(), indent=2, default=str))
                raise SystemExit("Cleanup blocked — non-zero FK references.")
            result = apply_test_pollution_cleanup_plan(session, plan)
            print(json.dumps(result, indent=2, default=str))
        finally:
            session.close()
        return

    guard_readonly_db_from_args(args, script_name=_SCRIPT)
    session = get_session()
    try:
        report = _build_dry_run_report(session, artifact_path=DEFAULT_DRY_RUN_ARTIFACT)
    finally:
        session.close()

    DEFAULT_DRY_RUN_ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
    DEFAULT_DRY_RUN_ARTIFACT.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
