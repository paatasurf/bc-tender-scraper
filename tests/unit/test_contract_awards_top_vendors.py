"""Tests for GET /api/contract-awards/top-vendors's Engine-1 fix (M3H):

matched awards are now grouped by ContractAward.company_id and joined to
Company, so the same real company no longer fragments across every raw
winner_company spelling. Unmatched awards remain their own rows.

Real-DB integration tests only, mirroring
tests/unit/test_merx_architecture_freshness.py -- whether repeat-inserted
awards under different company_id groupings consolidate correctly is
genuine PostgreSQL GROUP BY / JOIN behavior and cannot be meaningfully
verified against a mock. Skipped on CI and against any non-local
DATABASE_URL.

api/main.py::contract_awards_top_vendors() itself is called directly as a
plain function (bypassing the FastAPI Query() parameter machinery, which
only matters when routed through HTTP) -- this exercises the exact same
get_session()/query code the real endpoint runs, against real data.

Local Postgres in this environment may carry pre-existing committed
companies/contract_awards rows, so every test here uses a unique company
name / external_id (via _uid()) and deletes only its own rows before
starting -- never a blanket DELETE.
"""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from api.main import contract_awards_top_vendors
from db.models import Company, ContractAward

_TEST_SOURCE = "top_vendors_fix_test"


def _uid() -> str:
    return uuid.uuid4().hex[:12]


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
            "Refusing top-vendors integration tests against production DATABASE_URL"
        )
    return database_url


@pytest.fixture()
def local_db_session() -> Session:
    import config.env  # noqa: F401
    from db.connection import init_db

    _require_local_database_url()
    init_db()
    engine = create_engine(os.environ["DATABASE_URL"])
    factory = sessionmaker(bind=engine)
    session = factory()
    try:
        yield session
    finally:
        session.close()


def _make_company(session: Session, *, name: str, display_name: str = "") -> int:
    company = Company(name=name, display_name=display_name)
    session.add(company)
    session.commit()
    session.refresh(company)
    return company.id


def _make_award(
    session: Session,
    *,
    external_id: str,
    winner_company: str,
    award_value: float,
    company_id: int | None = None,
) -> None:
    session.add(
        ContractAward(
            source=_TEST_SOURCE,
            external_id=external_id,
            title=f"Test award {external_id}",
            winner_company=winner_company,
            award_value=award_value,
            company_id=company_id,
        )
    )
    session.commit()


def _cleanup(
    session: Session, *, company_names: list[str], external_ids: list[str]
) -> None:
    if external_ids:
        session.execute(
            text(
                "DELETE FROM contract_awards WHERE source = :source "
                "AND external_id = ANY(:external_ids)"
            ),
            {"source": _TEST_SOURCE, "external_ids": external_ids},
        )
    if company_names:
        session.execute(
            text("DELETE FROM companies WHERE name = ANY(:names)"),
            {"names": company_names},
        )
    session.commit()


def _find(data: list[dict], **predicate) -> dict:
    for item in data:
        if all(item.get(key) == value for key, value in predicate.items()):
            return item
    raise AssertionError(f"No item matching {predicate} in {data}")


def test_variant_winner_names_consolidate_into_one_matched_row(
    local_db_session: Session,
):
    """Proof (a): two awards with different raw winner_company strings but
    the same company_id must appear as ONE row, with summed counts/value --
    this is the actual fragmentation fix."""
    name = f"Consolidation Test Co {_uid()}"
    ext_a, ext_b = f"a-{_uid()}", f"b-{_uid()}"
    company_id = _make_company(local_db_session, name=name)
    try:
        _make_award(
            local_db_session,
            external_id=ext_a,
            winner_company=f"{name} Inc.",
            award_value=100000.0,
            company_id=company_id,
        )
        _make_award(
            local_db_session,
            external_id=ext_b,
            winner_company=f"{name} Incorporated",
            award_value=50000.0,
            company_id=company_id,
        )

        result = contract_awards_top_vendors(limit=100)
        row = _find(result["data"], company_id=company_id)

        assert row["matched"] is True
        assert row["award_count"] == 2
        assert row["total_value"] == 150000.0
        assert row["company_name"] == name
        # Deterministic representative raw value -- alphabetically smallest.
        assert row["vendor"] == f"{name} Inc."
        # Only one row for this company_id, not two.
        assert (
            len([d for d in result["data"] if d.get("company_id") == company_id]) == 1
        )
    finally:
        _cleanup(local_db_session, company_names=[name], external_ids=[ext_a, ext_b])


def test_unmatched_award_stays_visible_and_separate(local_db_session: Session):
    """Proof (b): an award with no resolved company_id must remain visible
    (matched=false, company_id=null) and must never be blended into any
    matched company's totals."""
    vendor = f"Unmatched Vendor {_uid()}"
    ext = f"u-{_uid()}"
    try:
        _make_award(
            local_db_session,
            external_id=ext,
            winner_company=vendor,
            award_value=25000.0,
            company_id=None,
        )

        result = contract_awards_top_vendors(limit=100)
        row = _find(result["data"], vendor=vendor)

        assert row["matched"] is False
        assert row["company_id"] is None
        assert row["award_count"] == 1
        assert row["total_value"] == 25000.0
        assert "company_name" not in row
    finally:
        _cleanup(local_db_session, company_names=[], external_ids=[ext])


def test_blank_display_name_falls_back_to_company_name(local_db_session: Session):
    """Proof (c): a matched company with an empty/blank display_name must
    show company_name = Company.name -- the same fallback convention
    already used by _company_to_api_dict()."""
    name = f"Fallback Test Co {_uid()}"
    ext = f"f-{_uid()}"
    company_id = _make_company(local_db_session, name=name, display_name="   ")
    try:
        _make_award(
            local_db_session,
            external_id=ext,
            winner_company=name,
            award_value=10000.0,
            company_id=company_id,
        )

        result = contract_awards_top_vendors(limit=100)
        row = _find(result["data"], company_id=company_id)

        assert row["company_name"] == name
    finally:
        _cleanup(local_db_session, company_names=[name], external_ids=[ext])


def test_response_contract_unchanged_total_limit_data_and_legacy_keys(
    local_db_session: Session,
):
    """Proof (d): the pre-existing response contract survives -- top-level
    total/limit/data, and every item still exposes
    vendor/award_count/total_value/company_id/matched, whether matched or
    not."""
    name = f"Contract Test Co {_uid()}"
    ext_matched, ext_unmatched = f"cm-{_uid()}", f"cu-{_uid()}"
    company_id = _make_company(local_db_session, name=name)
    try:
        _make_award(
            local_db_session,
            external_id=ext_matched,
            winner_company=name,
            award_value=1000.0,
            company_id=company_id,
        )
        _make_award(
            local_db_session,
            external_id=ext_unmatched,
            winner_company=f"Unresolved {_uid()}",
            award_value=1000.0,
            company_id=None,
        )

        result = contract_awards_top_vendors(limit=100)

        assert set(result.keys()) == {"total", "limit", "data"}
        assert isinstance(result["data"], list)
        assert result["total"] == len(result["data"])
        assert result["limit"] == 100

        matched_row = _find(result["data"], company_id=company_id)
        for key in ("vendor", "award_count", "total_value", "company_id", "matched"):
            assert key in matched_row

        unmatched_candidates = [
            d
            for d in result["data"]
            if d.get("matched") is False
            and d.get("company_id") is None
            and d.get("total_value") == 1000.0
        ]
        assert unmatched_candidates, "expected the unmatched test row to be present"
        for key in ("vendor", "award_count", "total_value", "company_id", "matched"):
            assert key in unmatched_candidates[0]
    finally:
        _cleanup(
            local_db_session,
            company_names=[name],
            external_ids=[ext_matched, ext_unmatched],
        )


def test_ordering_is_deterministic(local_db_session: Session):
    """Proof (e): rows are ordered by total_value desc, then award_count
    desc, then a stable name/id tie-breaker -- never DB row order."""
    name_hi = f"Ordering Hi {_uid()}"
    name_lo = f"Ordering Lo {_uid()}"
    ext_hi, ext_lo = f"oh-{_uid()}", f"ol-{_uid()}"
    company_hi = _make_company(local_db_session, name=name_hi)
    company_lo = _make_company(local_db_session, name=name_lo)
    try:
        _make_award(
            local_db_session,
            external_id=ext_lo,
            winner_company=name_lo,
            award_value=5000.0,
            company_id=company_lo,
        )
        _make_award(
            local_db_session,
            external_id=ext_hi,
            winner_company=name_hi,
            award_value=9000.0,
            company_id=company_hi,
        )

        result = contract_awards_top_vendors(limit=100)
        ids_in_order = [
            d["company_id"]
            for d in result["data"]
            if d.get("company_id") in (company_hi, company_lo)
        ]
        assert ids_in_order == [company_hi, company_lo]

        # Calling again must reproduce the exact same order -- deterministic,
        # not incidental to a single run.
        result_again = contract_awards_top_vendors(limit=100)
        ids_again = [
            d["company_id"]
            for d in result_again["data"]
            if d.get("company_id") in (company_hi, company_lo)
        ]
        assert ids_again == ids_in_order
    finally:
        _cleanup(
            local_db_session,
            company_names=[name_hi, name_lo],
            external_ids=[ext_hi, ext_lo],
        )
