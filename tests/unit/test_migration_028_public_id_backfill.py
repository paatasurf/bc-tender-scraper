"""Regression tests for migration 028's public_id backfill.

Extracts and executes the actual backfill SQL from the migration file
(between the ``-- BEGIN BACKFILL`` / ``-- END BACKFILL`` markers) rather than
a hand-copied duplicate, so these tests always exercise the real, current
migration content — not a copy that could silently drift from it.

Requires a local Postgres (same local_db_session convention as
tests/unit/test_registry_gateway.py); skipped when unavailable.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "db"
    / "migrations"
    / "028_registry_engine_foundations.sql"
)

TS_ID_RE = re.compile(r"^TS-(\d{8,})$")


def _extract_backfill_sql() -> str:
    content = MIGRATION_PATH.read_text(encoding="utf-8")
    start_marker = "-- BEGIN BACKFILL"
    end_marker = "-- END BACKFILL"
    start = content.index(start_marker) + len(start_marker)
    end = content.index(end_marker)
    return content[start:end].strip()


BACKFILL_SQL = _extract_backfill_sql()


def _require_local_database_url() -> str:
    from tests.db_test_safety import _ci_skips_db_integration

    if _ci_skips_db_integration():
        pytest.skip("DB integration tests skipped on CI")
    database_url = os.getenv("DATABASE_URL", "")
    if not database_url:
        pytest.skip("DATABASE_URL not configured")
    lowered = database_url.lower()
    if any(token in lowered for token in ("railway", "rlwy.net", "production")):
        pytest.skip("Refusing migration tests against production DATABASE_URL")
    return database_url


@pytest.fixture()
def local_db_session():
    import config.env  # noqa: F401
    from db.connection import init_db
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker

    database_url = _require_local_database_url()
    engine = create_engine(database_url, connect_args={"connect_timeout": 3})
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:
        pytest.skip("Local Postgres unavailable")

    init_db()
    factory = sessionmaker(bind=engine)
    session = factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _make_company(
    session,
    *,
    name,
    public_id=None,
    entity_role=None,
    registry_status=None,
):
    from db.models import Company

    kwargs = {"name": name, "display_name": name, "public_id": public_id}
    if entity_role is not None:
        kwargs["entity_role"] = entity_role
    if registry_status is not None:
        kwargs["registry_status"] = registry_status
    company = Company(**kwargs)
    session.add(company)
    session.flush()
    return company


def _run_backfill(session):
    """Execute the backfill within the caller's own transaction — never
    commits. Per-test isolation relies on the local_db_session fixture's
    rollback-on-teardown; committing here would leak hardcoded literal
    public_id test fixtures across tests sharing the same disposable
    database within a single pytest run.
    """
    from sqlalchemy import text

    session.execute(text(BACKFILL_SQL))
    session.flush()
    # The backfill is raw SQL — already-loaded ORM objects in this session's
    # identity map won't pick up the UPDATE's new values on their own
    # without an explicit expire (no commit happens here; see docstring).
    session.expire_all()


def _public_ids(session, company_ids):
    from db.models import Company

    rows = session.query(Company).filter(Company.id.in_(company_ids)).all()
    return {c.id: c.public_id for c in rows}


# --- structural check: advisory lock present in the extracted SQL -----------


def test_backfill_sql_contains_advisory_lock():
    assert "pg_advisory_xact_lock" in BACKFILL_SQL


# --- 1. clean initial backfill -------------------------------------------------


def test_clean_initial_backfill(local_db_session):
    companies = [
        _make_company(local_db_session, name=f"Clean Co {i}") for i in range(5)
    ]
    ids = [c.id for c in companies]

    _run_backfill(local_db_session)

    assigned = _public_ids(local_db_session, ids)
    for cid in ids:
        assert TS_ID_RE.match(assigned[cid])
    # Ascending company id -> ascending sequence number.
    ordered_ids = sorted(ids)
    seqs = [int(TS_ID_RE.match(assigned[cid]).group(1)) for cid in ordered_ids]
    assert seqs == sorted(seqs)
    assert len(set(seqs)) == len(seqs)


# --- 2. re-run is a no-op -------------------------------------------------------


def test_rerun_is_noop(local_db_session):
    companies = [
        _make_company(local_db_session, name=f"Rerun Co {i}") for i in range(4)
    ]
    ids = [c.id for c in companies]

    _run_backfill(local_db_session)
    first_pass = _public_ids(local_db_session, ids)

    _run_backfill(local_db_session)
    second_pass = _public_ids(local_db_session, ids)

    assert first_pass == second_pass


# --- 3. partial state: some already assigned, some NULL -----------------------


def test_partial_state_mixed_assigned_and_null(local_db_session):
    already = _make_company(
        local_db_session, name="Already Assigned Co", public_id="TS-00000042"
    )
    fresh = [_make_company(local_db_session, name=f"Fresh Co {i}") for i in range(3)]

    _run_backfill(local_db_session)

    result = _public_ids(local_db_session, [already.id] + [c.id for c in fresh])
    assert result[already.id] == "TS-00000042"  # untouched
    fresh_values = [result[c.id] for c in fresh]
    assert all(TS_ID_RE.match(v) for v in fresh_values)
    assert "TS-00000042" not in fresh_values
    assert len(set(fresh_values)) == len(fresh_values)


# --- 4. gaps in company ids -----------------------------------------------------


def test_gaps_in_company_ids(local_db_session):
    from db.models import Company

    keep_a = _make_company(local_db_session, name="Gap Keep A")
    to_delete = _make_company(local_db_session, name="Gap Delete Me")
    keep_b = _make_company(local_db_session, name="Gap Keep B")
    gap_id = to_delete.id
    local_db_session.delete(to_delete)
    local_db_session.flush()
    assert (
        local_db_session.query(Company).filter(Company.id == gap_id).one_or_none()
        is None
    )

    _run_backfill(local_db_session)

    result = _public_ids(local_db_session, [keep_a.id, keep_b.id])
    seq_a = int(TS_ID_RE.match(result[keep_a.id]).group(1))
    seq_b = int(TS_ID_RE.match(result[keep_b.id]).group(1))
    # Dense sequence numbering despite the gap in the underlying id column.
    assert seq_b == seq_a + 1


# --- 5. existing max ts suffix is respected ------------------------------------


def test_existing_max_ts_suffix_respected(local_db_session):
    from sqlalchemy import text

    _make_company(local_db_session, name="Max Suffix Existing", public_id="TS-00000100")
    new_companies = [
        _make_company(local_db_session, name=f"Max Suffix New {i}") for i in range(2)
    ]

    # The companies table is shared with every other test in this
    # session/database -- this test's own "TS-00000100" is only a floor
    # it guarantees is present, not necessarily the table's real max, so
    # read the actual pre-existing max suffix rather than assuming this
    # test is the sole source of already-assigned public_ids.
    existing_public_ids = (
        local_db_session.execute(
            text("SELECT public_id FROM companies WHERE public_id IS NOT NULL")
        )
        .scalars()
        .all()
    )
    max_before = max(
        (
            int(TS_ID_RE.match(v).group(1))
            for v in existing_public_ids
            if TS_ID_RE.match(v)
        ),
        default=0,
    )
    assert max_before >= 100  # this test's own row is always at least this

    _run_backfill(local_db_session)

    result = _public_ids(local_db_session, [c.id for c in new_companies])
    new_seqs = sorted(int(TS_ID_RE.match(v).group(1)) for v in result.values())
    # A shared, non-empty table may hold OTHER not-yet-backfilled companies
    # too, which legitimately consume sequence numbers between max_before
    # and this test's own two -- so assert the actual invariant ("respected"
    # means never reused/restarted below the existing max, and dense/unique
    # among themselves), not an exact continuation this test cannot control.
    assert new_seqs[0] > max_before
    assert new_seqs[1] > new_seqs[0]


# --- 6. already-assigned ids are never changed ---------------------------------


def test_already_assigned_ids_never_change(local_db_session):
    pre_assigned = _make_company(
        local_db_session, name="Never Change Co", public_id="TS-00000007"
    )
    _make_company(local_db_session, name="Never Change Sibling")

    _run_backfill(local_db_session)
    _run_backfill(local_db_session)  # run twice for good measure

    result = _public_ids(local_db_session, [pre_assigned.id])
    assert result[pre_assigned.id] == "TS-00000007"


# --- 7. no duplicates after backfill -------------------------------------------


def test_no_duplicate_public_ids_after_backfill(local_db_session):
    _make_company(local_db_session, name="Dup Check Existing", public_id="TS-00000010")
    companies = [
        _make_company(local_db_session, name=f"Dup Check New {i}") for i in range(6)
    ]

    _run_backfill(local_db_session)

    from db.models import Company

    all_ids = [
        c.public_id
        for c in local_db_session.query(Company)
        .filter(Company.id.in_([c.id for c in companies] + []))
        .all()
    ]
    all_ids.append("TS-00000010")
    assert len(all_ids) == len(set(all_ids))


# --- 8. non-eligible entity_role / registry_status rows stay NULL -------------


def test_ineligible_rows_remain_public_id_null(local_db_session):
    """Requirement 2: applicant_alias, probable_person, and non-active
    registry_status rows must never receive a public_id, even though the
    prior (buggy) backfill's bare WHERE public_id IS NULL would have
    covered them.
    """
    canonical_target = _make_company(
        local_db_session, name="Alias Target Canonical", entity_role="canonical"
    )
    alias = _make_company(
        local_db_session,
        name="Ineligible Alias",
        entity_role="applicant_alias",
        registry_status="active",
    )
    probable_person = _make_company(
        local_db_session,
        name="Ineligible Probable Person",
        entity_role="probable_person",
    )
    merged_standalone = _make_company(
        local_db_session,
        name="Ineligible Merged Standalone",
        entity_role="standalone",
        registry_status="merged",
    )
    excluded_canonical = _make_company(
        local_db_session,
        name="Ineligible Excluded Canonical",
        entity_role="canonical",
        registry_status="excluded",
    )
    retired_standalone = _make_company(
        local_db_session,
        name="Ineligible Retired Standalone",
        entity_role="standalone",
        registry_status="retired",
    )

    _run_backfill(local_db_session)

    result = _public_ids(
        local_db_session,
        [
            alias.id,
            probable_person.id,
            merged_standalone.id,
            excluded_canonical.id,
            retired_standalone.id,
        ],
    )
    assert result[alias.id] is None
    assert result[probable_person.id] is None
    assert result[merged_standalone.id] is None
    assert result[excluded_canonical.id] is None
    assert result[retired_standalone.id] is None
    # The canonical target itself is eligible and unaffected by its alias existing.
    assert (
        _public_ids(local_db_session, [canonical_target.id])[canonical_target.id]
        is not None
    )


# --- 9. canonical and standalone eligible rows both get backfilled ------------


def test_canonical_and_standalone_eligible_rows_get_backfilled(local_db_session):
    """Requirement 3: both eligible entity_role values must actually receive
    a public_id — canonical (the obvious case) and standalone (the
    documented legacy-policy case).
    """
    canonical = _make_company(
        local_db_session,
        name="Eligible Canonical Co",
        entity_role="canonical",
        registry_status="active",
    )
    standalone = _make_company(
        local_db_session,
        name="Eligible Standalone Co",
        entity_role="standalone",
        registry_status="active",
    )

    _run_backfill(local_db_session)

    result = _public_ids(local_db_session, [canonical.id, standalone.id])
    assert TS_ID_RE.match(result[canonical.id])
    assert TS_ID_RE.match(result[standalone.id])
    assert result[canonical.id] != result[standalone.id]
