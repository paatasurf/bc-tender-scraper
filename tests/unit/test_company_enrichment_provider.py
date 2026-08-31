"""Mock-based unit tests for pipeline/company_enrichment/provider.py and
orgbook_adapter.py (RFC Phase 0 acceptance criterion): OrgBookAdapter.lookup()
must return the same match hub.match_company(..., source="orgbook") would,
reshaped into ProviderResult -- no new matching logic, no invented facts.

No DB migration involved -- pipeline.registry_verification.hub is mocked
entirely, matching this phase's "unit tests only (mock-based), no DB
migration yet" acceptance criterion.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from pipeline.company_enrichment.orgbook_adapter import OrgBookAdapter
from pipeline.company_enrichment.provider import EnrichmentRequest, ProviderResult


def _request(company_id: int = 42) -> EnrichmentRequest:
    return EnrichmentRequest(company_id=company_id, company_name="Acme Construction Ltd")


def test_lookup_returns_unmatched_with_no_facts_when_hub_finds_no_match():
    session = MagicMock()
    with patch(
        "pipeline.company_enrichment.orgbook_adapter.hub.match_company",
        return_value=None,
    ) as match_mock:
        result = OrgBookAdapter().lookup(session, _request())

    match_mock.assert_called_once_with(session, 42, source="orgbook")
    assert result == ProviderResult(provider="orgbook", matched=False)


def test_lookup_reshapes_hub_match_into_provider_result_with_facts():
    session = MagicMock()
    with patch(
        "pipeline.company_enrichment.orgbook_adapter.hub.match_company",
        return_value={"source": "orgbook", "confidence": 0.91, "match_tier": "exact"},
    ):
        with patch("pipeline.company_enrichment.orgbook_adapter.hub.get_provider") as get_provider_mock:
            get_provider_mock.return_value.build_profile.return_value = {
                "legal_name": "Acme Construction Ltd.",
                "business_number": "BC1234567",
                "city": "Vancouver",
                "province": "BC",
            }
            result = OrgBookAdapter().lookup(session, _request())

    assert result.provider == "orgbook"
    assert result.matched is True
    assert result.error is None
    facts_by_field = {f.field_name: f.value for f in result.facts}
    assert facts_by_field == {
        "legal_name": "Acme Construction Ltd.",
        "business_number": "BC1234567",
        "city": "Vancouver",
        "province": "BC",
    }
    # confidence from the match is threaded through to every fact
    assert all(f.confidence == 0.91 for f in result.facts)


def test_lookup_never_invents_a_fact_for_a_field_the_profile_does_not_have():
    """Only present profile fields become facts -- a missing field is
    simply absent, never a guessed/default value."""
    session = MagicMock()
    with patch(
        "pipeline.company_enrichment.orgbook_adapter.hub.match_company",
        return_value={"confidence": 0.8},
    ):
        with patch("pipeline.company_enrichment.orgbook_adapter.hub.get_provider") as get_provider_mock:
            get_provider_mock.return_value.build_profile.return_value = {
                "legal_name": "Acme Construction Ltd.",
                "city": "",
            }
            result = OrgBookAdapter().lookup(session, _request())

    field_names = {f.field_name for f in result.facts}
    assert field_names == {"legal_name"}  # city="" (falsy) and business_number/province absent


def test_lookup_treats_a_matched_company_with_no_profile_as_matched_no_facts():
    session = MagicMock()
    with patch(
        "pipeline.company_enrichment.orgbook_adapter.hub.match_company",
        return_value={"confidence": 0.7},
    ):
        with patch("pipeline.company_enrichment.orgbook_adapter.hub.get_provider") as get_provider_mock:
            get_provider_mock.return_value.build_profile.return_value = None
            result = OrgBookAdapter().lookup(session, _request())

    assert result.matched is True
    assert result.facts == ()


def test_lookup_isolates_a_raising_hub_call_as_a_provider_error_never_raises():
    """A provider failure (e.g. a DB error inside hub.match_company) must
    become ProviderResult(error=...), never an exception the orchestrator's
    cascade has to catch itself -- golden case #6 (provider timeout/error
    isolation) starts here at the provider boundary."""
    session = MagicMock()
    with patch(
        "pipeline.company_enrichment.orgbook_adapter.hub.match_company",
        side_effect=RuntimeError("connection reset"),
    ):
        result = OrgBookAdapter().lookup(session, _request())

    assert result.matched is False
    assert result.error == "connection reset"


def test_adapter_metadata_marks_it_as_a_real_fact_source():
    assert OrgBookAdapter.name == "orgbook"
    assert OrgBookAdapter.is_fact_source is True
