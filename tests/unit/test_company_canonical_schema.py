"""Unit tests for company canonical merge schema."""

from __future__ import annotations

from db.company_canonical_constants import ENTITY_ROLES, ENTITY_ROLE_PROBABLE_PERSON
from db.models import Company, Permit


def test_company_model_includes_canonical_merge_columns():
    column_names = {column.name for column in Company.__table__.columns}
    expected = {
        "display_name",
        "entity_role",
        "canonical_company_id",
        "applicant_signatory",
        "canonical_merge_confidence",
        "canonical_merge_method",
    }
    assert expected.issubset(column_names)


def test_permit_model_includes_company_id():
    column_names = {column.name for column in Permit.__table__.columns}
    assert "company_id" in column_names
    assert "canonical_merge_confidence" in column_names


def test_entity_role_vocabulary():
    assert "canonical" in ENTITY_ROLES
    assert "applicant_alias" in ENTITY_ROLES
    assert "standalone" in ENTITY_ROLES
    assert ENTITY_ROLE_PROBABLE_PERSON in ENTITY_ROLES
