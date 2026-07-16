"""DDL helpers for the Classification Claims schema foundation (migration 029)."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

_MIGRATION_029 = (
    Path(__file__).resolve().parent / "migrations" / "029_classification_claims.sql"
)
_MIGRATION_029_ROLLBACK = (
    Path(__file__).resolve().parent
    / "migrations"
    / "029_classification_claims_rollback.sql"
)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


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


def classification_claims_migration_statements() -> list[str]:
    """Return SQL statements for migration 029 (forward)."""
    return _statements_from_path(_MIGRATION_029)


def classification_claims_rollback_statements() -> list[str]:
    """Return SQL statements for migration 029's rollback.

    Callers must not execute these without first verifying every table is
    empty — see db.classification_claims_migration.apply_classification_claims_rollback.
    """
    return _statements_from_path(_MIGRATION_029_ROLLBACK)


def classification_claims_table_names() -> list[str]:
    """The six table names, in DROP (reverse-creation) order."""
    return [
        "resolved_company_beliefs",
        "projector_runs",
        "claim_events",
        "claim_evidence",
        "classification_claims",
        "rule_set_versions",
    ]


def _canonical_digest(statements: list[str]) -> str:
    """SHA-256 of the exact parsed statement list, JSON-serialized (sorted
    keys not needed — a list is already order-sensitive, which is correct:
    changing statement order is itself a meaningful DDL change)."""
    canonical = json.dumps(statements, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def classification_claims_ddl_digest() -> str:
    """Deterministic SHA-256 over the canonical (comment-stripped, parsed)
    forward DDL statement list in 029_classification_claims.sql. Changes
    whenever the DDL changes — used to detect a stale dry-run artifact
    before --apply (see scripts/run_classification_claims_migration.py)."""
    return _canonical_digest(classification_claims_migration_statements())


def classification_claims_rollback_ddl_digest() -> str:
    """Deterministic SHA-256 over the canonical rollback DDL statement list.

    Not currently consumed by any dry-run/apply staleness check — rollback
    has a separate safety contract from forward-apply (see
    db.classification_claims_migration.apply_classification_claims_rollback):
    it never reads a pre-computed plan/artifact that could go stale, because
    it re-reads this file from disk and re-checks live row counts atomically,
    every time, inside the same transaction as the DROP statements. This
    digest exists for parity/tooling (e.g. a future rollback dry-run) and is
    exercised by tests proving the rollback statements are read fresh on
    every call, not cached.
    """
    return _canonical_digest(classification_claims_rollback_statements())


def is_valid_ddl_digest(value: object) -> bool:
    """True iff value is a lowercase 64-character hex SHA-256 digest string."""
    return isinstance(value, str) and bool(_SHA256_RE.fullmatch(value))
