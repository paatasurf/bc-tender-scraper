"""Integration-style tests for competitive intelligence service."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from db.models import Company
from pipeline.competitive_intel.service import get_competitive_intelligence
from tests.unit.competitive_fixtures import make_cip, make_company


@pytest.fixture
def mock_session():
    session = MagicMock()
    company = make_company(id=42)
    peer1 = make_company(id=2, name="Peer One", total_value=10_000_000)
    peer2 = make_company(id=3, name="Peer Two", total_value=8_000_000)
    peer3 = make_company(id=4, name="Peer Three", total_value=6_000_000)

    def get_side_effect(model, pk):
        if pk == 42:
            return company
        if pk == 2:
            return peer1
        if pk == 3:
            return peer2
        if pk == 4:
            return peer4 if pk == 4 else None
        return None

    session.get.side_effect = lambda model, pk: {
        42: company,
        2: peer1,
        3: peer2,
        4: make_company(id=4, name="Peer Four"),
    }.get(pk)

    cohort_members = [peer1, peer2, peer3, make_company(id=5), make_company(id=6), make_company(id=7), make_company(id=8), make_company(id=9)]
    session.scalars.return_value.all.return_value = cohort_members
    session.scalar.return_value = 0
    return session


def test_service_response_shape():
    session = MagicMock()
    company = make_company(id=100)
    session.get.return_value = company

    subject_cip = make_cip(company_id=100)
    peer_cip = make_cip(company_id=101, name="Rival")

    with (
        patch("pipeline.competitive_intel.service.get_cip", return_value=subject_cip),
        patch(
            "pipeline.competitive_intel.service.build_market_cohort",
        ) as mock_cohort,
        patch(
            "pipeline.competitive_intel.service.select_top_competitors",
            return_value=[],
        ),
        patch(
            "pipeline.competitive_intel.service.compute_benchmark_strip",
            return_value={"metrics": []},
        ),
    ):
        from pipeline.competitive_intel.types import MarketCohort

        mock_cohort.return_value = MarketCohort(
            members=[],
            definition="test",
            definition_key="sector_and_city",
            cohort_size=0,
        )
        result = get_competitive_intelligence(session, company_id=100, kind="construction")

    assert result["engine_version"] == "competitive_intel_v1.3"
    assert result["company_id"] == 100
    assert "benchmark" in result
    assert "top_competitors" in result
    assert "insufficient_market_data" in result["warnings"]
