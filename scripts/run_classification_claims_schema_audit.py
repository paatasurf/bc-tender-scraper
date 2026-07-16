#!/usr/bin/env python3
"""Class A — no write. Classification Claims schema verification.

Read-only introspection confirming the six migration-029 tables exist and
fully match the expected contract — every required column (name, type,
nullability), primary key, UNIQUE constraint, CHECK constraint (name AND
exact expression, via pg_get_constraintdef), index (name, uniqueness,
ordered columns/expressions, and partial predicate, via pg_get_indexdef/
pg_get_expr), and foreign key (exact source column and referenced
table/column) — plus zero rows (no backfill/seed has run) and that migration
029 issued no ALTER against any existing table. A matching name alone is
never sufficient for a CHECK, index, or foreign key.

The deep contract check is shared with db.classification_claims_migration's
own "already applied" gate via db.classification_claims_schema_contract, so
the two can never silently drift apart.

Prints JSON to stdout. Guard banner goes to stderr only.
"""

from __future__ import annotations

import contextlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config.env  # noqa: F401  (loads .env before importing db.connection)
from db.classification_claims_schema_contract import verify_schema_contract
from db.connection import get_session_factory
from db.db_safety import guard_readonly_db
from sqlalchemy import text

_SCRIPT = Path(__file__).name


def _migration_sql_touches_only_new_tables() -> tuple[bool, list[str]]:
    """Static check: the migration file must contain zero ALTER statements
    against any pre-existing table (companies included)."""
    sql_path = ROOT / "db" / "migrations" / "029_classification_claims.sql"
    source = sql_path.read_text(encoding="utf-8")
    violations = [
        line.strip()
        for line in source.splitlines()
        if line.strip().upper().startswith("ALTER TABLE")
    ]
    return (len(violations) == 0, violations)


def main() -> int:
    with contextlib.redirect_stdout(sys.stderr):
        guard_readonly_db(_SCRIPT)

    factory = get_session_factory()
    session = factory()
    findings: list[str] = []

    try:
        static_ok, alter_violations = _migration_sql_touches_only_new_tables()
        if not static_ok:
            findings.append(
                f"migration SQL contains ALTER statements against existing tables: {alter_violations}"
            )

        conformance = verify_schema_contract(session)
        findings.extend(conformance.describe_violations())

        table_reports: dict[str, dict] = {}
        for name, tc in conformance.tables.items():
            report: dict = {"exists": tc.exists, "conforms": tc.conforms}
            if tc.exists:
                row_count = session.execute(
                    text(f"SELECT COUNT(*) FROM {name}")
                ).scalar_one()
                report["row_count"] = int(row_count)
                if row_count != 0:
                    findings.append(f"{name} is not empty (row_count={row_count})")
                report["missing_columns"] = list(tc.missing_columns)
                report["wrong_type_columns"] = list(tc.wrong_type_columns)
                report["wrong_nullability_columns"] = list(tc.wrong_nullability_columns)
                report["missing_primary_key"] = tc.missing_primary_key
                report["missing_unique_constraints"] = [
                    list(c) for c in tc.missing_unique_constraints
                ]
                report["missing_check_constraints"] = list(tc.missing_check_constraints)
                report["wrong_check_constraints"] = list(tc.wrong_check_constraints)
                report["missing_indexes"] = list(tc.missing_indexes)
                report["wrong_indexes"] = list(tc.wrong_indexes)
                report["missing_foreign_keys"] = [
                    {
                        "source_column": fk.source_column,
                        "referenced_table": fk.referenced_table,
                        "referenced_column": fk.referenced_column,
                    }
                    for fk in tc.missing_foreign_keys
                ]
            table_reports[name] = report

        status = "PASS" if not findings else "FAIL"
        output = {
            "schema_version": 3,
            "status": status,
            "migration": "029_classification_claims",
            "migration_sql_touches_only_new_tables": static_ok,
            "tables": table_reports,
            "findings": findings,
        }
    finally:
        session.close()

    print(json.dumps(output, indent=2, default=str))
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
