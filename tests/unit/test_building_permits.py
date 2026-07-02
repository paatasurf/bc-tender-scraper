"""Unit tests for Vancouver building permit scraper formatting."""

from __future__ import annotations

import csv
from pathlib import Path

from scraper.building_permits import FIELDNAMES, _format_vancouver_record, _parse_date, _write_csv


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
            "permitcategory": "Renovation - Commercial/ Mixed Use - Lower Complexity",
        }
    )
    assert row["external_id"] == "BP-2026-01953"
    assert row["application_date"] == "2026-06-12"
    assert row["issue_date"] == "2026-06-22"
    assert row["contractor"] == "PB Management Group Inc"
    assert row["local_area"] == "Downtown"
    assert "source_status_raw" not in row
    assert row["source"] == "vancouver"
    assert row["city"] == "Vancouver"


def test_write_csv_rewrites_with_canonical_header(tmp_path, monkeypatch):
    csv_path = tmp_path / "building_permits.csv"
    monkeypatch.setattr("scraper.building_permits.BUILDING_PERMITS_CSV", str(csv_path))

    mismatched_header = [
        "external_id",
        "address",
        "permit_type",
        "project_value",
        "applicant",
        "application_date",
        "issue_date",
        "contractor",
        "local_area",
        "description",
        "source",
        "city",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=mismatched_header)
        writer.writeheader()
        writer.writerow(
            {
                "external_id": "BP-OLD-1",
                "address": "1 MAIN ST",
                "permit_type": "New Building",
                "project_value": "1000",
                "applicant": "Old Applicant",
                "application_date": "2026-01-01",
                "issue_date": "2026-01-02",
                "contractor": "Downtown",
                "local_area": "",
                "description": "Long project narrative that belonged in description",
                "source": "vancouver",
                "city": "Vancouver",
            }
        )

    fresh = _format_vancouver_record(
        {
            "permitnumber": "BP-NEW-1",
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
    _write_csv([fresh], append=True)

    with csv_path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert list(rows[0].keys()) == FIELDNAMES
    assert len(rows) == 1
    assert rows[0]["external_id"] == "BP-NEW-1"
    assert rows[0]["contractor"] == "PB Management Group Inc"
    assert rows[0]["description"] == "Interior alterations"
