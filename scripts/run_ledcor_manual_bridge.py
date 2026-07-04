#!/usr/bin/env python3
"""Ledcor manual bridge — Class C dry-run / apply (3046 + 302683 → 8756)."""

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
from pipeline.company_manual_bridge import apply_ledcor_manual_bridge_plan, build_ledcor_manual_bridge_plan

_SCRIPT = Path(__file__).name
DEFAULT_DRY_RUN_ARTIFACT = ROOT / "exports" / "ledcor_manual_bridge_dryrun_class_c.json"


def _build_dry_run_report(session, *, artifact_path: Path) -> dict:
    plan = build_ledcor_manual_bridge_plan(session)
    payload = {
        "operation": "ledcor_manual_bridge",
        "class": "C",
        "dry_run": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git_commit_sha": get_git_commit_sha(),
        "artifact_path": str(artifact_path),
        "planned_mutations": {
            "destructive_delete": False,
            "alias_reclassifications": len(plan.alias_specs),
            "canonical_aggregate_recompute": 1,
            "excluded_unchanged": len(plan.excluded_unchanged),
            "validation_errors": plan.validation_errors,
        },
        "plan": plan.to_report_dict(),
        "apply_command_preview": (
            "python scripts/run_ledcor_manual_bridge.py --use-production --apply --allow-production"
        ),
    }
    return attach_dry_run_provenance(payload, session)


def main() -> None:
    parser = argparse.ArgumentParser(description="Ledcor manual bridge (Class C)")
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
        help="Apply bridge plan (requires fresh dry-run artifact).",
    )
    args = parser.parse_args()

    if args.apply and args.dry_run is not None:
        parser.error("Use either --dry-run or --apply, not both.")

    if args.apply:
        guard_destructive_db_from_args(
            args,
            script_name=_SCRIPT,
            operation="Ledcor manual bridge",
            nominal_class=SafetyClass.C,
        )
        session = get_session()
        try:
            verify_dry_run_artifact(session=session, report_path=DEFAULT_DRY_RUN_ARTIFACT)
            with DEFAULT_DRY_RUN_ARTIFACT.open(encoding="utf-8") as handle:
                plan_dict = json.load(handle)["plan"]
            plan = build_ledcor_manual_bridge_plan(session)
            if plan.validation_errors:
                raise SystemExit(f"Live validation failed: {plan.validation_errors}")
            result = apply_ledcor_manual_bridge_plan(session, plan)
        finally:
            session.close()
        print("[Ledcor Manual Bridge] Apply complete:")
        print(f"  merge_run_id: {result['merge_run_id']}")
        print(f"  status: {result['status']}")
        print(f"  permits remapped: {result['fk_remap'].get('updated', 0)}")
        return

    artifact_path = Path(args.dry_run) if args.dry_run is not None else DEFAULT_DRY_RUN_ARTIFACT
    guard_readonly_db_from_args(args, script_name=_SCRIPT)
    session = get_session()
    try:
        report = _build_dry_run_report(session, artifact_path=artifact_path)
    finally:
        session.close()

    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(json.dumps(report, indent=2, default=str))
    print(f"\nWrote {artifact_path}")


if __name__ == "__main__":
    main()
