#!/usr/bin/env python3
"""Apply migration 032: Persistent pipeline coordinator state (R1 backing
store; Class D DDL).

Standalone script -- deliberately NOT wired into
db.connection._run_migrations() or init_db(). The schema stays inert in
every environment (local, staging, production) until an operator
explicitly runs --apply here.

Two new tables (pipeline_coordinator_runs, pipeline_coordinator_steps)
plus their indexes, purely additive -- no existing table is touched, no
default is added to an existing table, no data is written, backfilled, or
seeded. Both tables start (and must remain, immediately after apply)
completely empty.

pipeline/run_coordinator_postgres.py imports these tables as plain
SQLAlchemy Core Table objects (db/pipeline_coordinator_tables.py), not
db.models ORM classes -- so db.connection.init_db()'s
Base.metadata.create_all() can never auto-create this schema at app
startup/deploy. This script is the only thing that ever applies it.

CLI artifact-path contract: --dry-run [PATH] writes to PATH (default if
omitted). --apply reads from the SAME path, given as --artifact-path PATH
(default if omitted) -- mirrors
scripts/run_permit_official_source_id_migration.py.

Rollout runbook (cutover controlled by PIPELINE_COORDINATOR_BACKEND, see
pipeline/run_coordinator.py -- default "legacy", nothing below changes
behavior until step 5):
  1. Merge/deploy this PR's code with PIPELINE_COORDINATOR_BACKEND unset
     (or explicitly "legacy"). Nothing changes: the coordinator keeps
     using the local JSON file exactly as before. This script has not run
     against the target database yet, so pipeline_coordinator_runs/
     pipeline_coordinator_steps do not exist there yet -- that's fine,
     the legacy backend never looks for them.
  2. Run this script with --dry-run against the target DATABASE_URL.
     Inspect the artifact -- confirm "migration_pending": true and the
     planned DDL looks right.
  3. A human runs --apply (with --allow-production and the typed
     confirmation phrase, if the target is production) in their own
     terminal. This step is never automated and never bundled with a
     deploy.
  4. --apply verifies the postcondition itself (both tables conform to
     the full contract, both are empty) inside the same transaction,
     rolling back automatically on any mismatch -- see
     db/pipeline_coordinator_migration.py.
  5. As a separate, deliberate Railway config change (not part of this
     script, not part of any code deploy), set
     PIPELINE_COORDINATOR_BACKEND=postgres on the target environment.
  6. Trigger one controlled pipeline run and verify it end-to-end
     (coordinator state visible in pipeline_coordinator_runs, ordering
     gate still enforced, run completes) before trusting it for the
     regular schedule.
  7. Only after that verified run should a future PR consider removing
     the legacy backend/flag -- not part of R1.
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
from db.pipeline_coordinator_ddl import (  # noqa: E402
    is_valid_ddl_digest,
    pipeline_coordinator_ddl_digest,
    pipeline_coordinator_migration_statements,
)
from db.pipeline_coordinator_migration import (  # noqa: E402
    ApplyReadinessStatus,
    PipelineCoordinatorSchemaCorruptError,
    apply_pipeline_coordinator_migration,
    pipeline_coordinator_apply_readiness,
    pipeline_coordinator_before_stats,
)

_SCRIPT = Path(__file__).name
DEFAULT_DRY_RUN_ARTIFACT = (
    ROOT / "exports" / "pipeline_coordinator_state_migration_dryrun_class_d.json"
)


def _build_dry_run_report(session, *, artifact_path: Path) -> dict:
    before = pipeline_coordinator_before_stats(session)
    statements = pipeline_coordinator_migration_statements()
    return {
        "operation": "pipeline_coordinator_state_schema_migration",
        "class": "D",
        "dry_run": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git_commit_sha": get_git_commit_sha(),
        "ddl_digest": pipeline_coordinator_ddl_digest(),
        "artifact_path": str(artifact_path),
        "migration": "032_pipeline_coordinator_state",
        "production_before": before,
        "planned_mutations": {
            "destructive_delete": False,
            "ddl_only": True,
            "statements_planned": len(statements),
            "already_applied": not before.get("migration_pending", True),
        },
        "ddl_plan": {"statements": statements},
        "apply_command_preview": (
            "python scripts/run_pipeline_coordinator_state_migration.py "
            "--apply --allow-production"
        ),
        "not_wired_to": ["db.connection._run_migrations()", "db.connection.init_db()"],
    }


def _verify_dry_run_artifact(*, session, report_path: Path) -> None:
    """Refuse apply when the dry-run artifact is missing, malformed, or
    stale vs. the current commit, the current DDL, or the current schema-
    existence state. Mirrors
    scripts/run_permit_official_source_id_migration.py's staleness gate."""
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
    expected_digest = pipeline_coordinator_ddl_digest()
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
            "(032_pipeline_coordinator_state.sql does not match the artifact).\n"
            f"  artifact ddl_digest={recorded_digest} current ddl_digest={expected_digest}",
            file=sys.stderr,
        )
        raise SystemExit(1)

    current_before = pipeline_coordinator_before_stats(session)
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
        help="Execute migration 032 DDL (requires a fresh dry-run artifact).",
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
            operation="pipeline coordinator state schema migration (032)",
            nominal_class=SafetyClass.D,
        )
        session = get_session()
        try:
            _verify_dry_run_artifact(session=session, report_path=apply_artifact_path)

            readiness = pipeline_coordinator_apply_readiness(session)
            if readiness.status is ApplyReadinessStatus.FULLY_APPLIED:
                print("[Migration 032] Already applied -- nothing to do.")
                return
            if readiness.status is ApplyReadinessStatus.CORRUPT:
                raise PipelineCoordinatorSchemaCorruptError(
                    "Refusing to apply: the pipeline coordinator schema exists but "
                    "does not fully match the expected contract "
                    "(032_pipeline_coordinator_state.sql). This script never repairs "
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
        result = apply_pipeline_coordinator_migration(engine)
        print("[Migration 032] Apply complete:")
        for key, value in result.items():
            print(f"  {key}: {value}")
        print(
            "[Migration 032] Post-apply: schema is ready, but still inert until "
            "an operator sets PIPELINE_COORDINATOR_BACKEND=postgres on this "
            "environment (default is 'legacy' -- see step 5 of the rollout "
            "runbook above). No code deploy or app restart is needed for this "
            "apply itself; nothing reads or writes this schema until that flag "
            "is flipped."
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
