"""Unit tests for Google enrichment orchestrator."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from db.models import Company
from pipeline.google_enrichment.config import GoogleEnrichmentSettings
from pipeline.google_enrichment.models import PlaceCandidate
from pipeline.google_enrichment.orchestrator import GoogleEnrichmentOrchestrator
from pipeline.google_enrichment.provider import GoogleEnrichmentProvider


class _FixtureProvider(GoogleEnrichmentProvider):
    provider_name = "fixture"

    def __init__(self, candidates: list[PlaceCandidate]) -> None:
        self._candidates = candidates

    async def lookup(self, query: str, *, limit: int = 3) -> list[PlaceCandidate]:
        return self._candidates[:limit]

    async def healthcheck(self) -> bool:
        return True


def _settings() -> GoogleEnrichmentSettings:
    return GoogleEnrichmentSettings(
        provider="none",
        provider_fallback="none",
        apify_actor_id="",
        oss_scraper_url="",
        stale_days=30,
        batch_size=5,
        confidence_accept=0.70,
        confidence_review=0.55,
        no_match_retry_days=90,
        copy_website_to_website=False,
    )


def _company(**overrides) -> Company:
    defaults = {
        "id": 8638,
        "name": "Pontem Group Inc.",
        "primary_city": "Vancouver",
        "primary_province": "BC",
        "primary_address": "100 Main St, Vancouver, BC",
        "google_phone": "6045550100",
        "lifecycle_status": "active",
        "is_operating": True,
        "google_enrichment_status": "pending",
        "total_value": 1000000.0,
    }
    defaults.update(overrides)
    return Company(**defaults)


@pytest.fixture
def session() -> MagicMock:
    return MagicMock()


def test_orchestrator_dry_run_does_not_persist_company_writes(session: MagicMock):
    candidate = PlaceCandidate(
        place_id="ChIJtest",
        name="Pontem Group Ltd.",
        formatted_address="100 Main St, Vancouver, BC",
        phone="6045550100",
    )
    provider = _FixtureProvider([candidate])
    orchestrator = GoogleEnrichmentOrchestrator(
        settings=_settings(),
        provider=provider,
        fallback_provider=provider,
    )

    with patch(
        "pipeline.google_enrichment.orchestrator.fetch_eligible_companies",
        return_value=[_company()],
    ), patch(
        "pipeline.google_enrichment.orchestrator.fetch_reserved_place_ids",
        return_value=frozenset(),
    ), patch(
        "pipeline.google_enrichment.orchestrator.mark_stale_companies",
        return_value=0,
    ), patch(
        "pipeline.google_enrichment.orchestrator.CompanyGoogleWriter"
    ) as writer_cls:
        result = orchestrator.run(session, run_id="run-dry", dry_run=True)

    writer_cls.assert_not_called()
    session.commit.assert_not_called()
    assert result.dry_run is True
    assert result.attempted == 1
    assert result.companies[0].log_status == "success"


def test_orchestrator_writes_on_success(session: MagicMock):
    candidate = PlaceCandidate(
        place_id="ChIJtest",
        name="Pontem Group Ltd.",
        formatted_address="100 Main St, Vancouver, BC",
        phone="6045550100",
    )
    provider = _FixtureProvider([candidate])
    orchestrator = GoogleEnrichmentOrchestrator(
        settings=_settings(),
        provider=provider,
        fallback_provider=provider,
    )
    writer = MagicMock()

    with patch(
        "pipeline.google_enrichment.orchestrator.fetch_eligible_companies",
        return_value=[_company()],
    ), patch(
        "pipeline.google_enrichment.orchestrator.fetch_reserved_place_ids",
        return_value=frozenset(),
    ), patch(
        "pipeline.google_enrichment.orchestrator.mark_stale_companies",
        return_value=0,
    ), patch(
        "pipeline.google_enrichment.orchestrator.CompanyGoogleWriter",
        return_value=writer,
    ), patch(
        "pipeline.google_enrichment.orchestrator.persist_log_record",
    ):
        result = orchestrator.run(session, run_id="run-live", dry_run=False)

    writer.apply.assert_called_once()
    session.commit.assert_called_once()
    assert result.success == 1
