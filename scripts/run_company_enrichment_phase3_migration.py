#!/usr/bin/env python3
"""Apply migration 035: Company enrichment Phase 3 provenance/verification
schema (docs/COMPANY_CONTACT_PROVIDER_PHASE3_DESIGN.md S2; Class D DDL).

Standalone script -- deliberately NOT wired into
db.connection._run_migrations() or init_db(). The schema stays inert in
every environment (local, staging, production) until an operator
explicitly runs --apply here.

Alters the two existing tables migration 034 created (adds 6 columns + 1
CHECK constraint to company_enrichment_fields; 1 JSONB column + 1
validation function + 1 CHECK constraint to company_enrichment_jobs) --
purely additive, no other table touched, no data backfilled or seeded.
Unlike migration 034's own apply (which requires both brand-new tables to
be empty), this migration does NOT require either table to be empty --
they may already hold real candidate rows written by
pipeline/company_enrichment/orchestrator.py.

Applying this migration does NOT enable ENRICHMENT_ENABLED, does NOT wire
WebsiteContactProvider into _default_providers(), and does NOT write any
application data -- see docs/COMPANY_CONTACT_PROVIDER_PHASE3_DESIGN.md S8
for the separate, still-unmet production-admission gates that govern when
this provider may write anything at all, and S9 for the explicit list of
what remains out of scope for this migration.

Requires migration 034 to already be FULLY_APPLIED -- checked before any
DDL runs (both by this script's own readiness check and, redundantly,
inside apply_company_enrichment_phase3_migration()'s own transaction).

CLI artifact-path contract: --dry-run [PATH] writes to PATH (default if
omitted). --apply reads from the SAME path, given as --artifact-path PATH
(default if omitted) -- mirrors
scripts/run_company_enrichment_migration.py (migration 034) exactly.

Rollout runbook:
  1. Confirm migration 034 is already FULLY_APPLIED on the target
     DATABASE_URL (scripts/run_company_enrichment_migration.py has no
     --status flag; company_enrichment_apply_readiness() is the
     underlying check -- this script's own --dry-run reports 034's status
     too, as part of "production_before").
  2. Run this script with --dry-run against the target DATABASE_URL.
     Inspect the artifact -- confirm "migration_pending": true and the
     planned DDL looks right.
  3. A human runs --apply (with --allow-production and the typed
     confirmation phrase, if the target is production) in their own
     terminal. This step is never automated and never bundled with a
     deploy.
  4. --apply verifies the postcondition itself (034 is FULLY_APPLIED
     before any DDL runs; the Phase 3 schema fully conforms after) inside
     the same transaction, rolling back automatically on any mismatch --
     see db/company_enrichment_migration.py.
  5. The schema now has provenance/verification columns, but nothing
     reads or writes them yet -- ProviderFact already carries
     source_url/raw_value/extraction_method as optional fields, but
     write_enrichment_facts() persisting them, and any code ever setting
     verified_at/verified_by/verification_source_url, require their own
     separate, future, explicitly-authorized changes.
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
from db.company_enrichment_ddl import (  # noqa: E402
    company_enrichment_phase3_ddl_digest,
    company_enrichment_phase3_migration_statements,
    is_valid_ddl_digest,
)
from db.company_enrichment_migration import (  # noqa: E402
    ApplyReadinessStatus,
    CompanyEnrichmentPhase3SchemaCorruptError,
    apply_company_enrichment_phase3_migration,
    company_enrichment_apply_readiness,
    company_enrichment_phase3_apply_readiness,
    company_enrichment_phase3_before_stats,
)

_SCRIPT = Path(__file__).name
DEFAULT_DRY_RUN_ARTIFACT = (
    ROOT / "exports" / "company_enrichment_phase3_migration_dryrun_class_d.json"
)


def _build_dry_run_report(session, *, artifact_path: Path) -> dict:
    before = company_enrichment_phase3_before_stats(session)
    migration_034_status = company_enrichment_apply_readiness(session).status.value
    statements = company_enrichment_phase3_migration_statements()
    return {
        "operation": "company_enrichment_phase3_schema_migration",
        "class": "D",
        "dry_run": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git_commit_sha": get_git_commit_sha(),
        "ddl_digest": company_enrichment_phase3_ddl_digest(),
        "artifact_path": str(artifact_path),
        "migration": "035_company_enrichment_phase3",
        "migration_034_status": migration_034_status,
        "production_before": before,
        "planned_mutations": {
            "destructive_delete": False,
            "ddl_only": True,
            "statements_planned": len(statements),
            "already_applied": not before.get("migration_pending", True),
        },
        "ddl_plan": {"statements": statements},
        "apply_command_preview": (
            "python scripts/run_company_enrichment_phase3_migration.py "
            "--apply --allow-production"
        ),
        "not_wired_to": ["db.connection._run_migrations()", "db.connection.init_db()"],
    }


def _verify_dry_run_artifact(*, session, report_path: Path) -> None:
    """Refuse apply when the dry-run artifact is missing, malformed, or
    stale vs. the current commit, the current DDL, or the current schema-
    existence state. Mirrors
    scripts/run_company_enrichment_migration.py's staleness gate."""
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
    expected_digest = company_enrichment_phase3_ddl_digest()
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
            "(035_company_enrichment_phase3.sql does not match the artifact).\n"
            f"  artifact ddl_digest={recorded_digest} current ddl_digest={expected_digest}",
            file=sys.stderr,
        )
        raise SystemExit(1)

    current_before = company_enrichment_phase3_before_stats(session)
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
        help="Execute migration 035 DDL (requires a fresh dry-run artifact).",
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
            operation="company enrichment Phase 3 schema migration (035)",
            nominal_class=SafetyClass.D,
        )
        session = get_session()
        try:
            _verify_dry_run_artifact(session=session, report_path=apply_artifact_path)

            migration_034 = company_enrichment_apply_readiness(session)
            if migration_034.status is not ApplyReadinessStatus.FULLY_APPLIED:
                raise CompanyEnrichmentPhase3SchemaCorruptError(
                    "Refusing to apply migration 035: migration 034 is not "
                    f"FULLY_APPLIED (status={migration_034.status.value}). Apply "
                    "034 first (scripts/run_company_enrichment_migration.py "
                    "--apply).\n  034 violations:\n"
                    + "\n".join(f"    - {v}" for v in migration_034.violations)
                )

            readiness = company_enrichment_phase3_apply_readiness(session)
            if readiness.status is ApplyReadinessStatus.FULLY_APPLIED:
                print("[Migration 035] Already applied -- nothing to do.")
                return
            if readiness.status is ApplyReadinessStatus.CORRUPT:
                raise CompanyEnrichmentPhase3SchemaCorruptError(
                    "Refusing to apply: the Phase 3 schema exists but does not "
                    "fully match the expected contract "
                    "(035_company_enrichment_phase3.sql). This script never "
                    "repairs a schema silently.\n  Violations:\n"
                    + "\n".join(f"    - {v}" for v in readiness.violations)
                    + "\n  Next step: investigate manually, then fix forward "
                    "with a new migration."
                )
            # NOT_APPLIED: proceed.
        finally:
            session.close()

        engine = get_engine()
        result = apply_company_enrichment_phase3_migration(engine)
        print("[Migration 035] Apply complete:")
        for key, value in result.items():
            print(f"  {key}: {value}")
        print(
            "[Migration 035] Post-apply: provenance/verification columns exist, "
            "but nothing reads or writes verified_at/verified_by/"
            "verification_source_url yet, and write_enrichment_facts() does "
            "not yet persist source_url/raw_value/extraction_method -- both "
            "require their own separate, future, explicitly-authorized "
            "changes. ENRICHMENT_ENABLED is untouched by this apply. No code "
            "deploy or app restart is needed for this apply itself."
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
