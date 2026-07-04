#!/usr/bin/env python3
"""Load Enterprise Seed + ODB primary into market_registry (Class C)."""

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
from pipeline.market_registry.load import (
    DEFAULT_COMPANY_ID_LOOKUP_PATH,
    DEFAULT_SEED_PATH,
    apply_market_registry_load,
    plan_market_registry_load,
)

_SCRIPT = Path(__file__).name
DEFAULT_DRY_RUN_ARTIFACT = ROOT / "exports" / "market_registry_load_dryrun_class_c.json"


def _build_dry_run_report(
    session,
    *,
    seed_path: Path,
    company_id_lookup_path: Path,
    artifact_path: Path,
) -> dict:
    plan = plan_market_registry_load(
        session,
        seed_path=seed_path,
        company_id_lookup_path=company_id_lookup_path,
    )
    payload = {
        "operation": "market_registry_load",
        "class": "C",
        "dry_run": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git_commit_sha": get_git_commit_sha(),
        "artifact_path": str(artifact_path),
        "seed_file": str(seed_path),
        "company_id_lookup_file": str(company_id_lookup_path),
        "planned_mutations": {
            "destructive_delete": False,
            "ingest_batch_id": plan["ingest_batch_id_planned"],
            "rows_by_source": plan["rows_by_source"],
            "rows_total": plan["rows_total_planned"],
            "supersede_previous_active_batches": True,
            "rows_superseded_estimated": plan["rows_superseded_estimated"],
        },
        "load_plan": plan,
        "apply_command_preview": (
            "python scripts/run_market_registry_load.py "
            f"--seed \"{seed_path}\" --apply --allow-production"
        ),
    }
    return attach_dry_run_provenance(payload, session)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Load Enterprise Seed + ODB primary into market_registry (Class C, no DDL).",
    )
    add_production_safety_args(parser)
    parser.add_argument(
        "--seed",
        type=Path,
        default=DEFAULT_SEED_PATH,
        help=f"Enterprise seed JSON (default: {DEFAULT_SEED_PATH.name})",
    )
    parser.add_argument(
        "--company-id-lookup",
        type=Path,
        default=DEFAULT_COMPANY_ID_LOOKUP_PATH,
        help="Optional DB-enriched seed JSON for tenderscope_company_id by seed_id",
    )
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
        help="Upsert rows and supersede prior active batch (requires fresh dry-run artifact).",
    )
    args = parser.parse_args()

    if args.apply and args.dry_run is not None:
        parser.error("Use either --dry-run or --apply, not both.")

    seed_path = args.seed.resolve()
    lookup_path = args.company_id_lookup.resolve()

    if args.apply:
        guard_destructive_db_from_args(
            args,
            script_name=_SCRIPT,
            operation="market registry load",
            nominal_class=SafetyClass.C,
        )
        session = get_session()
        try:
            verify_dry_run_artifact(session=session, report_path=DEFAULT_DRY_RUN_ARTIFACT)
            result = apply_market_registry_load(
                session,
                seed_path=seed_path,
                company_id_lookup_path=lookup_path,
            )
        finally:
            session.close()
        print("[Market Registry Load] Apply complete:")
        for key, value in result.items():
            print(f"  {key}: {value}")
        return

    artifact_path = Path(args.dry_run) if args.dry_run is not None else DEFAULT_DRY_RUN_ARTIFACT
    guard_readonly_db_from_args(args, script_name=_SCRIPT)
    session = get_session()
    try:
        report = _build_dry_run_report(
            session,
            seed_path=seed_path,
            company_id_lookup_path=lookup_path,
            artifact_path=artifact_path,
        )
    finally:
        session.close()

    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(json.dumps(report, indent=2, default=str))
    print(f"\nWrote {artifact_path}")


if __name__ == "__main__":
    main()
