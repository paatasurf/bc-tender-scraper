"""Unit tests for competitive overlap primitives."""

from __future__ import annotations

import re

from tests.unit.competitive_fixtures import make_cip, make_company

from pipeline.cip_schema import ValueRange
from pipeline.competitive_intel.overlap import (
    category_overlap_raw,
    geographic_overlap_raw,
    similarity_pre_score,
    value_overlap_raw,
)


def test_geographic_overlap_shared_cities():
    subject = make_company(id=1)
    peer = make_company(id=2, name="Rival GC")
    s_cip = make_cip(company_id=1, service_cities=["Vancouver", "Burnaby"])
    p_cip = make_cip(company_id=2, service_cities=["Vancouver"])
    raw, detail = geographic_overlap_raw(s_cip, p_cip, subject, peer)
    assert raw > 50
    assert "Vancouver" in detail
    assert not re.search(r"\b(street|ave|road)\b", detail, re.I)


def test_category_overlap_bhattacharyya():
    s_cip = make_cip(sector_focus={"institutional": 0.7, "commercial": 0.3})
    p_cip = make_cip(company_id=2, sector_focus={"institutional": 0.6, "commercial": 0.4})
    raw, _ = category_overlap_raw(s_cip, p_cip, make_company(), make_company(id=2))
    assert raw > 90


def test_value_overlap_close_medians():
    s_cip = make_cip(value_range=ValueRange(p25=400_000, median=500_000, p75=600_000, max=1_000_000))
    p_cip = make_cip(
        company_id=2,
        value_range=ValueRange(p25=450_000, median=520_000, p75=650_000, max=1_200_000),
    )
    raw, detail = value_overlap_raw(s_cip, p_cip, make_company(), make_company(id=2))
    assert raw > 70
    assert "$" in detail


def test_similarity_weights():
    score = similarity_pre_score(80, 60, 40)
    assert score == round(0.35 * 60 + 0.35 * 80 + 0.30 * 40, 2)


def test_geographic_overlap_same_primary_city_floor():
    subject = make_company(id=1, primary_city="Vancouver")
    peer = make_company(id=2, primary_city="Vancouver")
    s_cip = make_cip(company_id=1, service_cities=[], concentration_map=[])
    s_cip.neighborhoods = ["W 41st Ave", "W Georgia St"]
    p_cip = make_cip(company_id=2, service_cities=[], concentration_map=[])
    p_cip.neighborhoods = ["Main St", "Cambie St"]
    raw, detail = geographic_overlap_raw(s_cip, p_cip, subject, peer)
    assert raw >= 40.0
    assert "Vancouver" in detail
