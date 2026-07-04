"""Unit tests for construction score history infrastructure."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from db.construction_tier_ddl import construction_tier_migration_statements
from db.models import Company, CompanyScoreHistory
from db.company_canonical_constants import ENTITY_ROLE_CANONICAL
from pipeline.construction_score_history import (
    build_score_history_row,
    get_score_history,
    record_score_snapshot,
    score_history_to_dict,
)
from pipeline.construction_tier_config import CONSTRUCTION_TIER_VERSION


def test_migration_statements_include_score_history():
    joined = "\n".join(construction_tier_migration_statements())
    assert "company_score_history" in joined
    assert "construction_score" in joined


def test_build_score_history_row():
    row = build_score_history_row(
        company_id=8638,
        construction_score=72,
        company_tier="tier_a",
        algorithm_version=CONSTRUCTION_TIER_VERSION,
    )
    assert row.company_id == 8638
    assert row.construction_score == 72
    assert row.company_tier == "tier_a"
    assert row.algorithm_version == CONSTRUCTION_TIER_VERSION


def test_score_history_to_dict():
    row = CompanyScoreHistory(
        company_id=1,
        construction_score=55,
        company_tier="tier_b",
        calculated_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
        algorithm_version=1,
    )
    payload = score_history_to_dict(row)
    assert payload["construction_score"] == 55
    assert payload["company_tier"] == "tier_b"
    assert payload["algorithm_version"] == 1


@pytest.fixture(scope="module")
def db_session():
    from db.connection import get_session, init_db
    from tests.db_test_safety import require_local_test_database

    require_local_test_database()
    init_db()
    session = get_session()
    yield session
    session.close()


def test_record_and_fetch_score_history(db_session):
    import uuid

    from tests.db_test_safety import teardown_test_company

    suffix = uuid.uuid4().hex[:8]
    company = Company(
        name=f"Score History Test {suffix}",
        display_name=f"Score History Test {suffix}",
        entity_role=ENTITY_ROLE_CANONICAL,
    )
    db_session.add(company)
    db_session.commit()
    company_id = company.id
    try:
        calculated_at = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
        record_score_snapshot(
            db_session,
            company_id=company.id,
            construction_score=80,
            company_tier="tier_a",
            calculated_at=calculated_at,
            commit=True,
        )

        history = get_score_history(db_session, company.id, limit=5)
        assert len(history) >= 1
        assert history[0].construction_score == 80
        assert history[0].company_tier == "tier_a"
    finally:
        teardown_test_company(db_session, company_id)
