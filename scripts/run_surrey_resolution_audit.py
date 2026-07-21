#!/usr/bin/env python3
"""Surrey permit resolution-readiness audit (Class A -- no write).

Read-only aggregate report of how many ``source='surrey'`` ``Permit``
rows would be resolvable against *already-existing* ``Company`` rows if a
future controlled backfill ran: raw applicant preserved, safe
normalization counts, then resolution against a read-only index of
existing companies (never fuzzy, never a create). See
``pipeline.surrey_resolution_audit`` for the full contract and for why a
separate read-only matcher was built instead of reusing
``CompanyResolver`` directly.

This script has no ``--apply`` and no ``--allow-production`` -- it cannot
write to any database under any flag combination, and it never assigns
or changes ``permits.company_id``.

Single connection, single explicit transaction: ``SET TRANSACTION
ISOLATION LEVEL REPEATABLE READ, READ ONLY`` is issued as the very first
statement on that connection, before anything else runs on it, and the
transaction is always rolled back (success or failure) before this
process exits. The artifact is written only after that transaction has
been closed -- if the audit raises, nothing is ever written.

The artifact contains only aggregate counts, histograms, and a content
digest over the examined Permit id set -- never a permit id, company id,
applicant string, company name, address, raw payload, or exception
message.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
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
from pipeline.surrey_resolution_audit import (  # noqa: E402
    ARTIFACT_SCHEMA_VERSION,
    audit_surrey_permit_resolution,
)

_SCRIPT = Path(__file__).name
DEFAULT_ARTIFACT_PATH = ROOT / "exports" / "surrey_resolution_audit_class_a.json"
TRANSACTION_MODE = "REPEATABLE READ, READ ONLY"


def run_audit(
    engine: Engine,
    *,
    artifact_path: Path | None,
    sample_size: int | None,
) -> dict[str, Any]:
    """Run the whole audit inside one connection and one explicit,
    always-rolled-back, ``REPEATABLE READ, READ ONLY`` transaction, then
    write the resulting aggregate-only artifact.

    Raises whatever ``audit_surrey_permit_resolution`` raises -- the
    ``finally`` block still closes the Session, rolls back, and closes
    the connection, but the artifact-write step below it is never
    reached, so a failure here writes nothing.
    """
    conn = engine.connect()
    trans = conn.begin()
    session: Session | None = None
    try:
        conn.execute(text(f"SET TRANSACTION ISOLATION LEVEL {TRANSACTION_MODE}"))
        session = Session(bind=conn)
        report = audit_surrey_permit_resolution(session, sample_size=sample_size)
    finally:
        # Session closed explicitly, before the transaction is rolled
        # back and the connection is closed -- one connection, one
        # transaction throughout; the Session never outlives either.
        if session is not None:
            session.close()
        trans.rollback()
        conn.close()

    artifact = {
        "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
        "git_commit_sha": get_git_commit_sha(),
        "source": "surrey",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "transaction_mode": TRANSACTION_MODE,
        "sample_size": sample_size,
        **report,
    }

    if artifact_path is not None:
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
            "Examine at most this many Surrey permits, deterministically "
            "ordered by Permit.id ascending (SQL LIMIT). Default: all "
            "source='surrey' permits."
        ),
    )
    args = parser.parse_args()

    guard_readonly_db_from_args(args, script_name=_SCRIPT)

    engine = get_engine()
    try:
        artifact = run_audit(
            engine,
            artifact_path=args.artifact_path,
            sample_size=args.sample_size,
        )
    finally:
        engine.dispose()

    print(json.dumps(artifact, indent=2))
    if args.artifact_path is not None:
        print(f"\nWrote {args.artifact_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
