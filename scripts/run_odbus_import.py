#!/usr/bin/env python3
"""Import Statistics Canada ODB CSV into odbus_reference (batch-versioned)."""

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

from db.connection import get_session, init_db
from db.db_safety import (
    add_production_safety_args,
    guard_destructive_db_from_args,
    guard_readonly_db_from_args,
    resolve_script_database_url,
)
from db.market_registry_constants import ODBUS_FILTER_MODES, ODBUS_FILTER_PRIMARY_NAICS23
from db.merge_dry_run_provenance import attach_dry_run_provenance, get_git_commit_sha
from pipeline.registry_verification.odbus_import import (
    assert_production_odbus_apply_allowed,
    csv_fingerprint,
    import_odbus_csv,
    odbus_reference_before_stats,
    plan_odbus_import,
    production_apply_authorized,
)

_SCRIPT = Path(__file__).name
DEFAULT_DRY_RUN_ARTIFACT = ROOT / "exports" / "odbus_import_primary_dryrun_class_d.json"


def _build_dry_run_report(
    session,
    *,
    csv_path: Path,
    filter_mode: str,
    artifact_path: Path,
) -> dict:
    plan = plan_odbus_import(csv_path, filter_mode=filter_mode)
    before = odbus_reference_before_stats(session)

    payload = {
        "operation": "odbus_reference_import",
        "class": "D",
        "dry_run": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git_commit_sha": get_git_commit_sha(),
        "artifact_path": str(artifact_path),
        "source_csv": csv_fingerprint(csv_path),
        "filter": {
            "mode": filter_mode,
            "production_apply_authorized": production_apply_authorized(filter_mode),
        },
        "production_before": {
            "odbus_reference": before,
        },
        "planned_mutations": {
            "destructive_delete": False,
            "ingest_batch_id": plan["ingest_batch_id_planned"],
            "source_observed_at": plan["source_observed_at"],
            "rows_upserted": plan["rows_upserted"],
            "rows_skipped_in_filter": plan["rows_skipped"],
            "supersede_previous_active_batches": True,
            "rows_superseded_estimated": before.get("active_count", 0),
            "net_active_delta_estimated": int(plan["rows_upserted"]) - int(before.get("active_count") or 0),
        },
        "import_plan": plan,
        "migration_022_required_before_apply": before.get("migration_022_pending", False),
        "apply_command_preview": (
            f"python scripts/run_odbus_import.py --filter {filter_mode} "
            f"--apply \"{csv_path}\" --allow-production"
        ),
    }
    return attach_dry_run_provenance(payload, session)


def main() -> None:
    parser = argparse.ArgumentParser(description="Import ODBus_v1.csv into odbus_reference.")
    add_production_safety_args(parser)
    parser.add_argument("csv_path", help="Path to ODBus_v1.csv (from ODBus_2023.zip).")
    parser.add_argument(
        "--filter",
        choices=sorted(ODBUS_FILTER_MODES),
        default=ODBUS_FILTER_PRIMARY_NAICS23,
        help="Row filter (default: primary_naics23). Only primary_naics23 may --apply on production.",
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
        help="Upsert filtered rows and supersede prior active batch (Class D).",
    )
    args = parser.parse_args()

    if args.apply and args.dry_run is not None:
        parser.error("Use either --dry-run or --apply, not both.")

    csv_path = Path(args.csv_path)
    filter_mode = args.filter

    if args.apply:
        guard_destructive_db_from_args(args, script_name=_SCRIPT, operation="odbus import")
        db_url = resolve_script_database_url(use_production=bool(args.allow_production))
        assert_production_odbus_apply_allowed(
            filter_mode=filter_mode,
            allow_production=bool(args.allow_production),
            database_url=db_url,
        )
        init_db()
        session = get_session()
        try:
            result = import_odbus_csv(session, csv_path, filter_mode=filter_mode)
        finally:
            session.close()
        print("[ODB Import] Apply complete:")
        for key, value in result.items():
            print(f"  {key}: {value}")
        return

    artifact_path = Path(args.dry_run) if args.dry_run is not None else DEFAULT_DRY_RUN_ARTIFACT
    guard_readonly_db_from_args(args, script_name=_SCRIPT)
    session = get_session()
    try:
        report = _build_dry_run_report(
            session,
            csv_path=csv_path,
            filter_mode=filter_mode,
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
