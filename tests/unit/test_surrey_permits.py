"""Unit tests for Surrey building permits scraper."""

from __future__ import annotations

from scraper.surrey_permits import (
    _build_where_clause,
    _format_record,
    _parse_issue_date,
)


def test_parse_issue_date_yyyymmdd():
    assert _parse_issue_date("20230102") == "2023-01-02"


def test_format_record_maps_arcgis_fields():
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
    assert row["external_id"] == "22-020638-000-00"
    assert row["address"] == "17065 84 Ave"
    assert row["permit_type"] == "New Single Family with Secondary Suite"
    assert row["project_value"] == "995000"
    assert row["issue_date"] == "2023-01-02"
    assert row["description"] == "New / Primary"
    assert row["source"] == "surrey"
    assert row["city"] == "Surrey"


def test_build_where_clause_incremental():
    clause = _build_where_clause(days=7)
    assert clause.startswith("IssuedDate >= '")


def test_build_where_clause_full_history():
    assert _build_where_clause(days=None) == "1=1"
