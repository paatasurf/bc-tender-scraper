"""Unit tests for award count resolution and benchmark integration."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from pipeline.competitive_intel.awards import AwardCountResolver, vendor_keys_from_name
from pipeline.competitive_intel.benchmark import compute_benchmark_strip
from pipeline.competitive_intel.types import MarketCohort, TopCompetitor
from tests.unit.competitive_fixtures import make_company


def test_vendor_keys_from_dba_name():
    keys = vendor_keys_from_name("David Evans DBA: WSP Canada Inc")
    assert "wspscanada" in keys or any("wsp" in key for key in keys)


def test_compute_benchmark_strip_uses_award_counts_map():
    subject = make_company(id=1, award_count=0)
    peer = make_company(id=2, award_count=0)
    cohort = MarketCohort(
        members=[peer, make_company(id=3, award_count=0)],
        definition="test",
        definition_key="sector_and_city",
        cohort_size=2,
    )
    peers = [
        TopCompetitor(
            company_id=2,
            name="Peer",
            company_kind="construction",
            threat_score=70,
            threat_breakdown={},
            similarity=0.8,
            total_projects=10,
            total_value=1_000_000,
            award_count=0,
        )
    ]
    award_counts = {1: 48, 2: 12, 3: 6}

    result = compute_benchmark_strip(
        subject,
        cohort,
        peers,
        kind="construction",
        award_counts=award_counts,
        award_market_members=[peer, make_company(id=3)],
    )
    awards = next(metric for metric in result["metrics"] if metric["key"] == "award_count")
    assert awards["company"] == 48
    assert awards["market_median"] == 9.0
    assert awards["top_competitor_median"] == 12


@pytest.mark.parametrize(
    ("name_a", "name_b"),
    [
        ("David Evans DBA: WSP Canada Inc", "Alex Olaru DBA: WSP Canada Inc."),
        ("Sasco Contractors Ltd.", "David Crarer DBA: Sasco Contractors Ltd."),
    ],
)
def test_vendor_keys_overlap_for_dba_siblings(name_a, name_b):
    keys_a = vendor_keys_from_name(name_a)
    keys_b = vendor_keys_from_name(name_b)
    assert keys_a & keys_b
