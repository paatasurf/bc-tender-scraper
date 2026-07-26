"""Unit tests for permit import helpers."""

from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session, sessionmaker

from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import CompileError

from db.models import Permit
from db.permit_import import (
    _IMPORTABLE_COLUMNS,
    _SKIP_ON_UPDATE,
    _clamp_permit_row,
    _dedupe_permit_rows,
    _importable_row_values,
    _promote_blank_permit_if_exists,
    upsert_city_permits,
)


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
        pytest.skip("Refusing permit import tests against production DATABASE_URL")
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
        pytest.skip("Local Postgres unavailable for permit import integration test")

    init_db()
    factory = sessionmaker(bind=engine)
    session = factory()
    try:
        yield session
    finally:
        session.close()


def test_promote_blank_permit_if_exists_updates_blank_row():
    session = MagicMock()
    session.scalar.side_effect = [None, 4317394]
    row = {
        "external_id": "BP-2025-00202",
        "address": "3380 VANNESS AVENUE, Vancouver, BC V5R 6B8",
        "project_value": "144787150.0",
        "applicant": "Tijana Sljivic",
        "contractor": "Axiom Builders Inc",
        "source": "vancouver",
        "city": "Vancouver",
    }
    promoted = _promote_blank_permit_if_exists(session, row, source="vancouver")
    assert promoted is True
    session.execute.assert_called_once()


def test_promote_blank_permit_skips_when_keyed_external_id_already_exists():
    session = MagicMock()
    session.scalar.return_value = 4315436
    row = {
        "external_id": "BP-2026-02400",
        "address": "1501 HARO STREET, Vancouver, BC V6G 1G4",
        "project_value": "40000.0",
        "applicant": "McCuaig and Associates Engineering Ltd.",
        "contractor": "Solid General Contractors Inc",
        "source": "vancouver",
        "city": "Vancouver",
    }

    promoted = _promote_blank_permit_if_exists(session, row, source="vancouver")

    assert promoted is False
    session.execute.assert_not_called()
    assert session.scalar.call_count == 1


def test_dedupe_permit_rows_keeps_last_duplicate():
    rows = [
        {"source": "surrey", "external_id": "A", "address": "1 Main"},
        {"source": "surrey", "external_id": "A", "address": "1 Main Updated"},
        {"source": "surrey", "external_id": "B", "address": "2 Main"},
    ]
    keyed, blank = _dedupe_permit_rows(rows)
    assert len(keyed) == 2
    assert blank == []
    by_id = {row["external_id"]: row for row in keyed}
    assert by_id["A"]["address"] == "1 Main Updated"


def test_dedupe_permit_rows_keeps_blank_fingerprint_once():
    rows = [
        {
            "source": "vancouver",
            "external_id": "",
            "address": "3380 VANNESS AVENUE",
            "project_value": "144787150.0",
            "applicant": "Tijana Sljivic",
        },
        {
            "source": "vancouver",
            "external_id": "",
            "address": "3380 VANNESS AVENUE",
            "project_value": "144787150.0",
            "applicant": "Tijana Sljivic",
        },
    ]
    keyed, blank = _dedupe_permit_rows(rows)
    assert keyed == []
    assert len(blank) == 1


def _mixed_company_resolution_batch() -> list[dict[str, object]]:
    """Simulate post-_attach_company_ids rows: one resolved, one unresolved."""
    base = {
        "external_id": "REPRO-1",
        "address": "1 Main St",
        "permit_type": "",
        "project_value": "",
        "applicant": "",
        "issue_date": "",
        "application_date": "",
        "description": "",
        "contractor": "",
        "local_area": "",
        "source": "vancouver",
        "city": "Vancouver",
    }
    resolved = {
        **base,
        "external_id": "REPRO-RESOLVED",
        "company_id": 123,
        "canonical_merge_confidence": 0.9,
        "canonical_merge_method": "exact_name",
    }
    unresolved = {**base, "external_id": "REPRO-UNRESOLVED"}
    return [resolved, unresolved]


def _compile_keyed_permit_upsert(batch: list[dict[str, object]]) -> None:
    from sqlalchemy.dialects.postgresql import insert

    stmt = insert(Permit.__table__).values(batch)
    update_cols = {
        col.name: stmt.excluded[col.name]
        for col in Permit.__table__.columns
        if col.name not in _SKIP_ON_UPDATE
    }
    stmt = stmt.on_conflict_do_update(
        index_elements=["source", "external_id"],
        index_where=text("external_id <> ''"),
        set_=update_cols,
    )
    stmt.compile(dialect=postgresql.dialect())


def test_mixed_key_permit_batch_fails_sqlalchemy_compile():
    """Regression: raw keyed rows after company resolution have uneven dict keys."""
    batch = _mixed_company_resolution_batch()
    assert len({tuple(sorted(row.keys())) for row in batch}) == 2

    with pytest.raises(CompileError, match="company_id"):
        _compile_keyed_permit_upsert(batch)


def test_importable_row_values_normalizes_mixed_key_batch_for_insert():
    """Regression: batch loop must map rows through _importable_row_values before insert."""
    source = "vancouver"
    batch = [
        _importable_row_values(row, source=source)
        for row in _mixed_company_resolution_batch()
    ]

    assert all(set(row.keys()) == _IMPORTABLE_COLUMNS for row in batch)
    assert batch[0]["company_id"] == 123
    assert batch[1]["company_id"] is None
    assert batch[1]["canonical_merge_confidence"] is None
    assert batch[1]["canonical_merge_method"] is None

    _compile_keyed_permit_upsert(batch)


def test_clamp_permit_row_truncates_varchar_fields():
    row = {
        "source": "vancouver",
        "external_id": "BP-1",
        "address": "x" * 350,
        "contractor": "y" * 400,
        "description": "z" * 500,
    }
    clamped = _clamp_permit_row(row)
    assert len(clamped["address"]) == 300
    assert len(clamped["contractor"]) == 300
    assert len(clamped["description"]) == 500


def test_upsert_city_permits_promotes_blank_row_when_external_id_arrives(
    local_db_session: Session,
):
    address = "750 W 32ND AVENUE, Vancouver, BC"
    project_value = "200000000.0"
    applicant = "Tavis McAuley DBA: McAuley Consulting"
    external_id = "BP-TEST-DEDUP-1"

    local_db_session.execute(
        text("""
            DELETE FROM permits
            WHERE source = 'vancouver'
              AND (
                external_id = :external_id
                OR (
                  address = :address
                  AND project_value = :project_value
                  AND applicant = :applicant
                )
              )
            """),
        {
            "external_id": external_id,
            "address": address,
            "project_value": project_value,
            "applicant": applicant,
        },
    )
    local_db_session.commit()

    base_row = {
        "external_id": "",
        "address": address,
        "permit_type": "New Building",
        "project_value": project_value,
        "applicant": applicant,
        "issue_date": "2026-04-17",
        "application_date": "",
        "description": "Stage 1 only",
        "contractor": "",
        "local_area": "",
        "source": "vancouver",
        "city": "Vancouver",
    }
    upsert_city_permits(
        local_db_session, [base_row], source="vancouver", full_refresh=False
    )

    keyed_row = {
        **base_row,
        "external_id": external_id,
        "application_date": "2024-11-07",
        "contractor": "Scott Construction Ltd",
        "description": "Stage 1/2/3 full permit",
    }
    upsert_city_permits(
        local_db_session, [keyed_row], source="vancouver", full_refresh=False
    )

    count = local_db_session.scalar(
        select(func.count())
        .select_from(Permit)
        .where(
            Permit.source == "vancouver",
            Permit.address == address,
            Permit.project_value == project_value,
            Permit.applicant == applicant,
        )
    )
    row = local_db_session.scalar(
        select(Permit).where(
            Permit.source == "vancouver",
            Permit.external_id == external_id,
        )
    )

    assert count == 1
    assert row is not None
    assert row.contractor == "Scott Construction Ltd"
    assert row.application_date == "2024-11-07"
    assert row.description == "Stage 1/2/3 full permit"

    local_db_session.execute(
        text(
            "DELETE FROM permits WHERE source = 'vancouver' AND external_id = :external_id"
        ),
        {"external_id": external_id},
    )
    local_db_session.commit()


def test_permit_orm_maps_official_source_id_nullable_string():
    column = Permit.__table__.columns["official_source_id"]
    assert column.nullable is True
    assert column.type.length == 100


def test_official_source_id_is_excluded_from_generic_import_columns():
    """official_source_id must be writable only by a dedicated, digest-pinned
    identity-bridge writer -- never by the generic scraper upsert, on insert
    or conflict-update."""
    assert "official_source_id" not in _IMPORTABLE_COLUMNS
    assert "official_source_id" in _SKIP_ON_UPDATE


def _official_source_id_probe_row(*, external_id: str) -> dict[str, object]:
    return {
        "external_id": external_id,
        "address": "1 Identity Bridge Test Lane",
        "permit_type": "New Building",
        "project_value": "1",
        "applicant": "",
        "issue_date": "2026-07-21",
        "application_date": "",
        "description": "",
        "contractor": "",
        "local_area": "",
        "source": "surrey",
        "city": "Surrey",
    }


@pytest.fixture()
def official_source_id_probe(local_db_session: Session):
    external_id = "26-999901-001-00"
    local_db_session.execute(
        text(
            "DELETE FROM permits WHERE source = 'surrey' AND external_id = :external_id"
        ),
        {"external_id": external_id},
    )
    local_db_session.commit()
    try:
        yield external_id
    finally:
        local_db_session.execute(
            text(
                "DELETE FROM permits WHERE source = 'surrey' AND external_id = :external_id"
            ),
            {"external_id": external_id},
        )
        local_db_session.commit()


def _seed_permit_with_official_source_id(
    session: Session, *, external_id: str, official_source_id: str
) -> None:
    upsert_city_permits(
        session,
        [_official_source_id_probe_row(external_id=external_id)],
        source="surrey",
        full_refresh=False,
    )
    session.execute(
        text(
            "UPDATE permits SET official_source_id = :value "
            "WHERE source = 'surrey' AND external_id = :external_id"
        ),
        {"value": official_source_id, "external_id": external_id},
    )
    session.commit()


def test_upsert_preserves_official_source_id_when_row_omits_key(
    local_db_session: Session, official_source_id_probe: str
):
    external_id = official_source_id_probe
    seeded = f"{external_id}/AB"
    _seed_permit_with_official_source_id(
        local_db_session, external_id=external_id, official_source_id=seeded
    )

    incoming = _official_source_id_probe_row(external_id=external_id)
    assert "official_source_id" not in incoming
    upsert_city_permits(
        local_db_session, [incoming], source="surrey", full_refresh=False
    )

    row = local_db_session.execute(
        text(
            "SELECT official_source_id, external_id FROM permits "
            "WHERE source = 'surrey' AND external_id = :external_id"
        ),
        {"external_id": external_id},
    ).one()
    assert row.official_source_id == seeded
    assert row.external_id == external_id


def test_upsert_preserves_official_source_id_when_incoming_is_none(
    local_db_session: Session, official_source_id_probe: str
):
    external_id = official_source_id_probe
    seeded = f"{external_id}/CD"
    _seed_permit_with_official_source_id(
        local_db_session, external_id=external_id, official_source_id=seeded
    )

    incoming = {
        **_official_source_id_probe_row(external_id=external_id),
        "official_source_id": None,
    }
    upsert_city_permits(
        local_db_session, [incoming], source="surrey", full_refresh=False
    )

    row = local_db_session.execute(
        text(
            "SELECT official_source_id FROM permits "
            "WHERE source = 'surrey' AND external_id = :external_id"
        ),
        {"external_id": external_id},
    ).one()
    assert row.official_source_id == seeded


def test_upsert_preserves_official_source_id_when_incoming_is_empty_string(
    local_db_session: Session, official_source_id_probe: str
):
    external_id = official_source_id_probe
    seeded = f"{external_id}/EF"
    _seed_permit_with_official_source_id(
        local_db_session, external_id=external_id, official_source_id=seeded
    )

    incoming = {
        **_official_source_id_probe_row(external_id=external_id),
        "official_source_id": "",
    }
    upsert_city_permits(
        local_db_session, [incoming], source="surrey", full_refresh=False
    )

    row = local_db_session.execute(
        text(
            "SELECT official_source_id FROM permits "
            "WHERE source = 'surrey' AND external_id = :external_id"
        ),
        {"external_id": external_id},
    ).one()
    assert row.official_source_id == seeded


def test_upsert_insert_behavior_is_still_correct_after_the_skip_change(
    local_db_session: Session, official_source_id_probe: str
):
    """Regression: excluding official_source_id from _IMPORTABLE_COLUMNS must
    not disturb any other column's normal insert/update behavior."""
    external_id = official_source_id_probe
    upsert_city_permits(
        local_db_session,
        [_official_source_id_probe_row(external_id=external_id)],
        source="surrey",
        full_refresh=False,
    )
    updated = {
        **_official_source_id_probe_row(external_id=external_id),
        "contractor": "Bridge Foundation Contracting Ltd",
        "application_date": "2026-07-22",
    }
    upsert_city_permits(
        local_db_session, [updated], source="surrey", full_refresh=False
    )

    row = local_db_session.execute(
        text(
            "SELECT external_id, contractor, application_date, official_source_id "
            "FROM permits WHERE source = 'surrey' AND external_id = :external_id"
        ),
        {"external_id": external_id},
    ).one()
    assert row.external_id == external_id
    assert row.contractor == "Bridge Foundation Contracting Ltd"
    assert row.application_date == "2026-07-22"
    assert row.official_source_id is None
