"""Unit tests for early permit signals."""

from __future__ import annotations

import random
from unittest.mock import MagicMock, patch

import pytest

from pipeline.early_signals import (
    _classify_signal_quality,
    _event_matches_project_types,
    _event_matches_regions,
    _matches_project_types,
    _order_signals_deterministically,
    _permit_matches_regions,
    _permit_matches_value_band,
    _score_early_signal_event,
    get_early_signals,
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
    event = _EventStub(
        region="Downtown", municipality="Vancouver", property_type="Mixed-use"
    )
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
        session=type(
            "S", (), {"scalars": lambda *a, **k: type("R", (), {"all": lambda: []})()}
        )(),
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


# --- PR-EARLY-1: min_score enforcement, deterministic ordering, diagnostics


def _row(*, id, score, scraped_at, signal_type="permit_application"):
    return {
        "id": id,
        "signal_type": signal_type,
        "score": score,
        "scraped_at": scraped_at,
        "application_date": scraped_at,
    }


def _empty_diag():
    return {"scanned": 0, "rejected_by_region": 0, "rejected_by_project_type": 0}


# --- _order_signals_deterministically: pure ordering logic ----------------


def test_order_deterministically_newest_first():
    rows = [
        _row(id=1, score=80, scraped_at="2026-07-01"),
        _row(id=2, score=80, scraped_at="2026-07-03"),
        _row(id=3, score=80, scraped_at="2026-07-02"),
    ]
    ordered = _order_signals_deterministically(rows)
    assert [row["id"] for row in ordered] == [2, 3, 1]


def test_order_deterministically_ties_broken_by_score_then_type_then_id():
    same_date = "2026-07-01"
    rows = [
        _row(id=5, score=60, scraped_at=same_date, signal_type="rezoning_application"),
        _row(id=1, score=70, scraped_at=same_date, signal_type="permit_application"),
        _row(
            id=2,
            score=70,
            scraped_at=same_date,
            signal_type="development_permit_application",
        ),
        _row(
            id=3,
            score=70,
            scraped_at=same_date,
            signal_type="development_permit_application",
        ),
    ]
    ordered = _order_signals_deterministically(rows)
    # Highest score first (70s before 60); among the 70s, signal_type
    # lexicographic ("development_permit_application" < "permit_application");
    # among the two development_permit_application rows (id 2 and 3), id
    # ascending.
    assert [row["id"] for row in ordered] == [2, 3, 1, 5]


def test_order_deterministically_is_input_order_independent():
    rows = [
        _row(id=1, score=70, scraped_at="2026-07-01"),
        _row(id=2, score=90, scraped_at="2026-07-01"),
        _row(id=3, score=50, scraped_at="2026-07-02"),
        _row(id=4, score=90, scraped_at="2026-07-02"),
    ]
    expected = [row["id"] for row in _order_signals_deterministically(rows)]
    for _ in range(5):
        shuffled = list(rows)
        random.Random(42).shuffle(shuffled)
        result = [row["id"] for row in _order_signals_deterministically(shuffled)]
        assert result == expected


# --- get_early_signals: min_score enforcement, limit, diagnostics --------


@patch("pipeline.early_signals._collect_event_signals")
@patch("pipeline.early_signals._collect_permit_signals")
def test_permit_and_event_below_threshold_are_excluded(mock_permits, mock_events):
    mock_permits.return_value = (
        [
            _row(id=1, score=70, scraped_at="2026-07-03"),
            _row(id=2, score=40, scraped_at="2026-07-02"),
        ],
        _empty_diag(),
    )
    mock_events.return_value = (
        [
            _row(
                id=3,
                score=65,
                scraped_at="2026-07-01",
                signal_type="rezoning_application",
            ),
            _row(
                id=4,
                score=10,
                scraped_at="2026-07-04",
                signal_type="rezoning_application",
            ),
        ],
        _empty_diag(),
    )

    result = get_early_signals(MagicMock(), company_id=None, min_score=50, limit=15)
    ids = {row["id"] for row in result["signals"]}
    assert ids == {1, 3}
    assert result["diagnostics"]["rejected_by_min_score"] == 2


@patch("pipeline.early_signals._collect_event_signals")
@patch("pipeline.early_signals._collect_permit_signals")
def test_score_exactly_at_threshold_is_included(mock_permits, mock_events):
    mock_permits.return_value = (
        [_row(id=1, score=50, scraped_at="2026-07-01")],
        _empty_diag(),
    )
    mock_events.return_value = ([], _empty_diag())

    result = get_early_signals(MagicMock(), company_id=None, min_score=50, limit=15)
    assert [row["id"] for row in result["signals"]] == [1]
    assert result["diagnostics"]["rejected_by_min_score"] == 0


@patch("pipeline.early_signals._collect_event_signals")
@patch("pipeline.early_signals._collect_permit_signals")
def test_score_one_below_threshold_is_excluded(mock_permits, mock_events):
    mock_permits.return_value = (
        [_row(id=1, score=49, scraped_at="2026-07-01")],
        _empty_diag(),
    )
    mock_events.return_value = ([], _empty_diag())

    result = get_early_signals(MagicMock(), company_id=None, min_score=50, limit=15)
    assert result["signals"] == []
    assert result["diagnostics"]["rejected_by_min_score"] == 1


@patch("pipeline.early_signals._collect_event_signals")
@patch("pipeline.early_signals._collect_permit_signals")
def test_limit_applies_after_threshold_filtering(mock_permits, mock_events):
    # 5 rows qualify (score >= 50); 2 more are below threshold. With
    # limit=3, exactly the 3 best-qualifying rows must be returned -- the
    # below-threshold rows must never occupy a limited slot.
    mock_permits.return_value = (
        [
            _row(id=1, score=90, scraped_at="2026-07-05"),
            _row(id=2, score=80, scraped_at="2026-07-04"),
            _row(id=3, score=70, scraped_at="2026-07-03"),
            _row(id=4, score=60, scraped_at="2026-07-02"),
            _row(id=5, score=50, scraped_at="2026-07-01"),
            _row(
                id=6, score=49, scraped_at="2026-07-06"
            ),  # newest, but below threshold
            _row(
                id=7, score=10, scraped_at="2026-07-07"
            ),  # newest, but below threshold
        ],
        _empty_diag(),
    )
    mock_events.return_value = ([], _empty_diag())

    result = get_early_signals(MagicMock(), company_id=None, min_score=50, limit=3)
    assert [row["id"] for row in result["signals"]] == [1, 2, 3]
    assert result["diagnostics"]["rejected_by_min_score"] == 2
    assert result["diagnostics"]["returned_count"] == 3
    assert result["total"] == 3


@patch("pipeline.early_signals._collect_event_signals")
@patch("pipeline.early_signals._collect_permit_signals")
def test_diagnostics_counts_are_correct_and_aggregate_only(mock_permits, mock_events):
    mock_permits.return_value = (
        [_row(id=1, score=90, scraped_at="2026-07-01")],
        {"scanned": 10, "rejected_by_region": 3, "rejected_by_project_type": 2},
    )
    mock_events.return_value = (
        [
            _row(
                id=2,
                score=30,
                scraped_at="2026-07-02",
                signal_type="rezoning_application",
            )
        ],
        {"scanned": 5, "rejected_by_region": 1, "rejected_by_project_type": 0},
    )

    result = get_early_signals(MagicMock(), company_id=None, min_score=50, limit=15)
    diagnostics = result["diagnostics"]

    assert diagnostics == {
        "scanned_permits": 10,
        "scanned_events": 5,
        "rejected_by_region": 4,
        "rejected_by_project_type": 2,
        "rejected_by_min_score": 1,
        "returned_count": 1,
    }
    # Aggregate-only: exactly these six counters, every value a plain
    # non-negative int -- no raw ids/names/addresses/payload/exception text.
    assert set(diagnostics.keys()) == {
        "scanned_permits",
        "scanned_events",
        "rejected_by_region",
        "rejected_by_project_type",
        "rejected_by_min_score",
        "returned_count",
    }
    for value in diagnostics.values():
        assert isinstance(value, int) and not isinstance(value, bool)
        assert value >= 0


@patch("pipeline.early_signals._collect_event_signals")
@patch("pipeline.early_signals._collect_permit_signals")
def test_existing_signals_payload_and_signal_types_preserved(mock_permits, mock_events):
    mock_permits.return_value = (
        [_row(id=1, score=90, scraped_at="2026-07-01")],
        _empty_diag(),
    )
    mock_events.return_value = (
        [
            _row(
                id=2,
                score=80,
                scraped_at="2026-07-02",
                signal_type="rezoning_application",
            )
        ],
        _empty_diag(),
    )

    result = get_early_signals(MagicMock(), company_id=None, min_score=50, limit=15)
    assert set(result.keys()) >= {
        "data_scope",
        "lookback_days",
        "company_id",
        "kind",
        "total",
        "market_regions",
        "market_project_types",
        "signal_types",
        "diagnostics",
        "signals",
    }
    assert result["signal_types"] == {
        "permit_application": 1,
        "development_permit_application": 0,
        "rezoning_application": 1,
    }


@patch("pipeline.early_signals._load_company_signals")
@patch("pipeline.early_signals._collect_event_signals")
@patch("pipeline.early_signals._collect_permit_signals")
def test_get_early_signals_for_profile_now_enforces_min_score(
    mock_permits, mock_events, mock_load_signals
):
    from pipeline.early_signals import get_early_signals_for_profile

    mock_load_signals.return_value = (None, None)
    mock_permits.return_value = (
        [
            _row(id=1, score=60, scraped_at="2026-07-01"),
            _row(id=2, score=30, scraped_at="2026-07-02"),
        ],
        _empty_diag(),
    )
    mock_events.return_value = ([], _empty_diag())

    profile = MagicMock()
    profile.company_id = 1921
    profile.min_project_value = None
    profile.max_project_value = None
    profile.regions = []

    signals = get_early_signals_for_profile(
        MagicMock(), profile, lookback_days=7, limit=8
    )
    assert [row["id"] for row in signals] == [1]


# --- API endpoint: threads and enforces min_score --------------------------


def test_early_signals_endpoint_passes_and_enforces_min_score():
    from fastapi.testclient import TestClient

    from api.main import app

    captured = {}

    def fake_get_early_signals(_session, **kwargs):
        captured.update(kwargs)
        return {
            "data_scope": "market_early_signal_events",
            "lookback_days": kwargs["lookback_days"],
            "company_id": kwargs["company_id"],
            "kind": kwargs["kind"],
            "total": 0,
            "market_regions": [],
            "market_project_types": [],
            "signal_types": {
                "permit_application": 0,
                "development_permit_application": 0,
                "rezoning_application": 0,
            },
            "diagnostics": {
                "scanned_permits": 0,
                "scanned_events": 0,
                "rejected_by_region": 0,
                "rejected_by_project_type": 0,
                "rejected_by_min_score": 0,
                "returned_count": 0,
            },
            "signals": [],
        }

    client = TestClient(app)
    with patch("pipeline.early_signals.get_early_signals", fake_get_early_signals):
        with patch("api.main.get_session") as mock_get_session:
            mock_get_session.return_value = MagicMock()
            response = client.get(
                "/api/early-signals",
                params={"company_id": 1921, "min_score": 72},
            )

    assert response.status_code == 200
    assert captured["min_score"] == 72
    body = response.json()
    assert "diagnostics" in body


# --- PR-EARLY-3A: deterministic signal quality layer ------------------


def test_development_and_rezoning_get_high_potential_quality():
    for signal_type in ("development_permit_application", "rezoning_application"):
        score, tier, reasons = _classify_signal_quality(
            signal_type=signal_type,
            haystack="New Building - Mixed Use Development",
            estimated_value=None,
            specializations=[],
        )
        assert tier == "high_potential"
        assert score >= 70
        assert reasons


def test_small_interior_permit_downranked_for_general_contractor():
    score, tier, reasons = _classify_signal_quality(
        signal_type="permit_application",
        haystack="Interior Alteration - Single Family Dwelling",
        estimated_value=None,
        specializations=["General Contracting"],
    )
    assert tier == "low_priority"
    assert any("maintenance-scale" in r.lower() for r in reasons)


def test_development_outranks_small_interior_permit_for_general_contractor():
    dev_score, dev_tier, _ = _classify_signal_quality(
        signal_type="development_permit_application",
        haystack="New Building - Mixed Use Development",
        estimated_value=None,
        specializations=["General Contracting"],
    )
    permit_score, permit_tier, _ = _classify_signal_quality(
        signal_type="permit_application",
        haystack="Interior Alteration - Single Family Dwelling",
        estimated_value=None,
        specializations=["General Contracting"],
    )
    assert dev_score > permit_score
    assert dev_tier == "high_potential"
    assert permit_tier == "low_priority"


def test_electrical_solar_specialization_prevents_unfair_downrank():
    baseline_score, baseline_tier, _ = _classify_signal_quality(
        signal_type="permit_application",
        haystack="Interior Alteration - Single Family Dwelling",
        estimated_value=None,
        specializations=["General Contracting"],
    )
    solar_score, solar_tier, solar_reasons = _classify_signal_quality(
        signal_type="permit_application",
        haystack="Interior Alteration - Solar Panel Installation Single Family Dwelling",
        estimated_value=None,
        specializations=["Solar", "Electrical"],
    )
    assert solar_score > baseline_score
    assert baseline_tier == "low_priority"
    assert solar_tier != "low_priority"
    assert any("specialization" in r.lower() for r in solar_reasons)


def test_renovation_specialization_prevents_unfair_interior_alteration_downrank():
    unprotected_score, unprotected_tier, _ = _classify_signal_quality(
        signal_type="permit_application",
        haystack="Interior Alteration - Retail Space",
        estimated_value=None,
        specializations=["Commercial Development"],
    )
    protected_score, protected_tier, protected_reasons = _classify_signal_quality(
        signal_type="permit_application",
        haystack="Interior Alteration - Retail Space",
        estimated_value=None,
        specializations=["Renovation", "Interior Alteration"],
    )
    assert protected_score > unprotected_score
    assert unprotected_tier == "low_priority"
    assert protected_tier != "low_priority"
    assert any("specialization" in r.lower() for r in protected_reasons)


def test_value_boost_only_applies_when_value_present():
    no_value_score, _no_value_tier, no_value_reasons = _classify_signal_quality(
        signal_type="permit_application",
        haystack="New Building - Commercial",
        estimated_value=None,
        specializations=[],
    )
    with_value_score, _with_value_tier, with_value_reasons = _classify_signal_quality(
        signal_type="permit_application",
        haystack="New Building - Commercial",
        estimated_value=6_000_000,
        specializations=[],
    )
    assert with_value_score > no_value_score
    assert not any("value" in r.lower() for r in no_value_reasons)
    assert any("value" in r.lower() for r in with_value_reasons)


def test_zero_value_never_boosts_and_never_penalizes():
    zero_score, _tier, zero_reasons = _classify_signal_quality(
        signal_type="permit_application",
        haystack="New Building - Commercial",
        estimated_value=0,
        specializations=[],
    )
    none_score, _tier2, _none_reasons = _classify_signal_quality(
        signal_type="permit_application",
        haystack="New Building - Commercial",
        estimated_value=None,
        specializations=[],
    )
    assert zero_score == none_score
    assert not any("value" in r.lower() for r in zero_reasons)


@pytest.mark.parametrize(
    ("signal_type", "haystack", "specializations", "expected_tier"),
    [
        ("development_permit_application", "New Building", [], "high_potential"),
        ("permit_application", "New Commercial Building Addition", [], "market_watch"),
        (
            "permit_application",
            "Medical Lift Installation",
            ["General Contracting"],
            "low_priority",
        ),
    ],
)
def test_all_three_tiers_are_reachable_and_explainable(
    signal_type, haystack, specializations, expected_tier
):
    score, tier, reasons = _classify_signal_quality(
        signal_type=signal_type,
        haystack=haystack,
        estimated_value=None,
        specializations=specializations,
    )
    assert tier == expected_tier
    assert reasons
    assert all(isinstance(reason, str) and reason for reason in reasons)


def test_quality_score_never_claims_win_probability_or_tender_guarantee():
    forbidden_phrases = (
        "win probability",
        "will win",
        "guaranteed tender",
        "guaranteed to",
    )
    for signal_type, haystack, specializations in (
        ("development_permit_application", "New Building", []),
        ("permit_application", "Interior Alteration - Single Family Dwelling", []),
        ("permit_application", "Solar Panel Installation", ["Solar"]),
    ):
        _score, _tier, reasons = _classify_signal_quality(
            signal_type=signal_type,
            haystack=haystack,
            estimated_value=None,
            specializations=specializations,
        )
        combined = " ".join(reasons).lower()
        for phrase in forbidden_phrases:
            assert phrase not in combined


# --- deterministic ordering: quality_score as primary sort key ---------


def _row_with_quality(
    *, id, score, quality_score, scraped_at, signal_type="permit_application"
):
    return {
        "id": id,
        "signal_type": signal_type,
        "score": score,
        "quality_score": quality_score,
        "scraped_at": scraped_at,
        "application_date": scraped_at,
    }


def test_order_deterministically_quality_score_is_primary_key():
    rows = [
        _row_with_quality(id=1, score=90, quality_score=40, scraped_at="2026-07-05"),
        _row_with_quality(id=2, score=50, quality_score=80, scraped_at="2026-07-01"),
    ]
    ordered = _order_signals_deterministically(rows)
    # id=2 has a lower relevance score AND an older date, but a higher
    # quality_score -- it must still rank first.
    assert [row["id"] for row in ordered] == [2, 1]


def test_order_deterministically_quality_ties_fall_back_to_existing_chain():
    rows = [
        _row_with_quality(
            id=5,
            score=60,
            quality_score=70,
            scraped_at="2026-07-01",
            signal_type="rezoning_application",
        ),
        _row_with_quality(id=1, score=70, quality_score=70, scraped_at="2026-07-03"),
        _row_with_quality(
            id=2,
            score=70,
            quality_score=70,
            scraped_at="2026-07-03",
            signal_type="development_permit_application",
        ),
    ]
    ordered = _order_signals_deterministically(rows)
    # All quality_score=70 (tied). Freshness: ids 1 & 2 (2026-07-03) before
    # id 5 (2026-07-01). Among the tied-freshness pair, relevance score
    # ties too (70), so signal_type breaks it lexicographically:
    # "development_permit_application" < "permit_application".
    assert [row["id"] for row in ordered] == [2, 1, 5]


def test_order_deterministically_with_quality_is_input_order_independent():
    rows = [
        _row_with_quality(id=1, score=70, quality_score=50, scraped_at="2026-07-01"),
        _row_with_quality(id=2, score=90, quality_score=90, scraped_at="2026-07-01"),
        _row_with_quality(id=3, score=50, quality_score=50, scraped_at="2026-07-02"),
        _row_with_quality(id=4, score=90, quality_score=90, scraped_at="2026-07-02"),
    ]
    expected = [row["id"] for row in _order_signals_deterministically(rows)]
    for _ in range(5):
        shuffled = list(rows)
        random.Random(7).shuffle(shuffled)
        result = [row["id"] for row in _order_signals_deterministically(shuffled)]
        assert result == expected


# --- get_early_signals: quality ordering + unchanged score/min_score ---


def _quality_row(
    id, score, quality_score, scraped_at, signal_type="permit_application"
):
    return {
        "id": id,
        "signal_type": signal_type,
        "score": score,
        "quality_score": quality_score,
        "quality_tier": "market_watch",
        "quality_reasons": ["stub reason"],
        "scraped_at": scraped_at,
        "application_date": scraped_at,
    }


@patch("pipeline.early_signals._collect_event_signals")
@patch("pipeline.early_signals._collect_permit_signals")
def test_get_early_signals_orders_by_quality_score_first(mock_permits, mock_events):
    mock_permits.return_value = (
        [
            _quality_row(
                1, 90, 20, "2026-07-05"
            ),  # newest, high relevance, LOW quality
            _quality_row(
                2, 60, 90, "2026-07-01"
            ),  # older, lower relevance, HIGH quality
        ],
        _empty_diag(),
    )
    mock_events.return_value = ([], _empty_diag())

    result = get_early_signals(MagicMock(), company_id=None, min_score=50, limit=15)
    assert [row["id"] for row in result["signals"]] == [2, 1]


@patch("pipeline.early_signals._collect_event_signals")
@patch("pipeline.early_signals._collect_permit_signals")
def test_min_score_still_gates_on_relevance_score_not_quality_score(
    mock_permits, mock_events
):
    mock_permits.return_value = (
        [
            # Low relevance score, very high quality -- must still be
            # excluded by min_score (the score/min_score contract is
            # unchanged by the quality layer).
            _quality_row(1, 30, 95, "2026-07-01"),
            # High relevance score, very low quality -- must still be
            # included.
            _quality_row(2, 80, 5, "2026-07-02"),
        ],
        _empty_diag(),
    )
    mock_events.return_value = ([], _empty_diag())

    result = get_early_signals(MagicMock(), company_id=None, min_score=50, limit=15)
    ids = [row["id"] for row in result["signals"]]
    assert ids == [2]
    assert result["diagnostics"]["rejected_by_min_score"] == 1


@patch("pipeline.early_signals._collect_event_signals")
@patch("pipeline.early_signals._collect_permit_signals")
def test_diagnostics_still_aggregate_only_with_quality_layer_present(
    mock_permits, mock_events
):
    mock_permits.return_value = (
        [_quality_row(1, 90, 80, "2026-07-01")],
        {"scanned": 10, "rejected_by_region": 3, "rejected_by_project_type": 2},
    )
    mock_events.return_value = (
        [_quality_row(2, 30, 20, "2026-07-02", signal_type="rezoning_application")],
        {"scanned": 5, "rejected_by_region": 1, "rejected_by_project_type": 0},
    )

    result = get_early_signals(MagicMock(), company_id=None, min_score=50, limit=15)
    diagnostics = result["diagnostics"]
    assert set(diagnostics.keys()) == {
        "scanned_permits",
        "scanned_events",
        "rejected_by_region",
        "rejected_by_project_type",
        "rejected_by_min_score",
        "returned_count",
    }
    for value in diagnostics.values():
        assert isinstance(value, int) and not isinstance(value, bool)
        assert value >= 0
