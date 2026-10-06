"""Real local-Postgres tests for migration 031 (integration_connections).

Mirrors tests/unit/test_company_enrichment_cache.py's exact fixture
pattern (as seen in this repo's sibling-feature test style): applies the
migration's raw DDL directly against a disposable local-Postgres test
schema via require_local_test_database() -- never against production,
never via scripts/run_*_migration.py --apply (that script does not exist
yet for this migration; see the PR description).
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from db.integration_connections_ddl import integration_connections_migration_statements
from db.integration_connections_tables import integration_connections
from db.models import Company
from tests.db_test_safety import require_local_test_database


@pytest.fixture
def connections_db():
    database_url = require_local_test_database()
    engine = create_engine(database_url, connect_args={"connect_timeout": 3})
    try:
        with engine.connect() as probe:
            probe.execute(text("SELECT 1"))
    except Exception:
        pytest.skip("Local Postgres unavailable")

    import db.models as _db_models  # noqa: F401  # register ORM models

    _db_models.Base.metadata.create_all(bind=engine)  # creates companies etc. -- not integration_connections
    with engine.begin() as conn:
        for statement in integration_connections_migration_statements():
            conn.execute(text(statement))

    with Session(engine) as session:
        company = Company(name="Integration Connection Test Co Ltd")
        session.add(company)
        session.commit()
        company_id = company.id

    def _reset() -> None:
        with engine.begin() as conn:
            conn.execute(
                text("DELETE FROM integration_connections WHERE company_id = :id"),
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


def _insert_connection(engine, company_id: int, **overrides) -> None:
    import datetime

    defaults = dict(
        company_id=company_id,
        provider="jira",
        nango_environment="dev",
        nango_provider_config_key="jira-cloud",
        nango_connection_id="conn-opaque-string-not-a-uuid",
        external_org_id="jira-site-123",
        status="active",
        created_at=datetime.datetime.now(datetime.timezone.utc),
    )
    defaults.update(overrides)
    with engine.begin() as conn:
        conn.execute(integration_connections.insert().values(**defaults))


def _fetch_one(engine, company_id: int):
    with Session(engine) as session:
        row = session.execute(
            select(integration_connections).where(
                integration_connections.c.company_id == company_id
            )
        ).mappings().one()
    return row


def test_insert_minimal_active_connection_succeeds(connections_db) -> None:
    engine, company_id = connections_db
    _insert_connection(engine, company_id)

    row = _fetch_one(engine, company_id)

    assert row["status"] == "active"
    assert row["revoked_at"] is None
    assert row["allowed_projects"] == []
    # Opaque string preserved exactly -- never reformatted/parsed as a UUID.
    assert row["nango_connection_id"] == "conn-opaque-string-not-a-uuid"


def test_nango_identity_uniqueness_is_scoped_to_the_triple(connections_db) -> None:
    """Same nango_connection_id is allowed to repeat across DIFFERENT
    environments or provider configs -- uniqueness is the documented
    (environment, provider_config_key, connection_id) triple, not a bare
    global uniqueness on connection_id alone (architecture review
    correction 2)."""
    engine, company_id = connections_db
    _insert_connection(
        engine,
        company_id,
        nango_environment="dev",
        nango_provider_config_key="jira-cloud",
        nango_connection_id="shared-id",
    )
    # Different environment, same connection_id + provider config -- must succeed.
    _insert_connection(
        engine,
        company_id,
        nango_environment="prod",
        nango_provider_config_key="jira-cloud",
        nango_connection_id="shared-id",
    )

    with Session(engine) as session:
        count = session.execute(
            select(integration_connections).where(
                integration_connections.c.company_id == company_id,
                integration_connections.c.nango_connection_id == "shared-id",
            )
        ).mappings().all()
    assert len(count) == 2


def test_duplicate_full_identity_triple_is_rejected(connections_db) -> None:
    engine, company_id = connections_db
    _insert_connection(
        engine,
        company_id,
        nango_environment="dev",
        nango_provider_config_key="jira-cloud",
        nango_connection_id="dup-id",
    )
    with pytest.raises(IntegrityError):
        _insert_connection(
            engine,
            company_id,
            nango_environment="dev",
            nango_provider_config_key="jira-cloud",
            nango_connection_id="dup-id",
        )


def test_invalid_status_value_is_rejected(connections_db) -> None:
    engine, company_id = connections_db
    with pytest.raises(IntegrityError):
        _insert_connection(engine, company_id, status="not_a_real_status")


def test_revoked_status_without_revoked_at_is_rejected(connections_db) -> None:
    """Status/revoked_at coherence CHECK: 'revoked' without a timestamp,
    or 'active' WITH one, must both be rejected -- these two columns can
    never disagree about whether the connection is revoked."""
    engine, company_id = connections_db
    with pytest.raises(IntegrityError):
        _insert_connection(engine, company_id, status="revoked", revoked_at=None)


def test_active_status_with_revoked_at_set_is_rejected(connections_db) -> None:
    import datetime

    engine, company_id = connections_db
    with pytest.raises(IntegrityError):
        _insert_connection(
            engine,
            company_id,
            status="active",
            revoked_at=datetime.datetime.now(datetime.timezone.utc),
        )


def test_allowed_projects_defaults_to_empty_array_meaning_deny_all(connections_db) -> None:
    """Semantics fixed by review: allowed_projects = [] means NO project
    is authorized -- a deny-all default, never 'unrestricted'. Also
    confirms the default is a real JSON array, not SQL NULL (a careless
    `if not row.allowed_projects` must see an empty list either way, but
    at the schema level NULL and '[]' are different things, and only
    '[]' satisfies the array-type CHECK below)."""
    engine, company_id = connections_db
    _insert_connection(engine, company_id)

    row = _fetch_one(engine, company_id)
    assert row["allowed_projects"] == []
    assert row["allowed_projects"] is not None
    # Nothing is authorized when the list is empty -- this is the
    # documented application-level contract (enforced for real by the
    # executor PR, which does not exist yet); here we only prove the
    # column holds exactly this value, not a sentinel like None/"*".
    assert row["allowed_projects"] != "*"
    assert row["allowed_projects"] != None  # noqa: E711 -- explicit, not NULL


@pytest.mark.parametrize(
    "bad_value",
    [
        '{"project_key": "ABC"}',  # object, not array
        '"ABC"',  # bare string
        "42",  # bare number
        "null",  # JSON null, distinct from SQL NULL and from []
    ],
)
def test_allowed_projects_must_be_a_json_array_not_other_json_types(
    connections_db, bad_value: str
) -> None:
    """Architecture review correction: allowed_projects must always hold
    a JSON array (ck_integration_connections_allowed_projects_is_array).
    An object, a bare scalar, or JSON null must all be rejected -- none
    of them can be silently reinterpreted as either 'deny-all' or
    'allow-all'."""
    engine, company_id = connections_db
    with engine.begin() as conn:
        with pytest.raises(IntegrityError):
            conn.execute(
                text(
                    "INSERT INTO integration_connections "
                    "(company_id, provider, nango_environment, nango_provider_config_key, "
                    " nango_connection_id, external_org_id, allowed_projects, status, created_at) "
                    "VALUES (:company_id, 'jira', 'dev', 'jira-cloud', 'bad-array-test', "
                    " 'site-1', CAST(:bad_value AS jsonb), 'active', now())"
                ),
                {"company_id": company_id, "bad_value": bad_value},
            )


def test_allowed_projects_empty_array_is_accepted_by_the_check(connections_db) -> None:
    """Sanity check that the array-type CHECK does not reject the
    legitimate deny-all default itself."""
    engine, company_id = connections_db
    _insert_connection(engine, company_id, allowed_projects=[])
    row = _fetch_one(engine, company_id)
    assert row["allowed_projects"] == []


def test_allowed_projects_with_one_documented_entry_shape_is_accepted(connections_db) -> None:
    engine, company_id = connections_db
    _insert_connection(
        engine,
        company_id,
        allowed_projects=[{"project_key": "ENG", "resource_type": "project"}],
    )
    row = _fetch_one(engine, company_id)
    assert row["allowed_projects"] == [{"project_key": "ENG", "resource_type": "project"}]
    assert row.allowed_projects is not None
