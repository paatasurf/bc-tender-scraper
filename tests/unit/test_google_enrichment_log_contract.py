"""Unit tests for Google enrichment log contract (in-memory only)."""

from __future__ import annotations

from pipeline.google_enrichment.config import GoogleEnrichmentSettings
from pipeline.google_enrichment.log_contract import (
    LOG_STATUS_ERROR,
    LOG_STATUS_NO_MATCH,
    LOG_STATUS_SUCCESS,
    build_candidate_snapshot,
    build_log_record,
    evaluate_lookup,
)
from pipeline.google_enrichment.matcher import PlaceMatcher
from pipeline.google_enrichment.models import CompanyMatchContext, PlaceCandidate


def _settings() -> GoogleEnrichmentSettings:
    return GoogleEnrichmentSettings(
        provider="none",
        provider_fallback="none",
        apify_actor_id="",
        oss_scraper_url="",
        stale_days=30,
        batch_size=21,
        confidence_accept=0.70,
        confidence_review=0.55,
        no_match_retry_days=90,
        copy_website_to_website=False,
    )


def test_build_log_record_populates_required_fields():
    record = build_log_record(
        company_id=8638,
        run_id="run-abc",
        provider="fixture",
        query_used="Pontem Group Vancouver BC",
        status=LOG_STATUS_SUCCESS,
        latency_ms=250,
        candidate_count=2,
        match_confidence=0.91,
        candidate_snapshot=[{"place_id": "ChIJ1"}],
        google_place_id="ChIJ1",
    )
    assert record.provider == "fixture"
    assert record.query_used == "Pontem Group Vancouver BC"
    assert record.status == LOG_STATUS_SUCCESS
    assert record.latency_ms == 250
    assert record.candidate_count == 2
    assert record.match_confidence == 0.91
    assert record.candidate_snapshot == [{"place_id": "ChIJ1"}]


def test_evaluate_lookup_creates_log_record_on_success():
    context = CompanyMatchContext(
        company_id=8638,
        name="Pontem Group Inc.",
        city="Vancouver",
        province="BC",
        address="100 Main St, Vancouver, BC",
        phone="6045550100",
    )
    candidate = PlaceCandidate(
        place_id="ChIJtest",
        name="Pontem Group Ltd.",
        formatted_address="100 Main St, Vancouver, BC, Canada",
        phone="6045550100",
    )
    evaluation = evaluate_lookup(
        context,
        candidates=[candidate],
        provider="fixture",
        query_used="Pontem Group Vancouver BC",
        run_id="run-success",
        latency_ms=180,
        settings=_settings(),
    )
    record = evaluation.log_record
    assert record.company_id == 8638
    assert record.run_id == "run-success"
    assert record.provider == "fixture"
    assert record.latency_ms == 180
    assert record.candidate_count == 1
    assert record.status == LOG_STATUS_SUCCESS
    assert record.match_confidence is not None
    assert record.candidate_snapshot is not None
    assert len(record.candidate_snapshot) == 1
    assert record.candidate_snapshot[0]["breakdown"]["total_score"] == record.match_confidence


def test_evaluate_lookup_creates_log_record_on_provider_error():
    context = CompanyMatchContext(company_id=1, name="Test Co")
    evaluation = evaluate_lookup(
        context,
        candidates=[],
        provider="fixture",
        query_used="Test Co Vancouver BC",
        run_id="run-error",
        latency_ms=5000,
        settings=_settings(),
        provider_error="timeout",
    )
    record = evaluation.log_record
    assert record.status == LOG_STATUS_ERROR
    assert record.error_message == "timeout"
    assert record.match_confidence is None
    assert evaluation.enrichment_status == "error"


def test_evaluate_lookup_creates_log_record_when_no_candidates():
    context = CompanyMatchContext(company_id=2, name="Empty Co", city="Vancouver")
    evaluation = evaluate_lookup(
        context,
        candidates=[],
        provider="fixture",
        query_used="Empty Co Vancouver BC",
        run_id="run-empty",
        latency_ms=40,
        settings=_settings(),
    )
    assert evaluation.log_record.status == LOG_STATUS_NO_MATCH
    assert evaluation.log_record.candidate_count == 0
    assert evaluation.enrichment_status == "no_match"


def test_candidate_snapshot_includes_breakdown():
    matcher = PlaceMatcher()
    context = CompanyMatchContext(
        company_id=3,
        name="Pontem Group",
        city="Vancouver",
        province="BC",
        address="100 Main St, Vancouver, BC",
        phone="6045550100",
    )
    candidate = PlaceCandidate(
        place_id="ChIJsnap",
        name="Pontem Group Ltd.",
        formatted_address="100 Main St, Vancouver, BC",
        phone="6045550100",
    )
    scored = matcher.rank_candidates(context, [candidate])
    snapshot = build_candidate_snapshot(scored)
    assert snapshot[0]["breakdown"]["name_score"] == scored[0].breakdown.name_score
    assert snapshot[0]["breakdown"]["total_score"] == scored[0].breakdown.total_score
