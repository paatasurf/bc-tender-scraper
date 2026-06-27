"""Unit tests for early permit signals."""

from __future__ import annotations

from pipeline.early_signals import (
    _event_matches_project_types,
    _event_matches_regions,
    _matches_project_types,
    _permit_matches_regions,
    _permit_matches_value_band,
    _score_early_signal_event,
    pipeline_lag_days,
)


class _PermitStub:
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


def test_pipeline_lag_days():
    permit = _PermitStub(application_date="2026-06-12", issue_date="2026-06-22")
    assert pipeline_lag_days(permit) == 10


def test_permit_matches_regions_by_local_area():
    permit = _PermitStub(local_area="Downtown", city="Vancouver")
    assert _permit_matches_regions(permit, ["Downtown"])
    assert not _permit_matches_regions(permit, ["Burnaby"])


def test_permit_matches_value_band():
    permit = _PermitStub(project_value="1000000")
    assert _permit_matches_value_band(permit, min_value=250_000, max_value=None)
    assert not _permit_matches_value_band(permit, min_value=2_000_000, max_value=None)


class _EventStub:
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


def test_event_matches_regions():
    event = _EventStub(region="Downtown", municipality="Vancouver", property_type="Mixed-use")
    assert _event_matches_regions(event, ["Downtown"])
    assert _event_matches_regions(event, ["vancouver"])
    assert not _event_matches_regions(event, ["Burnaby"])


def test_resolve_market_regions_uses_city_from_google_address():
    from pipeline.early_signals import _resolve_market_regions
    from pipeline.opportunity_discovery import CompanySignals

    signals = CompanySignals(
        name="Example GC",
        project_types=["New Building"],
        neighborhoods=["E 1ST AVENUE", "Downtown"],
        google_address="63 W 6th Ave Suite 309, Vancouver, BC V5Y 1K2, Canada",
        primary_city="",
        primary_address="",
        geographic_reach="",
        avg_project_value=0,
        avg_award_value=0,
        award_categories=[],
        award_clients=[],
        buyer_levels=[],
        ai_reliability_score=None,
    )
    regions = _resolve_market_regions(
        session=type("S", (), {"scalars": lambda *a, **k: type("R", (), {"all": lambda: []})()})(),
        company_id=None,
        signals_model=signals,
        explicit_regions=None,
    )
    assert "vancouver" in {r.lower() for r in regions}
    assert "Downtown" in regions
    assert "E 1ST AVENUE" not in regions


def test_score_early_signal_event_without_company():
    event = _EventStub(
        signal_type="rezoning_application",
        region="Strathcona",
        municipality="Vancouver",
        property_type="Rezoning - Increased Office Use",
    )
    score, reasons = _score_early_signal_event(None, event)
    assert score >= 50
    assert reasons


def test_event_matches_project_types():
    event = _EventStub(
        signal_type="development_permit_application",
        region="Downtown",
        municipality="Vancouver",
        property_type="New Building - Commercial Development",
    )
    assert _event_matches_project_types(event, ["Commercial"])
    assert _event_matches_project_types(event, ["New Building"])
    assert not _event_matches_project_types(event, ["Demolition"])


def test_matches_project_types_empty_list_allows_all():
    assert _matches_project_types("Commercial tower", [])
