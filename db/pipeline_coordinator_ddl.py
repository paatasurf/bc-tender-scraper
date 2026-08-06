"""DDL helpers for the persistent pipeline coordinator schema (migration 032).

Parses db/migrations/032_pipeline_coordinator_state.sql (and its paired
rollback) into individual statements, and offers a deterministic digest of
the forward DDL for staleness detection. Applies nothing on its own and is
NOT wired into db.connection._run_migrations() -- the only place that
applies this schema is
scripts/run_pipeline_coordinator_state_migration.py --apply (see
db/pipeline_coordinator_migration.py for the readiness/apply/postcondition
logic that script uses). Mirrors db/permit_official_source_id_ddl.py's
parsing and digest approach (migration 031).
"""

from __future__ import annotations

import hashlib
import json
import re

from pathlib import Path

_MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
_MIGRATION_032 = _MIGRATIONS_DIR / "032_pipeline_coordinator_state.sql"
_MIGRATION_032_ROLLBACK = (
    _MIGRATIONS_DIR / "032_pipeline_coordinator_state_rollback.sql"
)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

RUNS_TABLE = "pipeline_coordinator_runs"
STEPS_TABLE = "pipeline_coordinator_steps"

__all__ = [
    "RUNS_TABLE",
    "STEPS_TABLE",
    "pipeline_coordinator_migration_statements",
    "pipeline_coordinator_rollback_statements",
    "pipeline_coordinator_ddl_digest",
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


def pipeline_coordinator_migration_statements() -> list[str]:
    """Return the forward migration 032 SQL statements."""
    return _statements_from_path(_MIGRATION_032)


def pipeline_coordinator_rollback_statements() -> list[str]:
    """Return migration 032's paired rollback SQL statements."""
    return _statements_from_path(_MIGRATION_032_ROLLBACK)


def _canonical_digest(statements: list[str]) -> str:
    canonical = json.dumps(statements, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def pipeline_coordinator_ddl_digest() -> str:
    """Deterministic SHA-256 over the canonical (comment-stripped, parsed)
    forward-migration statement list. Changes whenever the DDL changes --
    used to detect a stale dry-run artifact before --apply."""
    return _canonical_digest(pipeline_coordinator_migration_statements())


def is_valid_ddl_digest(value: object) -> bool:
    """True iff value is a lowercase 64-character hex SHA-256 digest string."""
    return isinstance(value, str) and bool(_SHA256_RE.fullmatch(value))
