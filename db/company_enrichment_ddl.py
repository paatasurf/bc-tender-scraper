"""DDL helpers for the company on-demand enrichment schema (migration 034,
RFC Phase 1: docs/COMPANY_ON_DEMAND_ENRICHMENT_RFC.md S5) and its Phase 3
provenance/verification extension (migration 035,
docs/COMPANY_CONTACT_PROVIDER_PHASE3_DESIGN.md S2).

Parses db/migrations/034_company_enrichment.sql and
035_company_enrichment_phase3.sql (and their paired rollbacks) into
individual statements, and offers a deterministic digest of each forward
DDL for staleness detection. Applies nothing on its own and is NOT wired
into db.connection._run_migrations() -- the only places that apply these
schemas are scripts/run_company_enrichment_migration.py --apply (034) and
scripts/run_company_enrichment_phase3_migration.py --apply (035) -- see
db/company_enrichment_migration.py for the readiness/apply/postcondition
logic those scripts use. Mirrors db/ops_job_run_ddl.py's parsing and
digest approach (migration 033) exactly.

_statements_from_path() is dollar-quote-aware ($$ ... $$ / $tag$ ... $tag$)
-- migration 034 has no dollar-quoted blocks, so this is a no-op change in
behavior for it, but migration 035's CREATE FUNCTION ... AS $func$ ... and
DO $$ ... END $$ blocks contain many internal lines ending in ';' that are
NOT statement boundaries; a naive "split on any line ending in ';'" parser
would fragment those blocks mid-body.
"""

from __future__ import annotations

import hashlib
import json
import re

from pathlib import Path

_MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
_MIGRATION_034 = _MIGRATIONS_DIR / "034_company_enrichment.sql"
_MIGRATION_034_ROLLBACK = _MIGRATIONS_DIR / "034_company_enrichment_rollback.sql"
_MIGRATION_035 = _MIGRATIONS_DIR / "035_company_enrichment_phase3.sql"
_MIGRATION_035_ROLLBACK = _MIGRATIONS_DIR / "035_company_enrichment_phase3_rollback.sql"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

FIELDS_TABLE = "company_enrichment_fields"
JOBS_TABLE = "company_enrichment_jobs"

__all__ = [
    "FIELDS_TABLE",
    "JOBS_TABLE",
    "company_enrichment_migration_statements",
    "company_enrichment_rollback_statements",
    "company_enrichment_ddl_digest",
    "company_enrichment_phase3_migration_statements",
    "company_enrichment_phase3_rollback_statements",
    "company_enrichment_phase3_ddl_digest",
    "is_valid_ddl_digest",
]


_DOLLAR_TAG_RE = re.compile(r"\$[A-Za-z_]*\$")


def _statements_from_path(path: Path) -> list[str]:
    raw = path.read_text(encoding="utf-8")
    parts: list[str] = []
    buffer: list[str] = []
    dollar_tag: str | None = None  # e.g. "$$" or "$func$" while inside one
    for line in raw.splitlines():
        stripped = line.strip()
        if dollar_tag is None and stripped.startswith("--"):
            continue
        buffer.append(line)

        pos = 0
        while True:
            if dollar_tag is None:
                match = _DOLLAR_TAG_RE.search(line, pos)
                if match is None:
                    break
                dollar_tag = match.group(0)
                pos = match.end()
            else:
                idx = line.find(dollar_tag, pos)
                if idx == -1:
                    break
                pos = idx + len(dollar_tag)
                dollar_tag = None

        if dollar_tag is None and stripped.endswith(";"):
            statement = "\n".join(buffer).strip()
            if statement:
                parts.append(statement)
            buffer = []
    if buffer:
        statement = "\n".join(buffer).strip()
        if statement:
            parts.append(statement)
    return parts


def company_enrichment_migration_statements() -> list[str]:
    """Return the forward migration 034 SQL statements."""
    return _statements_from_path(_MIGRATION_034)


def company_enrichment_rollback_statements() -> list[str]:
    """Return migration 034's paired rollback SQL statements."""
    return _statements_from_path(_MIGRATION_034_ROLLBACK)


def company_enrichment_phase3_migration_statements() -> list[str]:
    """Return the forward migration 035 (Phase 3) SQL statements."""
    return _statements_from_path(_MIGRATION_035)


def company_enrichment_phase3_rollback_statements() -> list[str]:
    """Return migration 035's paired rollback SQL statements."""
    return _statements_from_path(_MIGRATION_035_ROLLBACK)


def _canonical_digest(statements: list[str]) -> str:
    canonical = json.dumps(statements, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def company_enrichment_ddl_digest() -> str:
    """Deterministic SHA-256 over the canonical (comment-stripped, parsed)
    forward-migration statement list. Changes whenever the DDL changes --
    used to detect a stale dry-run artifact before --apply."""
    return _canonical_digest(company_enrichment_migration_statements())


def company_enrichment_phase3_ddl_digest() -> str:
    """Same staleness-detection digest as company_enrichment_ddl_digest(),
    over migration 035's forward statement list instead of 034's."""
    return _canonical_digest(company_enrichment_phase3_migration_statements())


def is_valid_ddl_digest(value: object) -> bool:
    """True iff value is a lowercase 64-character hex SHA-256 digest string."""
    return isinstance(value, str) and bool(_SHA256_RE.fullmatch(value))
