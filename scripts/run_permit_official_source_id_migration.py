#!/usr/bin/env python3
"""Apply migration 031: Surrey official source identity schema foundation
(Class D DDL).

Standalone script -- deliberately NOT wired into
db.connection._run_migrations() or init_db(). The schema stays inert in
every environment until an operator explicitly runs --apply here.

One new nullable column (permits.official_source_id) plus one partial
unique index on (source, official_source_id), purely additive. No other
table is touched, no default is added, no data is written, backfilled, or
seeded by this script -- every existing row keeps official_source_id NULL
after apply.

This PR ships the migration only. No ORM mapping, Surrey bridge, importer,
scheduler, API, or frontend change accompanies it -- db.models.Permit does
not declare this column yet, so application code is never deployed ahead
of the schema it will eventually depend on.

CLI artifact-path contract: --dry-run [PATH] writes to PATH (default if
omitted). --apply reads from the SAME path, given as --artifact-path PATH
(default if omitted) -- mirrors
scripts/run_company_track_record_migration.py.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config.env  # noqa: F401,E402

from db.classification import SafetyClass  # noqa: E402
from db.connection import get_engine, get_session  # noqa: E402
from db.db_safety import (  # noqa: E402
    add_production_safety_args,
    guard_destructive_db_from_args,
    guard_readonly_db_from_args,
)
from db.merge_dry_run_provenance import get_git_commit_sha  # noqa: E402
from db.permit_official_source_id_ddl import (  # noqa: E402
    is_valid_ddl_digest,
    permit_official_source_id_ddl_digest,
    permit_official_source_id_migration_statements,
)
from db.permit_official_source_id_migration import (  # noqa: E402
    ApplyReadinessStatus,
    PermitOfficialSourceIdSchemaCorruptError,
    apply_permit_official_source_id_migration,
    permit_official_source_id_apply_readiness,
    permit_official_source_id_before_stats,
)

_SCRIPT = Path(__file__).name
DEFAULT_DRY_RUN_ARTIFACT = (
    ROOT / "exports" / "permit_official_source_id_migration_dryrun_class_d.json"
)


def _build_dry_run_report(session, *, artifact_path: Path) -> dict:
    before = permit_official_source_id_before_stats(session)
    statements = permit_official_source_id_migration_statements()
    return {
        "operation": "permit_official_source_id_schema_migration",
        "class": "D",
        "dry_run": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git_commit_sha": get_git_commit_sha(),
        "ddl_digest": permit_official_source_id_ddl_digest(),
        "artifact_path": str(artifact_path),
        "migration": "031_permit_official_source_id",
        "production_before": before,
        "planned_mutations": {
            "destructive_delete": False,
            "ddl_only": True,
            "statements_planned": len(statements),
            "already_applied": not before.get("migration_pending", True),
        },
        "ddl_plan": {"statements": statements},
        "apply_command_preview": (
            "python scripts/run_permit_official_source_id_migration.py "
            "--apply --allow-production"
        ),
        "not_wired_to": ["db.connection._run_migrations()", "db.connection.init_db()"],
    }


def _verify_dry_run_artifact(*, session, report_path: Path) -> None:
    """Refuse apply when the dry-run artifact is missing, malformed, or
    stale vs. the current commit, the current DDL, or the current schema-
    existence state. Mirrors
    scripts/run_company_track_record_migration.py's staleness gate."""
    if not report_path.is_file():
        print(
            f"[db_safety] dry-run is stale -- regenerate before apply.\n"
            f"  Missing artifact: {report_path}",
            file=sys.stderr,
        )
        raise SystemExit(1)

    try:
        payload = json.loads(report_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(
            f"[db_safety] dry-run is stale -- regenerate before apply.\n"
            f"  Invalid JSON in {report_path}: {exc}",
            file=sys.stderr,
        )
        raise SystemExit(1)

    expected_sha = get_git_commit_sha()
    recorded_sha = str(payload.get("git_commit_sha", ""))
    if recorded_sha != expected_sha:
        print(
            "[db_safety] dry-run is stale -- regenerate before apply.\n"
            f"  git commit mismatch: artifact={recorded_sha} current={expected_sha}",
            file=sys.stderr,
        )
        raise SystemExit(1)

    recorded_digest = payload.get("ddl_digest")
    expected_digest = permit_official_source_id_ddl_digest()
    if not is_valid_ddl_digest(recorded_digest):
        print(
            "[db_safety] dry-run is stale -- regenerate before apply.\n"
            f"  artifact ddl_digest is missing or malformed: {recorded_digest!r}",
            file=sys.stderr,
        )
        raise SystemExit(1)
    if recorded_digest != expected_digest:
        print(
            "[db_safety] dry-run is stale -- regenerate before apply.\n"
            "  DDL changed since this dry-run was generated "
            "(031_permit_official_source_id.sql does not match the artifact).\n"
            f"  artifact ddl_digest={recorded_digest} current ddl_digest={expected_digest}",
            file=sys.stderr,
        )
        raise SystemExit(1)

    current_before = permit_official_source_id_before_stats(session)
    recorded_before = payload.get("production_before")
    if recorded_before != current_before:
        print(
            "[db_safety] dry-run is stale -- regenerate before apply.\n"
            "  schema state changed since dry-run was generated.\n"
            f"  artifact production_before={recorded_before}\n"
            f"  current production_before={current_before}",
            file=sys.stderr,
        )
        raise SystemExit(1)


def main() -> None:
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
        help="Execute migration 031 DDL (requires a fresh dry-run artifact).",
    )
    parser.add_argument(
        "--artifact-path",
        metavar="PATH",
        default=None,
        help=(
            "Dry-run artifact path for --apply to read (default: "
            f"{DEFAULT_DRY_RUN_ARTIFACT}). Must match the path used with --dry-run."
        ),
    )
    args = parser.parse_args()

    modes_selected = sum([args.apply, args.dry_run is not None])
    if modes_selected > 1:
        parser.error("Use only one of --dry-run, --apply.")
    if args.artifact_path is not None and not args.apply:
        parser.error("--artifact-path is only valid together with --apply.")

    if args.apply:
        apply_artifact_path = (
            Path(args.artifact_path)
            if args.artifact_path is not None
            else DEFAULT_DRY_RUN_ARTIFACT
        )
        guard_destructive_db_from_args(
            args,
            script_name=_SCRIPT,
            operation="permit_official_source_id schema migration (031)",
            nominal_class=SafetyClass.D,
        )
        session = get_session()
        try:
            _verify_dry_run_artifact(session=session, report_path=apply_artifact_path)

            readiness = permit_official_source_id_apply_readiness(session)
            if readiness.status is ApplyReadinessStatus.FULLY_APPLIED:
                print("[Migration 031] Already applied -- nothing to do.")
                return
            if readiness.status is ApplyReadinessStatus.CORRUPT:
                raise PermitOfficialSourceIdSchemaCorruptError(
                    "Refusing to apply: the permits.official_source_id schema exists "
                    "but does not fully match the expected contract "
                    "(031_permit_official_source_id.sql). This script never repairs "
                    "a schema silently.\n"
                    "  Violations:\n"
                    + "\n".join(f"    - {v}" for v in readiness.violations)
                    + "\n  Next step: investigate manually, then fix forward with a "
                    "new migration."
                )
            # NOT_APPLIED: proceed.
        finally:
            session.close()

        engine = get_engine()
        result = apply_permit_official_source_id_migration(engine)
        print("[Migration 031] Apply complete:")
        for key, value in result.items():
            print(f"  {key}: {value}")
        print(
            "[Migration 031] Post-apply: official_source_id is inert until a "
            "separate identity-bridge PR adds the ORM mapping and a dedicated, "
            "digest-pinned writer."
        )
        return

    artifact_path = (
        Path(args.dry_run) if args.dry_run is not None else DEFAULT_DRY_RUN_ARTIFACT
    )
    guard_readonly_db_from_args(args, script_name=_SCRIPT)
    session = get_session()
    try:
        report = _build_dry_run_report(session, artifact_path=artifact_path)
    finally:
        session.close()

    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(
        json.dumps(report, indent=2, default=str), encoding="utf-8"
    )
    print(json.dumps(report, indent=2, default=str))
    print(f"\nWrote {artifact_path}")


if __name__ == "__main__":
    main()
