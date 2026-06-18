"""Unit tests for Burnaby building permits scraper."""

from __future__ import annotations

from datetime import date, timedelta

from scraper.burnaby_permits import (
    _extract_address,
    _filter_pdf_sources,
    _parse_issue_date_header,
    _parse_pdf_label_date,
    _parse_permit_block,
)


def test_parse_issue_date_header():
    text = "Permits Issued On: March 26, 2024\nValue of Work"
    assert _parse_issue_date_header(text) == "2024-03-26"


def test_parse_pdf_label_date():
    assert _parse_pdf_label_date("June-18-2026.pdf") == date(2026, 6, 18)
    assert _parse_pdf_label_date("Aprl-22-2026.pdf") == date(2026, 4, 22)


def test_parse_permit_block_2026_layout():
    block = """
BLD26-00383
Building Permit
(Residential)
New
$1,800,000.00
0
Applicant Name
MANNY SANDHU
Contractor Name
Contractor Address
1149692 BC LTD
13896 90 AVE
SURREY, BC V3V 6L1
Description
E-File -(MASTER BUILDING PERMIT) 2 Principal Buildings
Site Address
Legal Description
Current / Underlying Zone
5454 PARKER ST
LOT: 249 DISTRICT LOT: 126/127
"""
    row = _parse_permit_block(block, "BLD26-00383", "2026-06-12")
    assert row["external_id"] == "BLD26-00383"
    assert row["address"] == "5454 PARKER ST"
    assert row["project_value"] == "1800000.00"
    assert row["applicant"] == "MANNY SANDHU"
    assert "MASTER BUILDING PERMIT" in row["description"]
    assert row["source"] == "burnaby"
    assert row["city"] == "Burnaby"


def test_parse_permit_block_2024_layout():
    block = """
BLD24-00382LOT: 3 DISTRICT LOT: 153
6551 SUSSEX AVE
Legal Description
Current / Underlying Zone
Site Address
RAINBOW INTERNATIONAL OF DELTA
Applicant Name
11612 64A AVE DELTA, BC V4E 2C6
Contractor AddressContractor Name
BYL24-00015 Provincial Rental Housing repair
Description
Value of Work
$250,000.00
Building Permit
(Commercial)
Alteration
"""
    row = _parse_permit_block(block, "BLD24-00382", "2024-03-26")
    assert row["external_id"] == "BLD24-00382"
    assert row["address"] == "6551 SUSSEX AVE"
    assert row["project_value"] == "250000.00"


def test_extract_address_prefers_site_line_before_lot():
    block = "Description\nWork\nSite Address\n5454 PARKER ST\nLOT: 249"
    assert _extract_address(block) == "5454 PARKER ST"


def test_filter_pdf_sources_incremental():
    today = date.today()
    sources = [
        ("recent.pdf", "/a.pdf", today),
        ("old.pdf", "/b.pdf", today - timedelta(days=8)),
        ("future.pdf", "/c.pdf", today + timedelta(days=30)),
    ]
    filtered = _filter_pdf_sources(sources, days=7)
    assert len(filtered) == 1
    assert filtered[0][0] == "recent.pdf"


def test_filter_pdf_sources_full_history():
    sources = [
        ("June-18-2026.pdf", "/a.pdf", date(2026, 6, 18)),
        ("June-1-2026.pdf", "/b.pdf", date(2026, 6, 1)),
    ]
    assert len(_filter_pdf_sources(sources, days=None)) == 2
