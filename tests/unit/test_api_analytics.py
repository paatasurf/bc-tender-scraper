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


def test_distinct_count_works_for_permits_and_contract_awards() -> None:
    for domain, alias in (("permits", "p"), ("contract_awards", "a")):
        spec = AnalyticsQuerySpec(domain=domain, metrics=["distinct_count"])
        sql, _params, _provenance = build_query(spec)
        assert f"COUNT(DISTINCT {alias}.company_id)" in sql


def test_distinct_count_rejected_for_domains_without_company_id() -> None:
    """Regression: the old hardcoded permits/else-contract_awards alias
    silently produced SQL referencing an undefined table alias for
    companies/tenders/early_signals -- caught only by the endpoint's
    generic except-block as an opaque 503. Must fail fast as a clear
    ValueError (-> 422) instead."""
    for domain in ("companies", "tenders", "early_signals"):
        spec = AnalyticsQuerySpec(domain=domain, metrics=["distinct_count"])
        with pytest.raises(ValueError, match="distinct_count"):
            build_query(spec)


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


def test_group_by_company_id_does_not_leak_as_alias_into_group_by_clause() -> None:
    """Regression: dimensions (SELECT, "expr AS name") was reused verbatim
    for GROUP BY, producing "GROUP BY p.company_id AS company_id" -- a
    Postgres syntax error. Every "which N companies had the most ..."
    ranking query (group_by=company_id or canonical_company_id) failed with
    this, surfaced only as an opaque 503 to the caller."""
    for group in ("company_id", "canonical_company_id"):
        spec = AnalyticsQuerySpec(domain="permits", metrics=["count"], group_by=[group])
        sql, _params, _provenance = build_query(spec)
        assert " AS " not in sql[sql.index("GROUP BY") :]


def test_group_by_company_id_actually_executes_against_real_schema(
    local_db_session: Session,
) -> None:
    """The syntax-only check above would not have caught this bug on its
    own -- prove the query is valid against a real connection too."""
    spec = AnalyticsQuerySpec(
        domain="permits",
        metrics=["count"],
        group_by=["company_id"],
        order_by="count",
        limit=5,
    )
    sql, params, _provenance = build_query(spec)
    rows = local_db_session.execute(text(sql), params).mappings().all()
    for row in rows:
        assert "count" in row
        assert "company_id" in row


def test_bc_province_query_against_unsupported_domain_is_a_capability_gap() -> None:
    """Positive-data proof: a province-wide "BC" request against a domain
    with no province column (permits has none) must fail as an explicit
    validation error -- before any HTTP/DB call -- never silently drop the
    scope and answer with an unfiltered total pretending to be BC-scoped."""
    with pytest.raises(ValidationError, match="province-wide geography"):
        AnalyticsQuerySpec(domain="permits", metrics=["count"], province="BC")
    with pytest.raises(ValidationError, match="province-wide geography"):
        AnalyticsQuerySpec(domain="tenders", metrics=["count"], province="BC")
    with pytest.raises(ValidationError, match="province-wide geography"):
        AnalyticsQuerySpec(domain="early_signals", metrics=["count"], province="BC")


def test_canonical_company_ranking_collapses_aliases_into_one_row(
    local_db_session: Session,
) -> None:
    """Positive-data proof: two ALIAS company rows pointing at the same
    canonical parent must collapse into exactly one canonical_company_id
    ranking row (not two), carrying the canonical company's real name, with
    evidence (contributing_ids) pointing at the actual permit rows behind
    the count -- not a repeat of the aggregate."""
    import uuid

    from db.models import Company, Permit

    uid = uuid.uuid4().hex[:8]
    city = f"CanonicalTestCity-{uid}"

    canonical = Company(name=f"Canonical Co {uid}", display_name=f"Canonical Co {uid}")
    local_db_session.add(canonical)
    local_db_session.flush()

    alias_a = Company(
        name=f"Alias A {uid}",
        display_name=f"Alias A {uid}",
        canonical_company_id=canonical.id,
    )
    alias_b = Company(
        name=f"Alias B {uid}",
        display_name=f"Alias B {uid}",
        canonical_company_id=canonical.id,
    )
    local_db_session.add_all([alias_a, alias_b])
    local_db_session.flush()

    permit_a = Permit(
        address=f"1 Test St {uid}", city=city, source="unittest", company_id=alias_a.id
    )
    permit_b = Permit(
        address=f"2 Test St {uid}", city=city, source="unittest", company_id=alias_b.id
    )
    local_db_session.add_all([permit_a, permit_b])
    local_db_session.commit()

    try:
        spec = AnalyticsQuerySpec(
            domain="permits",
            metrics=["count"],
            filters={"city": city},
            group_by=["canonical_company_id"],
        )
        sql, params, _provenance = build_query(spec)
        rows = local_db_session.execute(text(sql), params).mappings().all()

        matching = [r for r in rows if r["canonical_company_id"] == canonical.id]
        assert len(matching) == 1, (
            "two aliases of one canonical company must collapse into one "
            "ranking row, not two"
        )
        row = matching[0]
        assert row["count"] == 2
        assert row["canonical_company_name"] == canonical.display_name
        assert set(row["contributing_ids"]) == {permit_a.id, permit_b.id}
    finally:
        local_db_session.execute(
            text("DELETE FROM permits WHERE id = ANY(:ids)"),
            {"ids": [permit_a.id, permit_b.id]},
        )
        local_db_session.execute(
            text("DELETE FROM companies WHERE id = ANY(:ids)"),
            {"ids": [alias_a.id, alias_b.id, canonical.id]},
        )
        local_db_session.commit()


def test_ranking_discloses_unresolved_company_attribution(
    local_db_session: Session,
) -> None:
    """Positive-data proof: a permit with no company attribution
    (company_id IS NULL) is invisible to the INNER-JOIN ranking query --
    _unresolved_count_query must surface it separately rather than let it
    silently vanish from the total."""
    import uuid

    from api.analytics import _alias_for, _unresolved_count_query
    from db.models import Company, Permit

    uid = uuid.uuid4().hex[:8]
    city = f"UnresolvedTestCity-{uid}"

    company = Company(name=f"Ranked Co {uid}")
    local_db_session.add(company)
    local_db_session.flush()

    attributed = Permit(
        address=f"1 St {uid}", city=city, source="unittest", company_id=company.id
    )
    unattributed = Permit(
        address=f"2 St {uid}", city=city, source="unittest", company_id=None
    )
    local_db_session.add_all([attributed, unattributed])
    local_db_session.commit()

    try:
        spec = AnalyticsQuerySpec(
            domain="permits",
            metrics=["count"],
            filters={"city": city},
            group_by=["company_id"],
        )
        u_sql, u_params = _unresolved_count_query(spec, _alias_for(spec.domain))
        unresolved = local_db_session.execute(text(u_sql), u_params).scalar_one()
        assert unresolved == 1
    finally:
        local_db_session.execute(
            text("DELETE FROM permits WHERE id = ANY(:ids)"),
            {"ids": [attributed.id, unattributed.id]},
        )
        local_db_session.execute(
            text("DELETE FROM companies WHERE id = :id"), {"id": company.id}
        )
        local_db_session.commit()


def test_province_filter_actually_scopes_to_bc_against_real_schema(
    local_db_session: Session,
) -> None:
    """Positive-data proof for a domain that DOES support province scope:
    a BC-tagged award is counted, an ON-tagged award under the same source
    is not -- proves the filter is real, not a silently-dropped no-op that
    would otherwise return both as an unscoped total."""
    import uuid

    from db.models import ContractAward

    uid = uuid.uuid4().hex[:8]
    source = f"unittest-{uid}"

    bc_award = ContractAward(
        source=source,
        external_id=f"bc-{uid}",
        title="BC Award",
        winner_company="BC Co",
        winner_province="BC",
    )
    on_award = ContractAward(
        source=source,
        external_id=f"on-{uid}",
        title="ON Award",
        winner_company="ON Co",
        winner_province="ON",
    )
    local_db_session.add_all([bc_award, on_award])
    local_db_session.commit()

    try:
        spec = AnalyticsQuerySpec(
            domain="contract_awards",
            metrics=["count"],
            filters={"source": source},
            province="BC",
        )
        sql, params, provenance = build_query(spec)
        assert provenance["province"] == "BC"
        row = local_db_session.execute(text(sql), params).mappings().one()
        assert row["count"] == 1
    finally:
        local_db_session.execute(
            text("DELETE FROM contract_awards WHERE id = ANY(:ids)"),
            {"ids": [bc_award.id, on_award.id]},
        )
        local_db_session.commit()
