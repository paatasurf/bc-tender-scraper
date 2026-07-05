#!/usr/bin/env python3
"""Batch backfill CIP + dominant_sector for CI-eligible construction companies (Class C)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config.env  # noqa: F401

from db.classification import SafetyClass
from db.connection import check_db_connection, get_session
from db.db_safety import add_production_safety_args, guard_destructive_db_from_args, guard_readonly_db_from_args
from db.merge_dry_run_provenance import attach_dry_run_provenance, verify_dry_run_artifact
from pipeline.cip_backfill import backfill_company_cips
from pipeline.runs import execute_tracked_step

_SCRIPT = Path(__file__).name
STEP_NAME = "cip-backfill"
DEFAULT_DRY_RUN_ARTIFACT = ROOT / "exports" / "cip_backfill_dryrun_class_c.json"


def _build_dry_run_report(session, *, artifact_path: Path, sample_size: int | None, company_ids: list[int] | None) -> dict:
    report = backfill_company_cips(
        session,
        dry_run=True,
        sample_size=sample_size,
        company_ids=company_ids,
    )
    report["artifact_path"] = str(artifact_path)
    report["operation"] = "cip_backfill"
    report["apply_command_preview"] = (
        "python scripts/run_cip_backfill.py --apply --allow-production --track"
    )
    return attach_dry_run_provenance(report, session)


def _verify_full_pool_artifact(report_path: Path) -> None:
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    eligible = int(payload.get("eligible_pool_total") or 0)
    processed = int(payload.get("companies_processed") or 0)
    if payload.get("sample_size_requested") is not None:
        print(
            "[db_safety] dry-run artifact is a sample — regenerate full-pool dry-run before apply.\n"
            f"  sample_size_requested={payload.get('sample_size_requested')}",
            file=sys.stderr,
        )
        raise SystemExit(1)
    if company_ids := payload.get("company_ids"):
        print(
            "[db_safety] dry-run artifact is scoped to specific company ids — regenerate full-pool dry-run before apply.\n"
            f"  company_ids={company_ids}",
            file=sys.stderr,
        )
        raise SystemExit(1)
    if eligible and processed != eligible:
        print(
            "[db_safety] dry-run artifact does not cover the full eligible pool — regenerate before apply.\n"
            f"  processed={processed} eligible={eligible}",
            file=sys.stderr,
        )
        raise SystemExit(1)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
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
        help="Persist CIPs to companies (requires fresh full-pool dry-run artifact).",
    )
    parser.add_argument(
        "--artifact",
        type=Path,
        default=DEFAULT_DRY_RUN_ARTIFACT,
        help="Dry-run artifact path for verification",
    )
    parser.add_argument(
        "--sample-size",
        type=int,
        default=None,
        help="Limit dry-run to first N CI-eligible companies (sample probes only; not valid for --apply).",
    )
    parser.add_argument(
        "--company-id",
        type=int,
        action="append",
        dest="company_ids",
        help="Limit to specific company id (repeatable; not valid for --apply).",
    )
    parser.add_argument(
        "--track",
        action="store_true",
        help="Record this run in pipeline_runs under step cip-backfill.",
    )
    args = parser.parse_args()

    if args.apply and args.dry_run is not None:
        parser.error("Use either --dry-run or --apply, not both.")
    if args.apply and (args.sample_size is not None or args.company_ids):
        parser.error("--sample-size / --company-id are not allowed with --apply (full pool only).")

    if args.apply:
        guard_destructive_db_from_args(
            args,
            script_name=_SCRIPT,
            operation="CIP / dominant_sector backfill",
            nominal_class=SafetyClass.C,
        )
        if not check_db_connection():
            raise SystemExit("Database connection failed")
        _verify_full_pool_artifact(args.artifact)
        session = get_session()

        def _apply_worker() -> dict:
            try:
                verify_dry_run_artifact(session=session, report_path=args.artifact)
                return backfill_company_cips(session, dry_run=False)
            finally:
                session.close()

        if args.track:
            result = execute_tracked_step(STEP_NAME, _apply_worker)
            print(json.dumps(result, indent=2, default=str))
            return 0 if result.get("counts", result).get("error_count", 0) == 0 else 1

        result = _apply_worker()
        print(json.dumps(result, indent=2, default=str))
        return 0 if result.get("error_count", 0) == 0 else 1

    artifact_path = Path(args.dry_run) if args.dry_run is not None else None
    if artifact_path is not None:
        guard_readonly_db_from_args(args, script_name=_SCRIPT)
        if not check_db_connection():
            raise SystemExit("Database connection failed")
        session = get_session()
        try:
            report = _build_dry_run_report(
                session,
                artifact_path=artifact_path,
                sample_size=args.sample_size,
                company_ids=args.company_ids,
            )
        finally:
            session.close()

        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

        print("=== CIP backfill dry-run (Class C) ===")
        print(f"Eligible pool:        {report.get('eligible_pool_total')}")
        print(f"Processed:            {report.get('companies_processed')}")
        print(f"Errors:               {report.get('error_count')}")
        print(f"Elapsed (s):          {report.get('elapsed_seconds')}")
        print(f"Est. full run (min):  {report.get('estimated_full_run_minutes')}")
        print(f"Sector distribution:  {report.get('dominant_sector_distribution')}")
        provenance = report.get("dry_run_provenance", {})
        print(f"git_commit_sha:       {provenance.get('git_commit_sha')}")
        print(f"dataset_fingerprint:  {provenance.get('dataset_fingerprint')}")
        print(f"Artifact: {artifact_path}")
        return 0 if report.get("error_count", 0) == 0 else 1

    parser.error("Specify --dry-run (writes artifact) or --apply (persists after artifact verification).")


if __name__ == "__main__":
    raise SystemExit(main())
