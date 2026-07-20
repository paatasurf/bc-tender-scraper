"""Regression tests for fail-closed Surrey permit resolution."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from db.permit_import import _attach_company_ids
from pipeline.kg.adapters.permit import build_permit_payload


def _unresolved():
    return type(
        "Resolution", (), {"company_id": None, "confidence": None, "method": ""}
    )()


def _resolved(
    company_id: int = 8638, confidence: float = 0.9, method: str = "contractor"
):
    return type(
        "Resolution",
        (),
        {"company_id": company_id, "confidence": confidence, "method": method},
    )()


@patch("db.permit_import.resolve_permit_company_from_row")
def test_surrey_never_creates_company_from_applicant_during_import(
    mock_resolve,
) -> None:
    mock_resolve.return_value = _unresolved()
    row = {"applicant": "Example Builder Ltd", "city": "Surrey"}

    _attach_company_ids(MagicMock(), [row], source="surrey")

    mock_resolve.assert_called_once()
    assert mock_resolve.call_args.kwargs["create_if_missing"] is False
    assert "company_id" not in row


@patch("db.permit_import.resolve_permit_company_from_row")
def test_existing_vancouver_creation_policy_is_unchanged(mock_resolve) -> None:
    mock_resolve.return_value = _unresolved()

    _attach_company_ids(
        MagicMock(), [{"applicant": "Example Builder Ltd"}], source="vancouver"
    )

    assert mock_resolve.call_args.kwargs["create_if_missing"] is True


@patch("db.permit_import.resolve_permit_company_from_row")
def test_existing_burnaby_creation_policy_is_unchanged(mock_resolve) -> None:
    mock_resolve.return_value = _unresolved()

    _attach_company_ids(
        MagicMock(), [{"applicant": "Example Builder Ltd"}], source="burnaby"
    )

    assert mock_resolve.call_args.kwargs["create_if_missing"] is True


@patch("db.permit_import.resolve_permit_company_from_row")
def test_surrey_resolver_receives_normalized_applicant_not_raw(mock_resolve) -> None:
    """Company Discovery must never see the raw ApplicantOrganization string
    (it commonly has a mailing address appended) -- only the safely
    normalized organization name, or an empty string when unresolved."""
    mock_resolve.return_value = _unresolved()
    row = {
        "applicant": "Tyrrell Projects Inc 19949 56 Ave Surrey, BC",
        "normalized_applicant": "Tyrrell Projects Inc",
        "applicant_normalization_status": "normalized_legal_suffix",
        "city": "Surrey",
    }

    _attach_company_ids(MagicMock(), [row], source="surrey")

    resolution_row = mock_resolve.call_args.args[1]
    assert resolution_row["applicant"] == "Tyrrell Projects Inc"
    assert resolution_row is not row


@patch("db.permit_import.resolve_permit_company_from_row")
def test_surrey_resolution_never_mutates_original_row_applicant(mock_resolve) -> None:
    """The original row (which flows on to the Permit DB write and KG
    dual-write) must keep the raw applicant untouched, whether resolution
    succeeds or fails."""
    mock_resolve.return_value = _resolved()
    raw = "Tyrrell Projects Inc 19949 56 Ave Surrey, BC"
    row = {
        "applicant": raw,
        "normalized_applicant": "Tyrrell Projects Inc",
        "applicant_normalization_status": "normalized_legal_suffix",
        "city": "Surrey",
    }

    _attach_company_ids(MagicMock(), [row], source="surrey")

    assert row["applicant"] == raw
    assert row["company_id"] == 8638


@patch("db.permit_import.resolve_permit_company_from_row")
def test_unresolved_surrey_row_gets_no_company_id(mock_resolve) -> None:
    mock_resolve.return_value = _unresolved()
    row = {
        "applicant": "Jane Smith",
        "normalized_applicant": "",
        "applicant_normalization_status": "unresolved",
        "city": "Surrey",
    }

    _attach_company_ids(MagicMock(), [row], source="surrey")

    assert "company_id" not in row


def test_kg_permit_payload_carries_raw_normalized_and_status() -> None:
    row = {
        "external_id": "22-020638-000-00",
        "address": "17065 84 Ave",
        "project_value": "995000",
        "applicant": "Tyrrell Projects Inc 19949 56 Ave Surrey, BC",
        "normalized_applicant": "Tyrrell Projects Inc",
        "applicant_normalization_status": "normalized_legal_suffix",
    }

    payload = build_permit_payload(row, source="surrey")

    assert payload["applicant"] == "Tyrrell Projects Inc 19949 56 Ave Surrey, BC"
    assert payload["normalized_applicant"] == "Tyrrell Projects Inc"
    assert payload["applicant_normalization_status"] == "normalized_legal_suffix"
    assert "source_applicant_raw" not in payload
