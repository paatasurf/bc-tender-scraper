#!/usr/bin/env python3
"""Company track-record shadow dry-run (Class A -- no write).

Read-only preview of ``pipeline.track_record_backfill
.backfill_company_track_records(..., dry_run=True)``: selects the
eligible batch, runs the adapter + pure scorer for each company, and
writes an aggregate-only artifact JSON -- score histogram, coverage
counts, error counts by stage/type, and a single SHA-256 eligibility
digest. Never a raw company id, name, address, phone, AI summary text,
per-company result, raw exception message, or connection detail.

This script has no ``--apply`` and no ``--allow-production`` -- it cannot
write to any database under any flag combination. A future PR-G3.3b will
add a separate, explicitly Class C, bounded apply script that reads an
artifact produced here.

Single connection, single explicit transaction: ``SET TRANSACTION
ISOLATION LEVEL REPEATABLE READ, READ ONLY`` is issued as the very first
statement on that connection, before anything else runs on it, and the
transaction is always rolled back (success or failure) before this
process exits. The artifact is written only after that transaction has
been closed -- if selection or scoring raises, nothing is ever written.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config.env  # noqa: F401,E402  (loads .env before importing db.connection)

from sqlalchemy import text  # noqa: E402
from sqlalchemy.engine import Engine  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from db.connection import get_engine  # noqa: E402
from db.db_safety import guard_readonly_db_from_args  # noqa: E402
from db.merge_dry_run_provenance import get_git_commit_sha  # noqa: E402
from pipeline.track_record_backfill import backfill_company_track_records  # noqa: E402
from pipeline.track_record_shadow_artifact import (  # noqa: E402
    build_shadow_dryrun_artifact,
)

_SCRIPT = Path(__file__).name
DEFAULT_ARTIFACT_PATH = ROOT / "exports" / "track_record_shadow_dryrun_class_a.json"


def run_dry_run(
    engine: Engine,
    *,
    artifact_path: Path,
    company_ids: list[int] | None,
    sample_size: int | None,
    reference_date: date | None,
    force: bool,
) -> dict[str, Any]:
    """Run the whole shadow dry-run inside one connection and one
    explicit, always-rolled-back, ``REPEATABLE READ, READ ONLY``
    transaction, then write the resulting aggregate-only artifact.

    Raises whatever ``backfill_company_track_records`` or artifact
    aggregation raises -- the ``finally`` block still closes the Session,
    rolls back, and closes the connection, but the artifact-write step
    below it is never reached, so a failure here writes nothing.
    """
    conn = engine.connect()
    trans = conn.begin()
    session: Session | None = None
    try:
        conn.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        session = Session(bind=conn)
        result = backfill_company_track_records(
            session,
            company_ids=company_ids,
            sample_size=sample_size,
            reference_date=reference_date,
            dry_run=True,
            force=force,
        )
        artifact = build_shadow_dryrun_artifact(
            result,
            git_commit_sha=get_git_commit_sha(),
            sample_size=sample_size,
            explicit_company_ids=company_ids,
            force=force,
            generated_at=datetime.now(timezone.utc),
        )
    finally:
        # Session closed explicitly, before the transaction is rolled
        # back and the connection is closed -- one connection, one
        # transaction throughout; the Session never outlives either.
        if session is not None:
            session.close()
        trans.rollback()
        conn.close()

    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
    return artifact


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--use-production",
        action="store_true",
        help=(
            "Read DATABASE_URL_PRODUCTION instead of local DATABASE_URL "
            "(Class A read-only; banner only -- this script never writes)."
        ),
    )
    parser.add_argument(
        "--artifact-path",
        type=Path,
        default=DEFAULT_ARTIFACT_PATH,
        help=f"Output artifact JSON path (default: {DEFAULT_ARTIFACT_PATH}).",
    )
    parser.add_argument(
        "--sample-size",
        type=int,
        default=None,
        help=(
            "Limit to the first N eligible companies (SQL LIMIT, applied "
            "after the eligibility/company-id filters and ORDER BY)."
        ),
    )
    parser.add_argument(
        "--company-id",
        type=int,
        action="append",
        dest="company_ids",
        help="Limit to a specific company id (repeatable).",
    )
    parser.add_argument(
        "--reference-date",
        type=str,
        default=None,
        metavar="YYYY-MM-DD",
        help="Explicit reference date (default: today, UTC).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "Analyze as if every selected company were being recomputed "
            "regardless of its current track_record_version. Read-only -- "
            "this flag never causes a write, in this script or any other."
        ),
    )
    args = parser.parse_args()

    reference_date: date | None = None
    if args.reference_date is not None:
        try:
            reference_date = date.fromisoformat(args.reference_date)
        except ValueError:
            parser.error(
                f"--reference-date must be YYYY-MM-DD, got {args.reference_date!r}"
            )

    guard_readonly_db_from_args(args, script_name=_SCRIPT)

    engine = get_engine()
    try:
        artifact = run_dry_run(
            engine,
            artifact_path=args.artifact_path,
            company_ids=args.company_ids,
            sample_size=args.sample_size,
            reference_date=reference_date,
            force=args.force,
        )
    finally:
        engine.dispose()

    print(json.dumps(artifact, indent=2))
    print(f"\nWrote {args.artifact_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
