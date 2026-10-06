"""Proves, against a real local Postgres, that integration_connections is
NEVER created by db.connection.init_db() (which calls
Base.metadata.create_all(bind=engine) unconditionally on every call) --
only by explicitly applying db/migrations/031_integration_connections.sql.

This test exists because an earlier version of this PR mapped
IntegrationConnection as a db.models ORM class (a Base subclass), and
this exact test -- run against a disposable local Postgres -- is what
caught that init_db() DOES auto-create any Base subclass, contradicting
the "stays inert" claim. The fix moved the table definition to a private
MetaData() (db/integration_connections_tables.py); this test is the
regression guard for that fix, not just documentation of it.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text

from db.integration_connections_ddl import integration_connections_migration_statements
from tests.db_test_safety import require_local_test_database


def _table_exists(engine, table_name: str) -> bool:
    with engine.connect() as conn:
        result = conn.execute(
            text(
                "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = :name)"
            ),
            {"name": table_name},
        )
        return bool(result.scalar())


@pytest.fixture
def clean_db_engine():
    database_url = require_local_test_database()
    engine = create_engine(database_url, connect_args={"connect_timeout": 3})
    try:
        with engine.connect() as probe:
            probe.execute(text("SELECT 1"))
    except Exception:
        pytest.skip("Local Postgres unavailable")

    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS integration_connections CASCADE"))

    try:
        yield engine
    finally:
        with engine.begin() as conn:
            conn.execute(text("DROP TABLE IF EXISTS integration_connections CASCADE"))
        engine.dispose()


def test_init_db_does_not_create_integration_connections(clean_db_engine) -> None:
    """Calls the EXACT sequence db.connection.init_db() uses
    (import db.models to register all ORM classes, then
    Base.metadata.create_all(bind=engine)) and asserts
    integration_connections does not appear -- because it is no longer a
    Base subclass at all."""
    import db.models  # noqa: F401  # registers all ORM models, mirrors init_db()'s own import

    assert _table_exists(clean_db_engine, "integration_connections") is False

    db.models.Base.metadata.create_all(bind=clean_db_engine)

    assert _table_exists(clean_db_engine, "integration_connections") is False
    # Sanity: create_all still does its normal job for real ORM tables --
    # proves this isn't a broken/no-op create_all call.
    assert _table_exists(clean_db_engine, "companies") is True


def test_explicit_migration_apply_is_the_only_way_the_table_appears(clean_db_engine) -> None:
    import db.models  # noqa: F401

    db.models.Base.metadata.create_all(bind=clean_db_engine)
    assert _table_exists(clean_db_engine, "integration_connections") is False

    with clean_db_engine.begin() as conn:
        for statement in integration_connections_migration_statements():
            conn.execute(text(statement))

    assert _table_exists(clean_db_engine, "integration_connections") is True
