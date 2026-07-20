"""Tests for conservative Surrey applicant normalization."""

from __future__ import annotations

from unittest.mock import patch

from pipeline.surrey_applicant import (
    STATUS_MISSING,
    STATUS_NORMALIZED_BUSINESS_ADDRESS,
    STATUS_NORMALIZED_LEGAL_SUFFIX,
    STATUS_UNRESOLVED,
    normalize_surrey_applicant,
)


def test_normalizes_legal_company_before_mailing_address() -> None:
    result = normalize_surrey_applicant(
        "Tyrrell Projects Inc 19949 56 Ave Surrey, British Columbia, Canada, V3A3Y2"
    )
    assert result.organization == "Tyrrell Projects Inc"
    assert result.status == STATUS_NORMALIZED_LEGAL_SUFFIX


def test_normalizes_business_keyword_before_unit_address() -> None:
    result = normalize_surrey_applicant(
        "Builden Construction Unit 508 13761 96 Ave Surrey, British Columbia, Canada"
    )
    assert result.organization == "Builden Construction"
    assert result.status == STATUS_NORMALIZED_BUSINESS_ADDRESS


def test_missing_value_is_not_resolvable() -> None:
    result = normalize_surrey_applicant("")
    assert result.organization == ""
    assert result.status == STATUS_MISSING
    assert result.is_resolvable is False


def test_ambiguous_location_suffix_is_fail_closed() -> None:
    result = normalize_surrey_applicant(
        "Oberizon Homes White Rock, British Columbia, Canada"
    )
    assert result.raw.startswith("Oberizon Homes")
    assert result.organization == ""
    assert result.status == STATUS_UNRESOLVED


def test_person_or_arbitrary_text_is_fail_closed() -> None:
    assert normalize_surrey_applicant("Jane Smith").organization == ""
    assert normalize_surrey_applicant("Unknown applicant record").organization == ""


def test_person_and_company_composite_is_fail_closed() -> None:
    result = normalize_surrey_applicant(
        "Rashpal Singh Padda and RRA New Homes Ltd 123 Main St Surrey"
    )
    assert result.organization == ""
    assert result.status == STATUS_UNRESOLVED


def test_normalizer_makes_no_db_session_or_network_calls() -> None:
    """The normalizer is a pure string function -- it must never touch
    requests or a DB session, regardless of input."""
    with patch("requests.get", side_effect=AssertionError("network call")), patch(
        "requests.post", side_effect=AssertionError("network call")
    ), patch("requests.Session", side_effect=AssertionError("network call")):
        normalize_surrey_applicant("Tyrrell Projects Inc 19949 56 Ave Surrey, BC")
        normalize_surrey_applicant("Rashpal Singh Padda and RRA New Homes Ltd")
        normalize_surrey_applicant("")
        normalize_surrey_applicant(None)
