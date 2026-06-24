"""Unit tests for Vancouver building permits scraper."""

from __future__ import annotations

from scraper.building_permits import (
    _build_incremental_where,
    _format_record,
    _parse_date,
)


def test_parse_date_iso():
    assert _parse_date("2026-06-15") == "2026-06-15"


def test_format_record_maps_opendata_fields():
    row = _format_record(
        {
            "permitnumber": "BP-2026-01982",
            "permitnumbercreateddate": "2026-06-15",
            "issuedate": "2026-06-19",
            "projectvalue": 1000000.0,
            "typeofwork": "Addition / Alteration",
            "address": "595 BURRARD STREET, Vancouver, BC",
            "projectdescription": "Interior alterations",
            "applicant": "Example Applicant",
            "buildingcontractor": "Halse Martin Construction Co Ltd",
            "geolocalarea": "Downtown",
        }
    )
    assert row["external_id"] == "BP-2026-01982"
    assert row["application_date"] == "2026-06-15"
    assert row["issue_date"] == "2026-06-19"
    assert row["project_value"] == "1000000.0"
    assert row["contractor"] == "Halse Martin Construction Co Ltd"
    assert row["local_area"] == "Downtown"
    assert row["source"] == "vancouver"
    assert row["city"] == "Vancouver"


def test_format_record_uses_permit_number_when_address_missing():
    row = _format_record(
        {
            "permitnumber": "BP-2026-01179",
            "permitnumbercreateddate": "2026-04-13",
            "issuedate": "2026-05-13",
            "typeofwork": "Temporary Building / Structure",
            "address": None,
            "buildingcontractor": None,
            "geolocalarea": None,
        }
    )
    assert row["external_id"] == "BP-2026-01179"
    assert row["address"] == "BP-2026-01179"
    assert row["contractor"] == ""
    assert row["local_area"] == ""


def test_build_incremental_where_includes_both_dates():
    clause = _build_incremental_where(14)
    assert "issuedate >=" in clause
    assert "permitnumbercreateddate >=" in clause
