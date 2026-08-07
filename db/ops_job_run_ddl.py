"""DDL helpers for the ops job run schema (migration 033, M3B).

Parses db/migrations/033_ops_job_runs.sql (and its paired rollback) into
individual statements, and offers a deterministic digest of the forward
DDL for staleness detection. Applies nothing on its own and is NOT wired
into db.connection._run_migrations() -- the only place that applies this
schema is scripts/run_ops_job_run_migration.py --apply (see
db/ops_job_run_migration.py for the readiness/apply/postcondition logic
that script uses). Mirrors db/pipeline_coordinator_ddl.py's parsing and
digest approach (migration 032).
"""

from __future__ import annotations

import hashlib
import json
import re

from pathlib import Path

_MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
_MIGRATION_033 = _MIGRATIONS_DIR / "033_ops_job_runs.sql"
_MIGRATION_033_ROLLBACK = _MIGRATIONS_DIR / "033_ops_job_runs_rollback.sql"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

RUNS_TABLE = "ops_job_runs"
EVENTS_TABLE = "ops_job_run_events"

__all__ = [
    "RUNS_TABLE",
    "EVENTS_TABLE",
    "ops_job_run_migration_statements",
    "ops_job_run_rollback_statements",
    "ops_job_run_ddl_digest",
    "is_valid_ddl_digest",
]


def _statements_from_path(path: Path) -> list[str]:
    raw = path.read_text(encoding="utf-8")
    parts: list[str] = []
    buffer: list[str] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if stripped.startswith("--"):
            continue
        buffer.append(line)
        if stripped.endswith(";"):
            statement = "\n".join(buffer).strip()
            if statement:
                parts.append(statement)
            buffer = []
    if buffer:
        statement = "\n".join(buffer).strip()
        if statement:
            parts.append(statement)
    return parts


def ops_job_run_migration_statements() -> list[str]:
    """Return the forward migration 033 SQL statements."""
    return _statements_from_path(_MIGRATION_033)


def ops_job_run_rollback_statements() -> list[str]:
    """Return migration 033's paired rollback SQL statements."""
    return _statements_from_path(_MIGRATION_033_ROLLBACK)


def _canonical_digest(statements: list[str]) -> str:
    canonical = json.dumps(statements, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def ops_job_run_ddl_digest() -> str:
    """Deterministic SHA-256 over the canonical (comment-stripped, parsed)
    forward-migration statement list. Changes whenever the DDL changes --
    used to detect a stale dry-run artifact before --apply."""
    return _canonical_digest(ops_job_run_migration_statements())


def is_valid_ddl_digest(value: object) -> bool:
    """True iff value is a lowercase 64-character hex SHA-256 digest string."""
    return isinstance(value, str) and bool(_SHA256_RE.fullmatch(value))
