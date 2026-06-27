"""Unit tests for Vancouver building permit scraper formatting."""

from __future__ import annotations

from scraper.building_permits import _format_vancouver_record, _parse_date


def test_parse_date_iso():
    assert _parse_date("2026-06-12") == "2026-06-12"


def test_format_vancouver_record_maps_early_signal_fields():
    row = _format_vancouver_record(
        {
            "permitnumber": "BP-2026-01953",
            "permitnumbercreateddate": "2026-06-12",
            "issuedate": "2026-06-22",
            "projectvalue": 1000000,
            "typeofwork": "Addition / Alteration",
            "address": "595 BURRARD STREET, Vancouver, BC",
            "projectdescription": "Interior alterations",
            "applicant": "Example Applicant",
            "buildingcontractor": "PB Management Group Inc",
            "geolocalarea": "Downtown",
        }
    )
    assert row["external_id"] == "BP-2026-01953"
    assert row["application_date"] == "2026-06-12"
    assert row["issue_date"] == "2026-06-22"
    assert row["contractor"] == "PB Management Group Inc"
    assert row["local_area"] == "Downtown"
    assert row["source"] == "vancouver"
    assert row["city"] == "Vancouver"
