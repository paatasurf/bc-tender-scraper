"""Unit tests for company analytics filters."""

from __future__ import annotations

from db.company_analytics import company_analytics_entity_filter
from db.company_canonical_constants import (
    ENTITY_ROLE_APPLICANT_ALIAS,
    ENTITY_ROLE_CANONICAL,
    ENTITY_ROLE_PROBABLE_PERSON,
)


def test_analytics_filter_excludes_alias_and_probable_person():
    clause = str(company_analytics_entity_filter().compile(compile_kwargs={"literal_binds": True}))
    assert ENTITY_ROLE_APPLICANT_ALIAS in clause
    assert ENTITY_ROLE_PROBABLE_PERSON in clause
    assert ENTITY_ROLE_CANONICAL not in clause or "NOT IN" in clause.upper()
