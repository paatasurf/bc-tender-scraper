"""Unit tests for pipeline.company_discovery."""

from __future__ import annotations

from pipeline.company_discovery import PRIORITY_CONTRACTOR, PRIORITY_DESCRIPTION, discover_companies


def test_akash_sidhu_finds_contractor_not_applicant():
    record = {
        "applicant": "Akash Sidhu",
        "contractor": "J B Siteworks Inc.",
        "description": "Demolition Contractor: JB Siteworks Inc. (Jas Brar)",
    }
    result = discover_companies(record)
    ordered = result.ordered_candidates()
    assert result.applicant_person == "Akash Sidhu"
    assert ordered[0].source == "contractor"
    assert ordered[0].priority == PRIORITY_CONTRACTOR
    assert ordered[0].resolution_name == "J B Siteworks Inc."


def test_person_applicant_with_description_contractor():
    record = {
        "applicant": "Akash Sidhu",
        "contractor": "",
        "description": "Demolition Contractor: JB Siteworks Inc.",
    }
    result = discover_companies(record)
    sources = [c.source for c in result.ordered_candidates()]
    assert "description:demo_contractor" in sources
    assert result.ordered_candidates()[0].priority == PRIORITY_DESCRIPTION


def test_dba_applicant_yields_business_at_lower_priority():
    record = {
        "applicant": "David Wong DBA: WHG Design",
        "contractor": "",
        "description": "",
    }
    result = discover_companies(record)
    assert len(result.ordered_candidates()) == 1
    assert result.ordered_candidates()[0].resolution_name == "WHG Design"


def test_contractor_beats_dba_applicant():
    record = {
        "applicant": "David Wong DBA: WHG Design",
        "contractor": "Other Builder Ltd.",
        "description": "",
    }
    result = discover_companies(record)
    assert result.ordered_candidates()[0].source == "contractor"


def test_plain_person_no_candidates():
    record = {"applicant": "Naki Ocran", "contractor": "", "description": ""}
    result = discover_companies(record)
    assert result.ordered_candidates() == []
    assert result.applicant_person == "Naki Ocran"
