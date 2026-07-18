"""Unit tests for the Company.sector_confidence ORM mapping (PR-E3B0).

Confirms db/models.py::Company now maps the sector_confidence column that
db/migrations/024_sector_confidence.sql defines. Migration 024 defines the
physical column; this change aligns the ORM mapping with that migration
contract. Without this mapping, pipeline/cip_builder.py::persist_cip()'s
`row.sector_confidence = cip.sector_confidence` silently set an
unmapped Python attribute that SQLAlchemy never included in UPDATE
statements. This file does not modify pipeline/cip_builder.py, and does
not modify or create any migration.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path

import pytest
from sqlalchemy import String, inspect, text
from sqlalchemy.orm.attributes import InstrumentedAttribute

from db.models import ArchCompany, Company

MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "db"
    / "migrations"
    / "024_sector_confidence.sql"
)


# ===================================================================
# 1-4: table/type/length/nullable+default match migration 024
# ===================================================================


def test_company_table_contains_sector_confidence():
    assert "sector_confidence" in Company.__table__.columns


def test_sector_confidence_sql_type_is_varchar():
    column = Company.__table__.columns["sector_confidence"]
    assert isinstance(column.type, String)


def test_sector_confidence_max_length_is_ten():
    column = Company.__table__.columns["sector_confidence"]
    assert column.type.length == 10


def test_sector_confidence_nullable_and_default_match_migration_024():
    """Migration 024's ALTER TABLE has no NOT NULL clause (nullable) and
    a server-side DEFAULT '' -- the ORM mapping must reflect exactly
    that, not an application-side default."""
    column = Company.__table__.columns["sector_confidence"]
    assert column.nullable is True
    assert column.server_default is not None
    # DefaultClause wraps a TextClause; compare its literal SQL text.
    default_sql = str(column.server_default.arg)
    assert default_sql == "''"


# ===================================================================
# 5-6: instrumentation / ORM change tracking
# ===================================================================


def test_sector_confidence_is_an_instrumented_mapped_attribute():
    assert isinstance(Company.sector_confidence, InstrumentedAttribute)


def test_assigning_sector_confidence_is_tracked_by_orm_history():
    company = Company(name=f"__test_sector_confidence_{uuid.uuid4().hex}__")
    state = inspect(company)

    before = state.attrs.sector_confidence.history
    assert before.added == ()

    company.sector_confidence = "high"
    after = state.attrs.sector_confidence.history

    assert list(after.added) == ["high"]


# ===================================================================
# 7: ArchCompany must not receive this field
# ===================================================================


def test_arch_company_does_not_have_sector_confidence():
    """Migration 024 only alters `companies` -- arch_companies is
    untouched, so the ORM must not claim a column that doesn't exist
    there."""
    assert "sector_confidence" not in ArchCompany.__table__.columns
    assert not hasattr(ArchCompany, "sector_confidence") or not isinstance(
        getattr(ArchCompany, "sector_confidence", None), InstrumentedAttribute
    )


# ===================================================================
# 8: migration 024 and the ORM mapping must not drift apart
# ===================================================================


def test_migration_024_and_orm_mapping_agree_on_name_and_length():
    sql = MIGRATION_PATH.read_text(encoding="utf-8")
    match = re.search(
        r"ADD COLUMN IF NOT EXISTS\s+(\w+)\s+VARCHAR\((\d+)\)\s+DEFAULT\s+''",
        sql,
    )
    assert match, "Could not parse migration 024's ADD COLUMN clause"
    migration_column_name, migration_length = match.group(1), int(match.group(2))

    column = Company.__table__.columns["sector_confidence"]
    assert column.name == migration_column_name
    assert column.type.length == migration_length


def test_migration_024_has_no_not_null_clause():
    """Static confirmation that migration 024 itself never adds a NOT
    NULL constraint -- the basis for this mapping's nullable=True."""
    sql = MIGRATION_PATH.read_text(encoding="utf-8")
    add_column_stmt = sql.split(";")[0]
    assert "NOT NULL" not in add_column_stmt.upper()


# ===================================================================
# 9: DB-backed regression test (local Postgres only, transaction rolled
# back, refuses production). Uses the repo's own established safe-DB
# test helper (tests/db_test_safety.require_local_test_database) rather
# than building new test infrastructure.
# ===================================================================


def test_sector_confidence_flush_persists_to_real_column():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from tests.db_test_safety import require_local_test_database

    database_url = require_local_test_database()
    engine = create_engine(database_url, connect_args={"connect_timeout": 3})
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:
        pytest.skip("Local Postgres unavailable")

    session = Session(engine)
    try:
        company = Company(name=f"__test_sector_confidence_{uuid.uuid4().hex}__")
        session.add(company)
        session.flush()  # INSERT within the open transaction, not committed

        company.sector_confidence = "high"
        session.flush()  # UPDATE within the same open transaction

        raw_value = session.execute(
            text("SELECT sector_confidence FROM companies WHERE id = :id"),
            {"id": company.id},
        ).scalar()
        assert raw_value == "high"
    finally:
        session.rollback()  # undo the INSERT/UPDATE -- nothing persists
        session.close()
