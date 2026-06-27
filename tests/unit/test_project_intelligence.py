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


def test_get_company_project_contacts_excludes_self(monkeypatch):
    from pipeline import project_intelligence as pi

    company = type(
        "Company",
        (),
        {"id": 1, "name": "Mountain Tai Construction & Management Ltd", "canonical_vendor_name": ""},
    )()
    permit = type(
        "Permit",
        (),
        {
            "id": 10,
            "address": "123 Main St",
            "permit_type": "New",
            "issue_date": "2024-01-01",
            "project_value": "1000000",
            "city": "Vancouver",
        },
    )()
    architect_contact = type(
        "Contact",
        (),
        {
            "id": 1,
            "project_id": 10,
            "project_type": "permit",
            "role": "architect",
            "company_name": "Vincent Wan Design",
            "contact_name": "Vincent Wan",
            "phone": "",
            "email": "",
            "source": "vancouver",
        },
    )()
    gc_contact = type(
        "Contact",
        (),
        {
            "id": 2,
            "project_id": 10,
            "project_type": "permit",
            "role": "gc",
            "company_name": "Mountain Tai Construction & Management Ltd",
            "contact_name": "",
            "phone": "",
            "email": "",
            "source": "vancouver",
        },
    )()

    class _Session:
        def get(self, _model, company_id):
            return company if company_id == 1 else None

        def scalars(self, _query):
            class _Result:
                def __init__(self, rows):
                    self._rows = rows

                def all(self):
                    return self._rows

            if not hasattr(self, "_calls"):
                self._calls = 0
            self._calls += 1
            return _Result([architect_contact, gc_contact] if self._calls == 1 else [permit])

    monkeypatch.setattr(
        pi,
        "_company_permit_ids",
        lambda session, company, **kwargs: {10},
    )

    result = pi.get_company_project_contacts(_Session(), 1)
    assert result["total"] == 1
    assert result["data"][0]["role"] == "architect"
    assert result["data"][0]["company_name"] == "Vincent Wan Design"
