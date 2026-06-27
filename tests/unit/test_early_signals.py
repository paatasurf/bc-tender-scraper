"""Unit tests for early permit signals."""

from __future__ import annotations

from pipeline.early_signals import (
    _event_matches_regions,
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
    assert not _event_matches_regions(event, ["Burnaby"])


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
