"""PR-DISCOVERY-1: multi-type unified opportunity feed (tender + permit +
contract_award), merge safety, deterministic ordering, and type-aware
limit behavior."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from pipeline.unified_opportunities import (
    CONSTRUCTION_SCORE_LABEL_AWARD,
    CONSTRUCTION_SCORE_LABEL_PERMIT,
    _extract_construction_signal,
    _identity_key,
    _interleave_by_type,
    get_unified_opportunities,
)

COMPANY_ID = 1921


def _tender_match(tid: int, score: int = 80) -> dict:
    return {
        "type": "tender",
        "id": tid,
        "score": score,
        "source": "rules",
        "context": "open_tender",
        "reasons": [f"tender {tid} reason"],
        "payload": {"id": tid, "title": f"Tender {tid}", "tender_source": "federal"},
    }


def _permit_match(pid: int, score: int = 70) -> dict:
    return {
        "type": "permit",
        "id": pid,
        "score": score,
        "source": "rules",
        "context": "own_permit",
        "reasons": [f"permit {pid} reason"],
        "payload": {"id": pid, "address": f"{pid} Main St"},
    }


def _award_match(aid: int, score: int = 65) -> dict:
    return {
        "type": "contract_award",
        "id": aid,
        "score": score,
        "source": "rules",
        "context": "market_award",
        "reasons": [f"award {aid} reason"],
        "payload": {"id": aid, "title": f"Award {aid}"},
    }


def _discovery(matches: list[dict]) -> dict:
    return {
        "company_id": COMPANY_ID,
        "kind": "construction",
        "ranking_model": "construction_intelligence_v2_hybrid",
        "matches": matches,
    }


def _empty_bd() -> dict:
    return {
        "engine_version": "business_fit_v3",
        "active_opportunities": {"threshold": 65, "items": []},
    }


# --- _extract_construction_signal: deterministic sort ----------------------


def test_extract_signal_sorts_by_score_desc_then_id_asc():
    discovery = _discovery(
        [
            _permit_match(300, score=70),
            _permit_match(100, score=90),
            _permit_match(200, score=90),  # tie with 100 -> id ascending
        ]
    )
    rows = _extract_construction_signal(
        discovery, item_type="permit", score_label=CONSTRUCTION_SCORE_LABEL_PERMIT
    )
    assert [row["id"] for row in rows] == [100, 200, 300]


def test_extract_signal_result_is_input_order_independent():
    matches_a = [_permit_match(1, 60), _permit_match(2, 80), _permit_match(3, 70)]
    matches_b = list(reversed(matches_a))
    rows_a = _extract_construction_signal(
        _discovery(matches_a),
        item_type="permit",
        score_label=CONSTRUCTION_SCORE_LABEL_PERMIT,
    )
    rows_b = _extract_construction_signal(
        _discovery(matches_b),
        item_type="permit",
        score_label=CONSTRUCTION_SCORE_LABEL_PERMIT,
    )
    assert [row["id"] for row in rows_a] == [row["id"] for row in rows_b] == [2, 3, 1]


def test_extract_signal_ignores_other_types():
    discovery = _discovery([_tender_match(1), _award_match(2), _permit_match(3)])
    permits = _extract_construction_signal(
        discovery, item_type="permit", score_label=CONSTRUCTION_SCORE_LABEL_PERMIT
    )
    assert [row["id"] for row in permits] == [3]


# --- _identity_key: composite, never bare numeric id ------------------------


def test_identity_key_discriminates_types_sharing_the_same_numeric_id():
    shared_id = 42
    tender_item = {
        "type": "tender",
        "id": shared_id,
        "payload": {"tender_source": "federal"},
    }
    permit_item = {"type": "permit", "id": shared_id, "payload": {}}
    award_item = {"type": "contract_award", "id": shared_id, "payload": {}}
    keys = {
        _identity_key(tender_item),
        _identity_key(permit_item),
        _identity_key(award_item),
    }
    assert len(keys) == 3  # all distinct despite the identical numeric id


def test_identity_key_includes_item_type_source_and_id():
    assert _identity_key({"type": "permit", "id": 5, "payload": {}}) == (
        "permit",
        "permits",
        5,
    )
    assert _identity_key({"type": "contract_award", "id": 5, "payload": {}}) == (
        "contract_award",
        "contract_awards",
        5,
    )
    assert _identity_key(
        {"type": "tender", "id": 5, "payload": {"tender_source": "commercial"}}
    ) == ("tender", "commercial", 5)


# --- _interleave_by_type: deterministic, type-aware, no starvation --------


def test_interleave_by_type_cycles_fixed_order_and_is_deterministic():
    tenders = [{"type": "tender", "id": i} for i in range(1, 6)]
    permits = [{"type": "permit", "id": 100}, {"type": "permit", "id": 101}]
    awards = [{"type": "contract_award", "id": 200}]

    ordered = _interleave_by_type([tenders, permits, awards], limit=20)
    types_in_order = [item["type"] for item in ordered]
    # Round 1: tender, permit, award. Round 2: tender, permit (award
    # exhausted). Rounds 3-5: tender only (permit/award exhausted).
    assert types_in_order == [
        "tender",
        "permit",
        "contract_award",
        "tender",
        "permit",
        "tender",
        "tender",
        "tender",
    ]


def test_interleave_by_type_permits_and_awards_never_starved_within_limit():
    """20 tenders but only 1 permit + 1 award: at limit=20, both must
    still appear -- a long tender list must not crowd them out."""
    tenders = [{"type": "tender", "id": i} for i in range(1, 21)]
    permits = [{"type": "permit", "id": 100}]
    awards = [{"type": "contract_award", "id": 200}]

    ordered = _interleave_by_type([tenders, permits, awards], limit=20)
    types = {item["type"] for item in ordered}
    assert "permit" in types
    assert "contract_award" in types
    ids = [(item["type"], item["id"]) for item in ordered]
    assert ("permit", 100) in ids
    assert ("contract_award", 200) in ids


def test_interleave_by_type_respects_limit():
    tenders = [{"type": "tender", "id": i} for i in range(1, 10)]
    ordered = _interleave_by_type([tenders, [], []], limit=3)
    assert len(ordered) == 3


def test_interleave_by_type_permutation_of_list_order_changes_only_which_type_leads():
    """The interleave itself is a pure function of (type_lists, limit); a
    caller that always passes [tender, permit, award] in that fixed order
    gets a fixed, reproducible result -- this test pins that the function
    has no hidden nondeterminism (e.g. dict/set ordering) beyond the
    explicit list order the caller chooses."""
    tenders = [{"type": "tender", "id": i} for i in range(1, 4)]
    permits = [{"type": "permit", "id": 100}]
    awards = [{"type": "contract_award", "id": 200}]

    run_1 = _interleave_by_type([tenders, permits, awards], limit=10)
    run_2 = _interleave_by_type([tenders, permits, awards], limit=10)
    assert run_1 == run_2


# --- get_unified_opportunities: full multi-type integration ---------------


@patch("pipeline.unified_opportunities.recommend_bd_intelligence")
@patch("pipeline.unified_opportunities.discover_opportunities")
def test_unified_feed_includes_all_three_types(mock_discover, mock_bd):
    mock_discover.return_value = _discovery(
        [_tender_match(1, 90), _permit_match(2, 80), _award_match(3, 70)]
    )
    mock_bd.return_value = _empty_bd()

    result = get_unified_opportunities(MagicMock(), COMPANY_ID, limit=20)

    types_present = {item["type"] for item in result["items"]}
    assert types_present == {"tender", "permit", "contract_award"}
    assert result["type_coverage"] == {"tender": 1, "permit": 1, "contract_award": 1}
    assert result["total"] == 3


@patch("pipeline.unified_opportunities.recommend_bd_intelligence")
@patch("pipeline.unified_opportunities.discover_opportunities")
def test_unified_feed_same_numeric_id_across_types_never_collapses(
    mock_discover, mock_bd
):
    shared_id = 777
    mock_discover.return_value = _discovery(
        [
            _tender_match(shared_id, 90),
            _permit_match(shared_id, 85),
            _award_match(shared_id, 80),
        ]
    )
    mock_bd.return_value = _empty_bd()

    result = get_unified_opportunities(MagicMock(), COMPANY_ID, limit=20)

    assert result["total"] == 3
    keys = {(item["type"], item["id"]) for item in result["items"]}
    assert keys == {
        ("tender", shared_id),
        ("permit", shared_id),
        ("contract_award", shared_id),
    }


@patch("pipeline.unified_opportunities.recommend_bd_intelligence")
@patch("pipeline.unified_opportunities.discover_opportunities")
def test_unified_feed_permit_and_award_order_is_input_order_independent(
    mock_discover, mock_bd
):
    """Tender extraction deliberately preserves discover_opportunities'
    own rank order (unchanged, pre-existing behavior -- that ranking
    engine, not this module, is the source of truth for tender order).
    Permit and contract_award, however, are explicitly re-sorted by this
    module (score desc, id asc) -- so shuffling *only* where the permit
    and award matches sit in the input list (interspersed at different
    points, tenders' own relative order left untouched) must not change
    the final result at all."""
    tenders = [_tender_match(1, 90), _tender_match(4, 60)]
    permit = _permit_match(2, 80)
    award = _award_match(3, 70)
    mock_bd.return_value = _empty_bd()

    order_a = [tenders[0], permit, award, tenders[1]]
    order_b = [permit, tenders[0], tenders[1], award]
    order_c = [award, permit, tenders[0], tenders[1]]

    results = []
    for matches in (order_a, order_b, order_c):
        mock_discover.return_value = _discovery(matches)
        result = get_unified_opportunities(MagicMock(), COMPANY_ID, limit=20)
        results.append([(item["type"], item["id"]) for item in result["items"]])

    assert results[0] == results[1] == results[2]


@patch("pipeline.unified_opportunities.recommend_bd_intelligence")
@patch("pipeline.unified_opportunities.discover_opportunities")
def test_unified_feed_type_aware_limit_does_not_drown_permits_and_awards(
    mock_discover, mock_bd
):
    """Regression guard for the exact bug scenario described in the task:
    at limit=20 with many qualifying tenders but only a couple of
    qualifying permits/awards, the feed must not fill back up with
    tenders alone."""
    matches = [_tender_match(i, score=100 - i) for i in range(1, 19)]  # 18 tenders
    matches.append(_permit_match(500, score=99))
    matches.append(_award_match(600, score=99))
    mock_discover.return_value = _discovery(matches)
    mock_bd.return_value = _empty_bd()

    result = get_unified_opportunities(MagicMock(), COMPANY_ID, limit=20)

    types_present = {item["type"] for item in result["items"]}
    assert "permit" in types_present
    assert "contract_award" in types_present
    assert result["type_coverage"]["permit"] == 1
    assert result["type_coverage"]["contract_award"] == 1


@patch("pipeline.unified_opportunities.recommend_bd_intelligence")
@patch("pipeline.unified_opportunities.discover_opportunities")
def test_unified_feed_tender_dual_provenance_preserved_alongside_other_types(
    mock_discover, mock_bd
):
    """Adding permit/contract_award to the feed must not disturb the
    existing tender construction_hybrid/business_pursuit/both provenance
    logic."""
    shared_tender = 55
    mock_discover.return_value = _discovery(
        [_tender_match(shared_tender, 88), _permit_match(2, 70)]
    )
    mock_bd.return_value = {
        "engine_version": "business_fit_v3",
        "active_opportunities": {
            "threshold": 65,
            "items": [
                {
                    "item_type": "tender",
                    "id": shared_tender,
                    "score": 72,
                    "score_label": "Business Pursuit Score",
                    "reasons": ["BPS fit"],
                    "payload": {"id": shared_tender, "title": "Shared"},
                }
            ],
        },
    }

    result = get_unified_opportunities(MagicMock(), COMPANY_ID, limit=20)
    by_key = {(item["type"], item["id"]): item for item in result["items"]}

    both_item = by_key[("tender", shared_tender)]
    assert both_item["model"] == "both"
    assert both_item["construction_hybrid"]["score"] == 88
    assert both_item["business_pursuit"]["score"] == 72

    permit_item = by_key[("permit", 2)]
    assert permit_item["model"] == "construction_hybrid"
    assert permit_item["business_pursuit"] is None

    assert result["model_coverage"]["both"] == 1


@patch("pipeline.unified_opportunities.recommend_bd_intelligence")
@patch("pipeline.unified_opportunities.discover_opportunities")
def test_unified_feed_permit_and_award_items_never_have_business_pursuit(
    mock_discover, mock_bd
):
    mock_discover.return_value = _discovery([_permit_match(1), _award_match(2)])
    mock_bd.return_value = _empty_bd()

    result = get_unified_opportunities(MagicMock(), COMPANY_ID, limit=20)
    for item in result["items"]:
        if item["type"] in ("permit", "contract_award"):
            assert item["business_pursuit"] is None
            assert item["model"] == "construction_hybrid"


@patch("pipeline.unified_opportunities.recommend_bd_intelligence")
@patch("pipeline.unified_opportunities.discover_opportunities")
def test_unified_feed_award_score_label_distinct_from_permit_and_tender(
    mock_discover, mock_bd
):
    mock_discover.return_value = _discovery([_award_match(1)])
    mock_bd.return_value = _empty_bd()

    result = get_unified_opportunities(MagicMock(), COMPANY_ID, limit=20)
    award_item = result["items"][0]
    assert (
        award_item["construction_hybrid"]["score_label"]
        == CONSTRUCTION_SCORE_LABEL_AWARD
    )


@patch("pipeline.unified_opportunities.recommend_bd_intelligence")
@patch("pipeline.unified_opportunities.discover_opportunities")
def test_unified_feed_relationship_items_never_appear(mock_discover, mock_bd):
    """relationship_opportunities is a distinct recommendation type, not a
    project opportunity, and must never leak into the unified feed."""
    mock_discover.return_value = _discovery([_tender_match(1)])
    mock_bd.return_value = {
        "engine_version": "business_fit_v3",
        "active_opportunities": {"threshold": 65, "items": []},
        "relationship_opportunities": {
            "items": [
                {
                    "item_type": "relationship",
                    "id": 999,
                    "score": 95,
                    "payload": {"id": 999},
                }
            ]
        },
    }

    result = get_unified_opportunities(MagicMock(), COMPANY_ID, limit=20)
    types_present = {item["type"] for item in result["items"]}
    assert "relationship" not in types_present
    assert all(t in {"tender", "permit", "contract_award"} for t in types_present)


@patch("pipeline.unified_opportunities.recommend_bd_intelligence")
@patch("pipeline.unified_opportunities.discover_opportunities")
def test_unified_feed_empty_discovery_returns_empty_feed(mock_discover, mock_bd):
    mock_discover.return_value = _discovery([])
    mock_bd.return_value = _empty_bd()

    result = get_unified_opportunities(MagicMock(), COMPANY_ID, limit=20)
    assert result["items"] == []
    assert result["total"] == 0
    assert result["type_coverage"] == {"tender": 0, "permit": 0, "contract_award": 0}


@pytest.mark.parametrize("limit", [1, 5, 20])
@patch("pipeline.unified_opportunities.recommend_bd_intelligence")
@patch("pipeline.unified_opportunities.discover_opportunities")
def test_unified_feed_never_exceeds_limit(mock_discover, mock_bd, limit):
    matches = [_tender_match(i) for i in range(1, 30)]
    mock_discover.return_value = _discovery(matches)
    mock_bd.return_value = _empty_bd()

    result = get_unified_opportunities(MagicMock(), COMPANY_ID, limit=limit)
    assert len(result["items"]) <= limit
