"""Unit tests for Competitive Threat Score."""

from __future__ import annotations

import re

import pytest

from pipeline.cip_schema import ValueRange
from pipeline.competitive_intel.threat_score import compute_threat_score
from pipeline.competitive_intel.types import ActivityStats
from tests.unit.competitive_fixtures import make_arch_company, make_cip, make_company


def _stats() -> ActivityStats:
    return ActivityStats(
        award_90d_p90=3.0,
        permit_90d_p90=5.0,
        award_90d_by_company={},
        permit_90d_by_company={},
    )


def _pair_variant(seed: int):
    """Generate subject/peer pairs with varied overlap."""
    sector_a = {"institutional": 0.5 + (seed % 5) * 0.05, "commercial": 0.5 - (seed % 5) * 0.05}
    sector_b = {"institutional": 0.4 + (seed % 4) * 0.06, "commercial": 0.6 - (seed % 4) * 0.06}
    med_s = 300_000 + seed * 50_000
    med_p = 350_000 + (seed % 7) * 40_000
    cities_s = ["Vancouver"] if seed % 2 == 0 else ["Vancouver", "Burnaby"]
    cities_p = ["Vancouver"] if seed % 3 != 0 else ["Richmond"]
    subject = make_company(id=1, primary_city=cities_s[0])
    peer = make_company(id=2 + seed, name=f"Peer {seed}", primary_city=cities_p[0])
    s_cip = make_cip(
        company_id=1,
        sector_focus=sector_a,
        service_cities=cities_s,
        value_range=ValueRange(p25=med_s * 0.5, median=med_s, p75=med_s * 2, max=med_s * 4),
    )
    p_cip = make_cip(
        company_id=2 + seed,
        sector_focus=sector_b,
        service_cities=cities_p,
        value_range=ValueRange(p25=med_p * 0.5, median=med_p, p75=med_p * 2, max=med_p * 4),
    )
    return subject, peer, s_cip, p_cip


@pytest.mark.parametrize("seed", range(20))
def test_threat_score_sum_invariant(seed: int):
    subject, peer, s_cip, p_cip = _pair_variant(seed)
    result = compute_threat_score(
        subject=subject,
        peer=peer,
        subject_cip=s_cip,
        peer_cip=p_cip,
        kind="construction",
        stats=_stats(),
    )
    component_sum = sum(b.points for b in result.breakdown)
    assert result.score == component_sum
    assert 0 <= result.score <= 100


def test_threat_score_deterministic():
    subject, peer, s_cip, p_cip = _pair_variant(3)
    r1 = compute_threat_score(
        subject=subject, peer=peer, subject_cip=s_cip, peer_cip=p_cip,
        kind="construction", stats=_stats(),
    )
    r2 = compute_threat_score(
        subject=subject, peer=peer, subject_cip=s_cip, peer_cip=p_cip,
        kind="construction", stats=_stats(),
    )
    assert r1.score == r2.score
    assert [b.points for b in r1.breakdown] == [b.points for b in r2.breakdown]


def test_geo_detail_no_street_tokens():
    subject, peer, s_cip, p_cip = _pair_variant(1)
    result = compute_threat_score(
        subject=subject, peer=peer, subject_cip=s_cip, peer_cip=p_cip,
        kind="construction", stats=_stats(),
    )
    geo = next(b for b in result.breakdown if b.factor == "geographic_overlap")
    assert not re.search(r"\b\d+\s+\w+\s+(st|street|ave|avenue|rd|road)\b", geo.detail, re.I)


def test_architecture_award_na():
    subject = make_arch_company(id=10)
    peer = make_arch_company(id=11, name="Rival Design")
    s_cip = make_cip(company_id=10, kind="architecture", name=subject.name)
    p_cip = make_cip(company_id=11, kind="architecture", name=peer.name)
    result = compute_threat_score(
        subject=subject, peer=peer, subject_cip=s_cip, peer_cip=p_cip,
        kind="architecture", stats=_stats(),
    )
    award = next(b for b in result.breakdown if b.factor == "award_activity")
    assert award.points == 0
    assert "N/A" in award.detail
