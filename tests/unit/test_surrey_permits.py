"""Unit tests for Surrey building permits scraper."""

from __future__ import annotations

from scraper.surrey_permits import (
    _build_where_clause,
    _format_record,
    _parse_issue_date,
    _should_fetch_next_page,
)


def test_parse_issue_date_yyyymmdd():
    assert _parse_issue_date("20230102") == "2023-01-02"


def test_format_record_maps_current_arcgis_fields():
    row = _format_record(
        {
            "PermitNumber": "22-020638-000-00",
            "ProjectAddress": "17065 84 Ave",
            "WorkDescription": "New Single Family with Secondary Suite",
            "SubDescription": "Primary",
            "IssuedDate": "20230102",
            "ValueOfConstruction": 995000,
            "ApplicantOrganization": "Example Builder Ltd",
        }
    )
    assert row["external_id"] == "22-020638-000-00"
    assert row["address"] == "17065 84 Ave"
    assert row["permit_type"] == "New Single Family with Secondary Suite"
    assert row["project_value"] == "995000"
    assert row["applicant"] == "Example Builder Ltd"
    assert row["issue_date"] == "2023-01-02"
    assert row["description"] == "New Single Family with Secondary Suite / Primary"
    assert "source_status_raw" not in row
    assert row["source"] == "surrey"
    assert row["city"] == "Surrey"


def test_format_record_ignores_permit_status_when_present():
    row = _format_record(
        {
            "PermitNumber": "22-020638-000-00",
            "ProjectAddress": "17065 84 Ave",
            "WorkDescription": "Renovation",
            "SubDescription": "Single Family",
            "IssuedDate": "20230102",
            "ValueOfConstruction": 995000,
            "PermitStatus": "Issued",
        }
    )
    assert "source_status_raw" not in row


def test_format_record_falls_back_to_legacy_arcgis_fields():
    row = _format_record(
        {
            "PermitNumber": "22-020638-000-00",
            "Address": "17065 84 Ave",
            "PermitType": "New Single Family with Secondary Suite",
            "WorkType": "New",
            "SubType": "Primary",
            "IssuedDate": "20230102",
            "ValueOfConstruction": 995000,
        }
    )
    assert row["address"] == "17065 84 Ave"
    assert row["permit_type"] == "New Single Family with Secondary Suite"
    assert row["description"] == "New / Primary"


def test_build_where_clause_incremental():
    clause = _build_where_clause(days=7)
    assert clause.startswith("IssuedDate >= '")


def test_build_where_clause_full_history():
    assert _build_where_clause(days=None) == "1=1"


def test_should_fetch_next_page_when_transfer_limit_exceeded():
    assert _should_fetch_next_page(raw_count=500, page_size=500, exceeded=True) is True


def test_should_fetch_next_page_when_full_page_without_flag():
    assert _should_fetch_next_page(raw_count=500, page_size=500, exceeded=False) is True


def test_should_stop_on_short_final_page():
    assert _should_fetch_next_page(raw_count=330, page_size=500, exceeded=False) is False

