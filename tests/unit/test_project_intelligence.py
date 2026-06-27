"""Unit tests for Project Intelligence contact extraction."""

from __future__ import annotations

from pipeline.project_intelligence import contact_from_party_name, contacts_from_permit


class _PermitStub:
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


def test_contact_from_dba_name_splits_contact_and_company():
    row = contact_from_party_name(
        "Vincent Wan DBA: Vincent Wan Design",
        project_id=1,
        project_type="permit",
        role="architect",
        source="vancouver",
    )
    assert row is not None
    assert row["contact_name"] == "Vincent Wan"
    assert row["company_name"] == "Vincent Wan Design"
    assert row["role"] == "architect"


def test_contacts_from_permit_builds_architect_and_gc():
    permit = _PermitStub(
        id=42,
        applicant="Philip Ng DBA: Construction General Contractor",
        contractor="Mountain Tai Construction & Management Ltd",
        source="vancouver",
    )
    rows = contacts_from_permit(permit)
    roles = {row["role"]: row for row in rows}
    assert set(roles) == {"architect", "gc"}
    assert roles["architect"]["company_name"] == "Construction General Contractor"
    assert roles["gc"]["company_name"] == "Mountain Tai Construction & Management Ltd"
