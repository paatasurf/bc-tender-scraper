"""Unified opportunities — construction hybrid + BD business pursuit union."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from pipeline.unified_opportunities import (
    BUSINESS_PURSUIT_SCORE_LABEL,
    CONSTRUCTION_SCORE_LABEL_AI,
    _interleave_tender_ids,
    get_unified_opportunities,
)

PONTEM_ID = 8638
TENDER_HEAT_PUMP = 16992
TENDER_CAMPUS_VIEW = 14438


def _pontem_construction_discovery() -> dict:
    return {
        "company_id": PONTEM_ID,
        "kind": "construction",
        "ranking_model": "construction_intelligence_v2_hybrid",
        "matches": [
            {
                "type": "tender",
                "id": TENDER_HEAT_PUMP,
                "score": 86,
                "source": "ai_match",
                "context": "cached_tender_match",
                "reasons": ["Match driven by industry keyword match, domain specialization."],
                "payload": {
                    "id": TENDER_HEAT_PUMP,
                    "title": "Air-to-Water Heat Pump Workforce Capacity Building",
                    "company": "City of Vancouver",
                },
            },
            {
                "type": "permit",
                "id": 4365025,
                "score": 75,
                "source": "rules",
                "context": "own_permit",
                "payload": {"id": 4365025, "address": "1780 FIR STREET"},
            },
            {
                "type": "tender",
                "id": 14434,
                "score": 79,
                "source": "ai_match",
                "context": "cached_tender_match",
                "reasons": ["Match driven by industry keyword match."],
                "payload": {"id": 14434, "title": "Firehall No.1 Building Envelope Renewal"},
            },
        ],
    }


def _pontem_bd_intelligence() -> dict:
    return {
        "company_id": PONTEM_ID,
        "engine_version": "business_fit_v3",
        "active_opportunities": {
            "threshold": 65,
            "items": [
                {
                    "item_type": "tender",
                    "id": TENDER_CAMPUS_VIEW,
                    "score": 80,
                    "score_label": BUSINESS_PURSUIT_SCORE_LABEL,
                    "pursuit_verdict": "Pursue — strong alignment across trade, sector, and geography",
                    "fit_assessment": {"business_fit": {"score": 100}},
                    "reasons": ["Trade alignment: general_building"],
                    "payload": {
                        "id": TENDER_CAMPUS_VIEW,
                        "title": "CRHC – Campus View Affordable Housing – Appliances",
                    },
                },
            ],
        },
    }


@patch("pipeline.unified_opportunities.recommend_bd_intelligence")
@patch("pipeline.unified_opportunities.discover_opportunities")
def test_pontem_unified_includes_both_models(mock_discover, mock_bd):
    mock_discover.return_value = _pontem_construction_discovery()
    mock_bd.return_value = _pontem_bd_intelligence()

    result = get_unified_opportunities(MagicMock(), PONTEM_ID, limit=20)

    by_id = {item["tender_id"]: item for item in result["items"]}

    assert TENDER_HEAT_PUMP in by_id
    assert TENDER_CAMPUS_VIEW in by_id

    heat = by_id[TENDER_HEAT_PUMP]
    assert heat["model"] == "construction_hybrid"
    assert heat["construction_hybrid"]["score"] == 86
    assert heat["construction_hybrid"]["score_label"] == CONSTRUCTION_SCORE_LABEL_AI
    assert heat["construction_hybrid"]["source"] == "ai_match"
    assert heat["business_pursuit"] is None

    campus = by_id[TENDER_CAMPUS_VIEW]
    assert campus["model"] == "business_pursuit"
    assert campus["business_pursuit"]["score"] == 80
    assert campus["business_pursuit"]["score_label"] == BUSINESS_PURSUIT_SCORE_LABEL
    assert campus["construction_hybrid"] is None

    assert result["model_coverage"] == {
        "construction_hybrid": 2,
        "business_pursuit": 1,
        "both": 0,
    }
    assert result["total"] == 3


@patch("pipeline.unified_opportunities.recommend_bd_intelligence")
@patch("pipeline.unified_opportunities.discover_opportunities")
def test_dedup_both_models_keep_native_scores(mock_discover, mock_bd):
    shared_id = 99999
    mock_discover.return_value = {
        "ranking_model": "construction_intelligence_v2_hybrid",
        "matches": [
            {
                "type": "tender",
                "id": shared_id,
                "score": 88,
                "source": "ai_match",
                "context": "cached_tender_match",
                "reasons": ["Hybrid match"],
                "payload": {"id": shared_id, "title": "Shared Tender"},
            },
        ],
    }
    mock_bd.return_value = {
        "engine_version": "business_fit_v3",
        "active_opportunities": {
            "threshold": 65,
            "items": [
                {
                    "item_type": "tender",
                    "id": shared_id,
                    "score": 72,
                    "score_label": BUSINESS_PURSUIT_SCORE_LABEL,
                    "pursuit_verdict": "Prepare",
                    "reasons": ["BPS fit"],
                    "payload": {"id": shared_id, "title": "Shared Tender"},
                },
            ],
        },
    }

    result = get_unified_opportunities(MagicMock(), PONTEM_ID, limit=10)
    assert result["total"] == 1
    item = result["items"][0]
    assert item["tender_id"] == shared_id
    assert item["model"] == "both"
    assert item["construction_hybrid"]["score"] == 88
    assert item["construction_hybrid"]["score_label"] == CONSTRUCTION_SCORE_LABEL_AI
    assert item["business_pursuit"]["score"] == 72
    assert item["business_pursuit"]["score_label"] == BUSINESS_PURSUIT_SCORE_LABEL
    assert result["model_coverage"]["both"] == 1


def test_interleave_alternates_rank_lists():
    ordered = _interleave_tender_ids([1, 2, 3], [10, 20], limit=10)
    assert ordered == [1, 10, 2, 20, 3]


def test_interleave_dedupes_same_id():
    ordered = _interleave_tender_ids([100, 200], [100, 300], limit=10)
    assert ordered == [100, 200, 300]


def test_interleave_respects_limit():
    ordered = _interleave_tender_ids([1, 2, 3, 4], [10, 20, 30, 40], limit=3)
    assert ordered == [1, 10, 2]
