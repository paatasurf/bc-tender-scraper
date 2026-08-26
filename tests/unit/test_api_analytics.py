import os

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from api.analytics import AnalyticsQuerySpec, build_query


def test_builds_parameterized_read_only_permit_query() -> None:
    spec = AnalyticsQuerySpec(
        domain="permits",
        metrics=["count"],
        geography="Burnaby",
        date_field="issue_date",
        lookback_days=60,
        group_by=["permit_type"],
        order_by="count",
        limit=25,
    )
    sql, params, provenance = build_query(spec)
    assert "scraped_at" not in sql
    assert ":geography" in sql and ":start_date" in sql
    assert "Burnaby" == params["geography"]
    assert provenance["read_only"] is True


def test_scraped_at_is_not_an_allowed_event_field() -> None:
    try:
        AnalyticsQuerySpec(
            domain="permits",
            metrics=["count"],
            date_field="scraped_at",
            lookback_days=30,
        )
    except ValidationError:
        return
    raise AssertionError("scraped_at must be rejected as event time")


def test_raw_sql_and_unapproved_join_dimensions_are_rejected() -> None:
    for kwargs in (
        {"domain": "permits", "metrics": ["count"], "filters": {"id OR 1=1": "x"}},
        {"domain": "early_signals", "metrics": ["count"], "group_by": ["company_id"]},
    ):
        try:
            AnalyticsQuerySpec(**kwargs)
        except ValidationError:
            continue
        raise AssertionError("unsafe QuerySpec was accepted")


def test_records_operation_builds_allowlisted_field_select() -> None:
    spec = AnalyticsQuerySpec(
        domain="permits",
        operation="records",
        geography="Burnaby",
        date_field="issue_date",
        lookback_days=60,
        fields=["address", "permit_type", "issue_date"],
        limit=10,
    )
    sql, params, provenance = build_query(spec)
    assert "scraped_at" not in sql
    assert "p.id AS id" in sql
    assert "p.address AS address" in sql
    assert "ORDER BY p.issue_date DESC" in sql
    assert params["result_limit"] == 10
    assert provenance["operation"] == "records"
    assert provenance["fields"][0] == "id"


def test_records_operation_defaults_to_full_allowlist_and_id_order() -> None:
    from api.analytics import _RECORD_FIELDS

    spec = AnalyticsQuerySpec(domain="permits", operation="records")
    sql, _params, provenance = build_query(spec)
    assert "ORDER BY p.id DESC" in sql
    assert set(provenance["fields"]) == _RECORD_FIELDS["permits"]
    assert "company_id" in provenance["fields"]


def test_records_operation_rejects_metrics_and_group_by() -> None:
    for kwargs in (
        {"domain": "permits", "operation": "records", "metrics": ["count"]},
        {"domain": "permits", "operation": "records", "group_by": ["city"]},
        {"domain": "permits", "operation": "records", "fields": ["not_a_real_field"]},
    ):
        try:
            AnalyticsQuerySpec(**kwargs)
        except ValidationError:
            continue
        raise AssertionError("unsafe records QuerySpec was accepted")


def test_aggregate_operation_rejects_fields() -> None:
    with pytest.raises(ValidationError):
        AnalyticsQuerySpec(domain="permits", metrics=["count"], fields=["address"])


def _require_local_database_url() -> str:
    from tests.db_test_safety import _ci_skips_db_integration

    if _ci_skips_db_integration():
        pytest.skip(
            "DB integration tests skipped on CI (set CI_DATABASE_URL to enable)"
        )
    database_url = os.getenv("DATABASE_URL", "")
    if not database_url:
        pytest.skip("DATABASE_URL not configured")
    lowered = database_url.lower()
    if any(token in lowered for token in ("railway", "rlwy.net", "production")):
        pytest.skip(
            "Refusing analytics integration tests against production DATABASE_URL"
        )
    return database_url


@pytest.fixture()
def local_db_session() -> Session:
    import config.env  # noqa: F401
    from db.connection import init_db

    database_url = _require_local_database_url()
    engine = create_engine(database_url, connect_args={"connect_timeout": 3})
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:
        pytest.skip("Local Postgres unavailable for analytics integration test")

    init_db()
    factory = sessionmaker(bind=engine)
    session = factory()
    try:
        yield session
    finally:
        session.close()


def test_records_query_actually_executes_against_real_schema(
    local_db_session: Session,
) -> None:
    """Not a syntax-only check: proves the built SQL is valid against the
    real permits table (columns, types, ORDER BY) on a real connection."""
    spec = AnalyticsQuerySpec(
        domain="permits",
        operation="records",
        fields=["address", "city", "issue_date"],
        limit=5,
    )
    sql, params, _provenance = build_query(spec)
    rows = local_db_session.execute(text(sql), params).mappings().all()
    assert len(rows) <= 5
    for row in rows:
        assert set(row.keys()) == {"id", "address", "city", "issue_date"}


def test_aggregate_query_actually_executes_against_real_schema(
    local_db_session: Session,
) -> None:
    spec = AnalyticsQuerySpec(
        domain="permits",
        metrics=["count"],
        group_by=["city"],
        order_by="count",
        limit=5,
    )
    sql, params, _provenance = build_query(spec)
    rows = local_db_session.execute(text(sql), params).mappings().all()
    for row in rows:
        assert "count" in row
        assert "city" in row
