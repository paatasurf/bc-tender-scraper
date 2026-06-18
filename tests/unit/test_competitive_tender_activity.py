"""Unit tests for missed opportunities and competitor tender activity."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from pipeline.competitive_intel.tender_activity import (
    get_competitor_tender_activity,
    get_missed_opportunities,
)
from pipeline.competitive_intel.types import TopCompetitor


def _peer(company_id: int, name: str, threat: int = 70) -> TopCompetitor:
    return TopCompetitor(
        company_id=company_id,
        name=name,
        company_kind="construction",
        threat_score=threat,
        threat_breakdown={"score": threat, "breakdown": [], "confidence": "medium"},
        similarity=0.8,
        total_projects=10,
        total_value=1_000_000,
        award_count=2,
    )


def _match(
    *,
    company_id: int,
    tender_source: str = "federal",
    tender_id: int = 1,
    score: int = 80,
):
    row = MagicMock()
    row.company_id = company_id
    row.tender_source = tender_source
    row.tender_id = tender_id
    row.score = score
    row.created_at = datetime.now(timezone.utc) - timedelta(days=10)
    return row


def test_missed_opportunities_excludes_subject_matches():
    session = MagicMock()
    peers = [_peer(2, "Rival Co", 75)]
    rival_match = _match(company_id=2, tender_id=99, score=85)

    session.scalars.return_value.all.return_value = [rival_match]

    with (
        patch(
            "pipeline.competitive_intel.tender_activity.get_top_competitors_for_company",
            return_value=peers,
        ),
        patch(
            "pipeline.competitive_intel.tender_activity._subject_match_keys",
            return_value={("federal", 99)},
        ),
    ):
        result = get_missed_opportunities(session, company_id=1)

    assert result["company_id"] == 1
    assert result["items"] == []


def test_missed_opportunities_returns_competitor_tender():
    session = MagicMock()
    peers = [_peer(2, "Rival Co", 75)]
    rival_match = _match(company_id=2, tender_id=42, score=85)
    tender = MagicMock()
    tender.id = 42
    tender.title = "School Expansion"
    tender.estimated_value_numeric = 1_200_000
    tender.ai_budget_estimate = ""
    tender.estimated_value = ""

    session.scalars.return_value.all.side_effect = [[rival_match], [tender]]

    with (
        patch(
            "pipeline.competitive_intel.tender_activity.get_top_competitors_for_company",
            return_value=peers,
        ),
        patch(
            "pipeline.competitive_intel.tender_activity._subject_match_keys",
            return_value=set(),
        ),
    ):
        result = get_missed_opportunities(session, company_id=1)

    assert len(result["items"]) == 1
    item = result["items"][0]
    assert item["title"] == "School Expansion"
    assert item["competitor_name"] == "Rival Co"
    assert item["competitor_threat_score"] == 75
    assert item["tender_value"] == 1_200_000


def test_competitor_activity_sorted_by_match_count():
    session = MagicMock()
    peers = [_peer(2, "Alpha", 60), _peer(3, "Beta", 55)]
    alpha_matches = [_match(company_id=2, tender_id=1), _match(company_id=2, tender_id=2)]
    beta_matches = [_match(company_id=3, tender_id=3)]
    tender_one = MagicMock()
    tender_one.id = 1
    tender_one.title = "T1"
    tender_one.estimated_value_numeric = 100_000
    tender_one.ai_budget_estimate = ""
    tender_one.estimated_value = ""
    tender_three = MagicMock()
    tender_three.id = 3
    tender_three.title = "T3"
    tender_three.estimated_value_numeric = 50_000
    tender_three.ai_budget_estimate = ""
    tender_three.estimated_value = ""

    session.scalars.return_value.all.side_effect = [
        alpha_matches,
        [tender_one],
        beta_matches,
        [tender_three],
    ]

    with patch(
        "pipeline.competitive_intel.tender_activity.get_top_competitors_for_company",
        return_value=peers,
    ):
        result = get_competitor_tender_activity(session, company_id=1)

    competitors = result["competitors"]
    assert len(competitors) == 2
    assert competitors[0]["name"] == "Alpha"
    assert competitors[0]["match_count"] == 2
    assert competitors[1]["name"] == "Beta"
    assert competitors[1]["match_count"] == 1
