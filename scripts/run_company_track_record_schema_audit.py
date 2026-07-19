#!/usr/bin/env python3
"""Class A — no write. Company track-record schema verification.

Read-only introspection confirming migration 030's four track_record_*
columns on ``companies`` -- if present at all -- fully match the expected
contract: column name/type/nullability/length, absence of any default,
and all three CHECK constraints (name AND exact expression, via
pg_get_constraintdef). A matching name alone is never sufficient for a
CHECK constraint. Also reports how many rows currently have any non-NULL
track_record_* value, and confirms migration 030's own SQL file only ever
issues ALTER TABLE against ``companies`` (never any other table).

Single Engine, single Connection, single audit-owned transaction:
everything above -- the schema-contract check and the non-NULL row count
-- is read through the exact same connection inside one explicit
REPEATABLE READ, READ ONLY transaction (see
db.track_record_migration.build_track_record_audit_report), so the two
checks can never observe two different points in time and this script
can never issue a write, even accidentally.

Empty-row policy (--require-empty): by default, a non-zero
nonnull_row_count is reported informationally only and does NOT fail the
audit -- after a future wiring PR starts writing real data, non-empty
rows are expected, not a defect. Pass --require-empty for the
post-migration/pre-wiring gate: run this once immediately after --apply
(before any wiring PR is merged) to confirm nothing has written to these
columns yet; a non-zero count then fails the audit.

Prints JSON to stdout. Guard banner goes to stderr only.

Target database: DATABASE_URL (local) by default; pass --use-production to
read DATABASE_URL_PRODUCTION instead. This remains Class A read-only in
either case -- there is no --allow-production here, no interactive
destructive confirmation, and no DDL/DML path, against local or
production.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config.env  # noqa: F401,E402  (loads .env before importing db.connection)

from db.connection import get_engine  # noqa: E402
from db.db_safety import guard_readonly_db_from_args  # noqa: E402
from db.track_record_migration import build_track_record_audit_report  # noqa: E402
from sqlalchemy import text  # noqa: E402

_SCRIPT = Path(__file__).name


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--use-production",
        action="store_true",
        help="Read DATABASE_URL_PRODUCTION instead of local DATABASE_URL (Class A read-only; banner only).",
    )
    parser.add_argument(
        "--require-empty",
        action="store_true",
        help=(
            "Post-migration/pre-wiring gate: fail if any row has a non-NULL "
            "track_record_* value. Run this once immediately after --apply, "
            "before any wiring PR is merged. Default: nonnull_row_count is "
            "informational only and never fails the audit by itself."
        ),
    )
    args = parser.parse_args()

    with contextlib.redirect_stdout(sys.stderr):
        guard_readonly_db_from_args(args, script_name=_SCRIPT)

    engine = get_engine()
    conn = engine.connect()
    try:
        # One explicit, audit-owned transaction. SET TRANSACTION must be
        # the first statement after BEGIN in Postgres -- conn.begin()
        # issues BEGIN, so this is the very next statement on conn.
        trans = conn.begin()
        try:
            conn.execute(
                text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            )
            output = build_track_record_audit_report(
                conn, require_empty=args.require_empty
            )
        finally:
            # Read-only transaction -- rollback and commit are equivalent
            # here; rollback is used so this script can never be mistaken
            # for a writer even if a future edit accidentally adds one.
            trans.rollback()
    finally:
        conn.close()
        engine.dispose()

    print(json.dumps(output, indent=2, default=str))
    return 0 if output["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
