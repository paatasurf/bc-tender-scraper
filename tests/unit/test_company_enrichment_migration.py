"""Fresh-Postgres migration tests for db/company_enrichment_migration.py.

Covers the readiness/apply-safety contract end to end (rollback -> not
applied -> apply -> fully applied -> rollback -> not applied again), and
Bugbot finding #5: _fk_exists() must verify the LOCAL column the foreign
key is defined on (company_id), not just that SOME foreign key on the
table happens to point at the referenced table/column. The original
query only joined constraint_column_usage (which reports the REFERENCED
side of a FK), never key_column_usage (the LOCAL side) -- it could not
have told "company_id has an FK to companies.id" apart from "some
unrelated column on this table has an FK to companies.id".
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from db.company_enrichment_ddl import (
    company_enrichment_migration_statements,
    company_enrichment_rollback_statements,
)
from db.company_enrichment_migration import (
    ApplyReadinessStatus,
    _fk_exists,
    company_enrichment_apply_readiness,
)
from db.models import Company
from tests.db_test_safety import require_local_test_database


@pytest.fixture
def clean_engine():
    database_url = require_local_test_database()
    engine = create_engine(database_url, connect_args={"connect_timeout": 3})
    try:
        with engine.connect() as probe:
            probe.execute(text("SELECT 1"))
    except Exception:
        pytest.skip("Local Postgres unavailable")

    with engine.begin() as conn:
        for stmt in company_enrichment_rollback_statements():
            conn.execute(text(stmt))
    try:
        yield engine
    finally:
        with engine.begin() as conn:
            for stmt in company_enrichment_rollback_statements():
                conn.execute(text(stmt))
        engine.dispose()


def test_fresh_postgres_full_migration_lifecycle(clean_engine) -> None:
    """Rollback -> not_applied -> apply -> fully_applied -> rollback ->
    not_applied again, all against a genuinely clean schema."""
    engine = clean_engine

    with engine.connect() as conn:
        r = company_enrichment_apply_readiness(conn)
        assert r.status == ApplyReadinessStatus.NOT_APPLIED

    with engine.begin() as conn:
        for stmt in company_enrichment_migration_statements():
            conn.execute(text(stmt))

    with engine.connect() as conn:
        r = company_enrichment_apply_readiness(conn)
        assert r.status == ApplyReadinessStatus.FULLY_APPLIED, r.violations

    with engine.begin() as conn:
        for stmt in company_enrichment_rollback_statements():
            conn.execute(text(stmt))

    with engine.connect() as conn:
        r = company_enrichment_apply_readiness(conn)
        assert r.status == ApplyReadinessStatus.NOT_APPLIED

        exists = conn.execute(
            text(
                "SELECT to_regclass('company_enrichment_fields'), to_regclass('company_enrichment_jobs')"
            )
        ).first()
        assert exists == (None, None)


def test_fk_exists_confirms_the_real_company_id_foreign_key(clean_engine) -> None:
    """Positive case: the real migration's company_id -> companies.id FK
    is correctly detected."""
    engine = clean_engine
    with engine.begin() as conn:
        for stmt in company_enrichment_migration_statements():
            conn.execute(text(stmt))

    with engine.connect() as conn:
        assert (
            _fk_exists(
                conn,
                table="company_enrichment_fields",
                local_column="company_id",
                ref_table="companies",
                ref_column="id",
            )
            is True
        )
        assert (
            _fk_exists(
                conn,
                table="company_enrichment_jobs",
                local_column="company_id",
                ref_table="companies",
                ref_column="id",
            )
            is True
        )


def test_fk_exists_rejects_a_foreign_key_on_the_wrong_local_column(
    clean_engine,
) -> None:
    """Bugbot finding #5 regression: a table where a DIFFERENT column
    (not company_id) carries the FK to companies.id must NOT be reported
    as "company_id has a foreign key to companies.id". The pre-fix query
    would have returned True here (it only checked that SOME FK on the
    table points at companies.id), which is exactly the corruption
    scenario this check exists to catch and previously could not."""
    engine = clean_engine

    with Session(engine) as session:
        company = Company(name="FK Column Check Co Ltd")
        session.add(company)
        session.commit()
        company_id = company.id

    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE fk_check_scratch ("
                "  id SERIAL PRIMARY KEY,"
                "  company_id INTEGER NOT NULL,"  # deliberately NO foreign key on this column
                "  owner_company_id INTEGER NOT NULL REFERENCES companies(id)"  # the FK is on a DIFFERENT column
                ")"
            )
        )
    try:
        with engine.connect() as conn:
            # The column that actually has the FK is owner_company_id, not company_id.
            assert (
                _fk_exists(
                    conn,
                    table="fk_check_scratch",
                    local_column="owner_company_id",
                    ref_table="companies",
                    ref_column="id",
                )
                is True
            )
            assert (
                _fk_exists(
                    conn,
                    table="fk_check_scratch",
                    local_column="company_id",
                    ref_table="companies",
                    ref_column="id",
                )
                is False
            )
    finally:
        with engine.begin() as conn:
            conn.execute(text("DROP TABLE fk_check_scratch"))
        with engine.begin() as conn:
            conn.execute(
                text("DELETE FROM companies WHERE id = :id"), {"id": company_id}
            )
