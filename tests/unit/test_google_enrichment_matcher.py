"""Unit tests for PlaceMatcher deterministic scoring."""

from __future__ import annotations

import pytest

from pipeline.google_enrichment.config import GoogleEnrichmentSettings
from pipeline.google_enrichment.log_contract import (
    LOG_STATUS_REJECTED,
    LOG_STATUS_REVIEW,
    LOG_STATUS_SUCCESS,
    evaluate_lookup,
)
from pipeline.google_enrichment.matcher import (
    HARD_REJECT_DUPLICATE_PLACE_ID,
    HARD_REJECT_PROVINCE_OUTSIDE_BC,
    PlaceMatcher,
)
from pipeline.google_enrichment.models import CompanyMatchContext, PlaceCandidate


def _candidate(**overrides) -> PlaceCandidate:
    defaults = {
        "place_id": "ChIJabc",
        "name": "Pontem Group Ltd.",
        "formatted_address": "100 Main St, Vancouver, BC, Canada",
        "phone": "+1 604-555-0100",
    }
    defaults.update(overrides)
    return PlaceCandidate(**defaults)


def _context(**overrides) -> CompanyMatchContext:
    defaults = {
        "company_id": 8638,
        "name": "Pontem Group Inc.",
        "city": "Vancouver",
        "province": "BC",
        "address": "100 Main Street, Vancouver, BC",
        "phone": "6045550100",
    }
    defaults.update(overrides)
    return CompanyMatchContext(**defaults)


@pytest.fixture
def matcher() -> PlaceMatcher:
    return PlaceMatcher()


def test_identical_input_produces_identical_score(matcher: PlaceMatcher):
    context = _context()
    candidate = _candidate()
    first = matcher.score_context(context, candidate)
    second = matcher.score_context(context, candidate)
    assert first.breakdown == second.breakdown
    assert first.confidence == second.confidence


def test_name_normalization_inc_ltd(matcher: PlaceMatcher):
    breakdown = matcher.score(
        "Pontem Group Inc.",
        "Vancouver",
        "BC",
        "",
        "",
        _candidate(name="Pontem Group Ltd."),
    )
    assert breakdown.name_score == 1.0


def test_missing_phone_scores_zero_phone_component(matcher: PlaceMatcher):
    breakdown = matcher.score(
        "Pontem Group",
        "Vancouver",
        "BC",
        "100 Main St, Vancouver, BC",
        "",
        _candidate(phone=""),
    )
    assert breakdown.phone_score == 0.0


def test_matching_phone_adds_phone_bonus(matcher: PlaceMatcher):
    breakdown = matcher.score(
        "Pontem Group",
        "Vancouver",
        "BC",
        "",
        "604-555-0100",
        _candidate(phone="(604) 555-0100"),
    )
    assert breakdown.phone_score == 1.0


def test_missing_city_reduces_city_score(matcher: PlaceMatcher):
    breakdown = matcher.score(
        "Pontem Group",
        "",
        "BC",
        "",
        "",
        _candidate(formatted_address="100 Main St, Toronto, ON, Canada"),
    )
    assert breakdown.city_score == 0.0


def test_punctuation_in_address_does_not_break_similarity(matcher: PlaceMatcher):
    breakdown = matcher.score(
        "Pontem Group",
        "Vancouver",
        "BC",
        "100 Main St., Vancouver, BC",
        "",
        _candidate(formatted_address="100 Main St Vancouver BC"),
    )
    assert breakdown.address_score > 0.7


def test_hard_reject_duplicate_place_id(matcher: PlaceMatcher):
    scored = matcher.score_context(
        _context(),
        _candidate(place_id="ChIJdup"),
        reserved_place_ids=frozenset({"ChIJdup"}),
    )
    assert scored.hard_rejected is True
    assert scored.reject_reason == HARD_REJECT_DUPLICATE_PLACE_ID
    assert scored.confidence == 0.0


def test_hard_reject_province_outside_bc(matcher: PlaceMatcher):
    scored = matcher.score_context(
        _context(),
        _candidate(formatted_address="50 King St, Toronto, ON, Canada"),
    )
    assert scored.hard_rejected is True
    assert scored.reject_reason == HARD_REJECT_PROVINCE_OUTSIDE_BC


def test_duplicate_candidate_ordering_is_stable(matcher: PlaceMatcher):
    low = _candidate(place_id="ChIJaaa", name="Other Co", formatted_address="1 A St, Vancouver, BC")
    high = _candidate(place_id="ChIJbbb", name="Pontem Group", formatted_address="100 Main St, Vancouver, BC")
    tie_a = _candidate(place_id="ChIJccc", name="Pontem Group", formatted_address="100 Main St, Vancouver, BC")
    tie_b = _candidate(place_id="ChIJddd", name="Pontem Group", formatted_address="100 Main St, Vancouver, BC")

    ranked = matcher.rank_candidates(_context(), [low, tie_b, high, tie_a])
    assert ranked[0].candidate.place_id == "ChIJbbb"
    assert ranked[1].candidate.place_id == "ChIJccc"
    assert ranked[2].candidate.place_id == "ChIJddd"
    assert ranked[-1].candidate.place_id == "ChIJaaa"


def test_threshold_auto_accept(matcher: PlaceMatcher):
    settings = GoogleEnrichmentSettings(
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
    evaluation = evaluate_lookup(
        _context(),
        candidates=[_candidate()],
        provider="fixture",
        query_used="Pontem Group Vancouver BC",
        run_id="run-1",
        latency_ms=120,
        settings=settings,
    )
    assert evaluation.log_record.status == LOG_STATUS_SUCCESS
    assert evaluation.enrichment_status == "enriched"
    assert evaluation.log_record.match_confidence >= 0.70


def test_threshold_review_band():
    settings = GoogleEnrichmentSettings(
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
    candidate = _candidate(
        name="Pontem Construction",
        formatted_address="200 Side St, Vancouver, BC",
        phone="",
    )
    evaluation = evaluate_lookup(
        _context(name="Pontem Group", address="999 Other Ave, Vancouver, BC", phone=""),
        candidates=[candidate],
        provider="fixture",
        query_used="Pontem Group Vancouver BC",
        run_id="run-2",
        latency_ms=90,
        settings=settings,
    )
    assert evaluation.log_record.status == LOG_STATUS_REVIEW
    assert evaluation.enrichment_status == "review"
    assert 0.55 <= (evaluation.log_record.match_confidence or 0) < 0.70


def test_threshold_below_review_is_rejected():
    settings = GoogleEnrichmentSettings(
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
    candidate = _candidate(
        name="Coastal Drywall",
        formatted_address="500 Remote Rd, Vancouver, BC",
        phone="",
    )
    evaluation = evaluate_lookup(
        _context(),
        candidates=[candidate],
        provider="fixture",
        query_used="Pontem Group Vancouver BC",
        run_id="run-3",
        latency_ms=75,
        settings=settings,
    )
    assert evaluation.log_record.status == LOG_STATUS_REJECTED
    assert evaluation.enrichment_status == "no_match"


def test_match_breakdown_fields_present(matcher: PlaceMatcher):
    breakdown = matcher.score(
        "Pontem Group",
        "Vancouver",
        "BC",
        "100 Main St, Vancouver, BC",
        "6045550100",
        _candidate(),
    )
    assert hasattr(breakdown, "name_score")
    assert hasattr(breakdown, "city_score")
    assert hasattr(breakdown, "province_score")
    assert hasattr(breakdown, "address_score")
    assert hasattr(breakdown, "phone_score")
    assert breakdown.total_score == min(
        1.0,
        round(
            0.40 * breakdown.name_score
            + 0.25 * breakdown.city_score
            + 0.10 * breakdown.province_score
            + 0.15 * breakdown.address_score
            + 0.10 * breakdown.phone_score,
            4,
        ),
    )
