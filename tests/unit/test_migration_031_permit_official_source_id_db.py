"""Local-Postgres regression tests for migration 031 (Surrey official source
identity schema foundation, PR-EN1E-1) -- proves the partial unique index
and nullability contract are actually enforced by the real database, and
that the migration is additive, idempotent, and cleanly reversible.

This PR is migration-first: db/models.py does NOT map official_source_id
yet (that ORM/importer wiring is a separate, later PR, applied only after
this migration has actually run in production). So every test here talks
to the official_source_id column exclusively through raw SQL, never
through the Permit ORM class.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from db.models import Permit
from db.permit_official_source_id_ddl import (
    permit_official_source_id_migration_statements,
    permit_official_source_id_rollback_statements,
)
from tests.db_test_safety import require_local_test_database


@pytest.fixture()
def migrated_conn():
    database_url = require_local_test_database()
    engine = create_engine(database_url, connect_args={"connect_timeout": 3})
    try:
        with engine.connect() as probe:
            probe.execute(text("SELECT 1"))
    except Exception:
        pytest.skip("Local Postgres unavailable")

    conn = engine.connect()
    outer = conn.begin()
    conn.execute(text("SET LOCAL lock_timeout = '10s'"))
    for statement in permit_official_source_id_migration_statements():
        conn.execute(text(statement))
    try:
        yield conn
    finally:
        if outer.is_active:
            outer.rollback()
        conn.close()
        engine.dispose()


def _insert_bare_permit(conn, *, source: str, external_id: str) -> int:
    """Insert a permit through the ORM class (which does not know about
    official_source_id) and return its id."""
    session = Session(bind=conn)
    unique = uuid.uuid4().hex[:8]
    permit = Permit(
        address=f"{unique} EN1E1 Migration Test Avenue",
        permit_type="Commercial Alteration",
        project_value="1",
        applicant="",
        source=source,
        city="Surrey",
        external_id=external_id,
    )
    session.add(permit)
    session.flush()
    return int(permit.id)


def _set_official_source_id(conn, *, permit_id: int, value: str | None) -> None:
    conn.execute(
        text("UPDATE permits SET official_source_id = :value WHERE id = :id"),
        {"value": value, "id": permit_id},
    )


def test_null_official_source_id_is_allowed(migrated_conn):
    permit_id = _insert_bare_permit(
        migrated_conn, source="surrey", external_id="26-000001-001-00"
    )
    _set_official_source_id(migrated_conn, permit_id=permit_id, value=None)  # no error


def test_two_rows_with_same_null_official_source_id_are_allowed(migrated_conn):
    a = _insert_bare_permit(
        migrated_conn, source="surrey", external_id="26-000002-001-00"
    )
    b = _insert_bare_permit(
        migrated_conn, source="surrey", external_id="26-000003-001-00"
    )
    _set_official_source_id(migrated_conn, permit_id=a, value=None)
    _set_official_source_id(migrated_conn, permit_id=b, value=None)  # no error


def test_two_rows_with_same_empty_string_official_source_id_are_allowed(migrated_conn):
    a = _insert_bare_permit(
        migrated_conn, source="surrey", external_id="26-000004-001-00"
    )
    b = _insert_bare_permit(
        migrated_conn, source="surrey", external_id="26-000005-001-00"
    )
    _set_official_source_id(migrated_conn, permit_id=a, value="")
    _set_official_source_id(migrated_conn, permit_id=b, value="")  # no error


def test_two_rows_same_source_and_nonempty_official_source_id_are_rejected(
    migrated_conn,
):
    shared = f"26-000006-001-00/{uuid.uuid4().hex[:4]}"
    a = _insert_bare_permit(
        migrated_conn, source="surrey", external_id="26-000006-001-00"
    )
    b = _insert_bare_permit(
        migrated_conn, source="surrey", external_id="26-000007-001-00"
    )
    _set_official_source_id(migrated_conn, permit_id=a, value=shared)
    with pytest.raises(IntegrityError):
        _set_official_source_id(migrated_conn, permit_id=b, value=shared)


def test_same_official_source_id_across_different_sources_is_allowed(migrated_conn):
    shared = f"26-000008-001-00/{uuid.uuid4().hex[:4]}"
    a = _insert_bare_permit(
        migrated_conn, source="surrey", external_id="26-000008-001-00"
    )
    b = _insert_bare_permit(
        migrated_conn, source="vancouver", external_id="26-000008-001-00"
    )
    _set_official_source_id(migrated_conn, permit_id=a, value=shared)
    _set_official_source_id(migrated_conn, permit_id=b, value=shared)  # no error


def test_migration_is_additive_and_does_not_touch_external_id_or_existing_row(
    migrated_conn,
):
    """Insert a row, then re-run the (idempotent) migration statements
    again, and confirm the row's external_id and other fields are
    unaffected by a second apply."""
    permit_id = _insert_bare_permit(
        migrated_conn, source="surrey", external_id="26-000009-001-00"
    )

    for statement in permit_official_source_id_migration_statements():
        migrated_conn.execute(text(statement))

    row = migrated_conn.execute(
        text(
            "SELECT external_id, address, official_source_id FROM permits WHERE id = :id"
        ),
        {"id": permit_id},
    ).one()
    assert row.external_id == "26-000009-001-00"
    assert row.official_source_id is None


def test_forward_migration_is_idempotent_when_applied_twice_more(migrated_conn):
    for _ in range(2):
        for statement in permit_official_source_id_migration_statements():
            migrated_conn.execute(text(statement))  # must not raise


def test_rollback_removes_column_and_index_then_forward_reapplies_cleanly(
    migrated_conn,
):
    for statement in permit_official_source_id_rollback_statements():
        migrated_conn.execute(text(statement))

    exists = migrated_conn.execute(
        text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = 'permits' AND column_name = 'official_source_id'"
        )
    ).first()
    assert exists is None

    index_exists = migrated_conn.execute(
        text(
            "SELECT 1 FROM pg_indexes WHERE indexname = "
            "'ux_permits_source_official_source_id'"
        )
    ).first()
    assert index_exists is None

    # Rollback is idempotent.
    for statement in permit_official_source_id_rollback_statements():
        migrated_conn.execute(text(statement))  # must not raise

    # Forward migration re-applies cleanly after rollback.
    for statement in permit_official_source_id_migration_statements():
        migrated_conn.execute(text(statement))

    exists_again = migrated_conn.execute(
        text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = 'permits' AND column_name = 'official_source_id'"
        )
    ).first()
    assert exists_again is not None
