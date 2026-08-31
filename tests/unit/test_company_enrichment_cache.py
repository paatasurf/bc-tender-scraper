"""Real local-Postgres tests for pipeline/company_enrichment/orchestrator.py's
cache-check (RFC S7 step 2, golden case #3: fresh cache -> immediate
return, zero provider calls, no job row created).

Applies migration 034 directly (company_enrichment_migration_statements())
against the local test database, mirroring
tests/unit/test_company_intelligence_telemetry.py's job_run_db fixture
pattern -- this is NOT scripts/run_company_enrichment_migration.py --apply
(that guarded CLI script is never invoked by this test or any other code
in this PR); it is the same "apply raw DDL directly against a disposable
local-Postgres test schema" convention every other real-Postgres dedup
test in this repo already uses.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from db.company_enrichment_ddl import company_enrichment_migration_statements
from db.company_enrichment_tables import company_enrichment_fields
from db.models import Company
from pipeline.company_enrichment.orchestrator import check_cache
from tests.db_test_safety import require_local_test_database


@pytest.fixture
def enrichment_db():
    database_url = require_local_test_database()
    engine = create_engine(database_url, connect_args={"connect_timeout": 3})
    try:
        with engine.connect() as probe:
            probe.execute(text("SELECT 1"))
    except Exception:
        pytest.skip("Local Postgres unavailable")

    with engine.begin() as conn:
        for statement in company_enrichment_migration_statements():
            conn.execute(text(statement))

    company_id = None
    with Session(engine) as session:
        company = Company(name="Cache Hit Test Co Ltd")
        session.add(company)
        session.commit()
        company_id = company.id

    def _reset() -> None:
        with engine.begin() as conn:
            conn.execute(
                text("DELETE FROM company_enrichment_fields WHERE company_id = :id"),
                {"id": company_id},
            )
            conn.execute(
                text("DELETE FROM company_enrichment_jobs WHERE company_id = :id"),
                {"id": company_id},
            )
            conn.execute(
                text("DELETE FROM companies WHERE id = :id"), {"id": company_id}
            )

    try:
        yield engine, company_id
    finally:
        _reset()
        engine.dispose()


def test_no_fields_yet_is_not_a_cache_hit(enrichment_db) -> None:
    """Golden case #9 territory, not #3: no data at all must NOT be
    treated as a fresh cache -- the caller must proceed to the provider
    cascade at least once."""
    engine, company_id = enrichment_db
    with Session(engine) as session:
        assert check_cache(session, company_id) is None


def test_fresh_fields_are_a_cache_hit_with_no_job_created(enrichment_db) -> None:
    engine, company_id = enrichment_db
    with engine.begin() as conn:
        conn.execute(
            company_enrichment_fields.insert().values(
                company_id=company_id,
                field_name="legal_name",
                value="Cache Hit Test Co Ltd.",
                source="orgbook",
                confidence=0.9,
                verified=False,
                fetched_at=datetime.now(timezone.utc),
                superseded_at=None,
                run_id="fixture-run-id",
            )
        )

    with Session(engine) as session:
        result = check_cache(session, company_id)

    assert result is not None
    assert result["status"] == "cache_hit"
    assert result["company_id"] == company_id
    assert len(result["fields"]) == 1
    assert result["fields"][0]["field_name"] == "legal_name"

    with engine.connect() as conn:
        job_count = conn.execute(
            text("SELECT COUNT(*) FROM company_enrichment_jobs WHERE company_id = :id"),
            {"id": company_id},
        ).scalar_one()
    assert job_count == 0  # cache hit must never create a job row


def test_stale_fields_are_not_a_cache_hit(enrichment_db) -> None:
    engine, company_id = enrichment_db
    stale_fetched_at = datetime.now(timezone.utc) - timedelta(days=31)
    with engine.begin() as conn:
        conn.execute(
            company_enrichment_fields.insert().values(
                company_id=company_id,
                field_name="legal_name",
                value="Cache Hit Test Co Ltd.",
                source="orgbook",
                confidence=0.9,
                verified=False,
                fetched_at=stale_fetched_at,
                superseded_at=None,
                run_id="fixture-run-id",
            )
        )

    with Session(engine) as session:
        result = check_cache(session, company_id, stale_days=30)

    assert result is None


def test_superseded_fields_are_ignored_for_cache_freshness(enrichment_db) -> None:
    """A superseded row must never count toward freshness -- only current
    (superseded_at IS NULL) rows do."""
    engine, company_id = enrichment_db
    with engine.begin() as conn:
        conn.execute(
            company_enrichment_fields.insert().values(
                company_id=company_id,
                field_name="legal_name",
                value="Old Value",
                source="orgbook",
                confidence=0.9,
                verified=False,
                fetched_at=datetime.now(timezone.utc),
                superseded_at=datetime.now(timezone.utc),
                run_id="fixture-run-id",
            )
        )

    with Session(engine) as session:
        result = check_cache(session, company_id)

    assert result is None
