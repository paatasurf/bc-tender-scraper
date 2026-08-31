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
    CompanyEnrichmentApplyPostconditionError,
    _fk_exists,
    apply_company_enrichment_migration,
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


# ---------------------------------------------------------------------------
# Safety review finding #3: a wrong-shaped index with the SAME NAME
# already existing (e.g. from an earlier interrupted/buggy apply attempt)
# must never be silently accepted -- CREATE ... IF NOT EXISTS only checks
# the NAME, never the actual shape, so this is a genuine blind spot any
# --apply script using IF NOT EXISTS DDL is exposed to unless explicitly
# guarded against, as this one is.
# ---------------------------------------------------------------------------


def _create_fields_table_with_wrong_shaped_index(engine) -> None:
    """Manually creates company_enrichment_fields with the SAME index
    NAME the real migration uses, but WITHOUT the partial WHERE clause --
    simulating a stale/earlier/buggy apply attempt, or manual tampering.
    """
    with engine.begin() as conn:
        conn.execute(text("""
                CREATE TABLE company_enrichment_fields (
                    id SERIAL PRIMARY KEY,
                    company_id INTEGER NOT NULL REFERENCES companies(id),
                    field_name VARCHAR(50) NOT NULL,
                    value TEXT NOT NULL,
                    source VARCHAR(30) NOT NULL,
                    confidence DOUBLE PRECISION,
                    verified BOOLEAN NOT NULL DEFAULT FALSE,
                    fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    superseded_at TIMESTAMPTZ,
                    run_id VARCHAR(36)
                )
                """))
        # WRONG on purpose: same name as the real migration's index, but
        # missing "WHERE superseded_at IS NULL" -- a full unique index,
        # not partial.
        conn.execute(
            text(
                "CREATE UNIQUE INDEX ux_company_enrichment_fields_company_field_source "
                "ON company_enrichment_fields (company_id, field_name, source)"
            )
        )


def test_readiness_check_detects_a_wrong_shaped_index_even_in_a_partial_schema(
    clean_engine,
) -> None:
    """Readiness must report CORRUPT -- and must explicitly name the
    wrong index in its violations, not just "the other table is missing"
    -- even when only ONE of the two tables exists yet. Before this
    review's fix, index/FK checks were gated on BOTH tables existing
    together, so a wrong index on an already-created table was silently
    omitted from the violations list whenever the other table hadn't
    been created yet -- an operator reading that incomplete report could
    have concluded "just create the other table" without realizing the
    existing one was already wrong."""
    engine = clean_engine
    _create_fields_table_with_wrong_shaped_index(engine)

    with engine.connect() as conn:
        r = company_enrichment_apply_readiness(conn)

    assert r.status == ApplyReadinessStatus.CORRUPT
    assert any(
        "ux_company_enrichment_fields_company_field_source" in v for v in r.violations
    ), r.violations
    assert any("company_enrichment_jobs table is missing" in v for v in r.violations)


def test_apply_refuses_and_rolls_back_when_a_wrong_shaped_index_already_exists(
    clean_engine,
) -> None:
    """The definitive safety property (not just detection, but REFUSAL):
    apply_company_enrichment_migration() must never silently succeed when
    a same-named-but-wrong-shaped index already exists -- CREATE UNIQUE
    INDEX IF NOT EXISTS would otherwise silently skip creating the
    correct one (Postgres's IF NOT EXISTS only checks the name, never the
    definition), and the migration would "succeed" while leaving the
    schema permanently wrong. This must fail EXPLICITLY (per this
    review's "must either fix the index or fail explicitly" requirement
    -- this codebase's established convention, matching
    OpsJobRunSchemaCorruptError's own "never repairs a corrupt schema
    silently" precedent, is fail-explicit, not auto-repair) and must roll
    back cleanly -- nothing else this call would have created (i.e.
    company_enrichment_jobs) may be left half-committed."""
    engine = clean_engine
    _create_fields_table_with_wrong_shaped_index(engine)

    with pytest.raises(
        CompanyEnrichmentApplyPostconditionError, match="does not conform"
    ):
        apply_company_enrichment_migration(engine)

    with engine.connect() as conn:
        jobs_exists = conn.execute(
            text("SELECT to_regclass('company_enrichment_jobs')")
        ).scalar()
        assert jobs_exists is None  # rolled back cleanly, nothing half-committed

        # The pre-existing wrong index is untouched -- fail-explicit, not
        # auto-repaired.
        index_info = conn.execute(
            text(
                "SELECT indexdef FROM pg_indexes WHERE indexname = "
                "'ux_company_enrichment_fields_company_field_source'"
            )
        ).scalar_one()
        assert "WHERE" not in index_info.upper()  # still the wrong (non-partial) shape
