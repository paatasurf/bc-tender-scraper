"""Phase X.1.3 P0 — Dashboard section helper parity with morning brief."""

from __future__ import annotations

from intelligence.canonical_brief import opportunity_labels_by_disposition
from intelligence.morning_brief import sections_from_executive_brief

from tests.unit.test_canonical_integration_x12 import FIXTURE_BRIEF


def test_fixture_has_company_health_for_p0_panels() -> None:
    assert FIXTURE_BRIEF["company_health"]["summary"]


def test_disposition_buckets_match_morning_brief_pursue_prepare() -> None:
    buckets = opportunity_labels_by_disposition(FIXTURE_BRIEF)
    sections = sections_from_executive_brief(FIXTURE_BRIEF)
    assert buckets["pursue"] == ["Surrey Civic Centre Renovation"]
    assert buckets["prepare"] == ["Burnaby RFP — Mechanical"]
    assert any("Surrey Civic" in line for line in sections["pursue_now"])
    assert any("Burnaby" in line for line in sections["prepare_next"])


def test_top_risk_fallback_to_business_risks_when_top_risks_empty() -> None:
    brief = {
        **FIXTURE_BRIEF,
        "top_risks": {
            "section_title": "Top Risks",
            "items": [
                {
                    "decision_id": "dec-risk-1",
                    "rank": 1,
                    "label": "Labour shortage in Q3",
                    "entity_type": "risk",
                    "entity_id": None,
                    "disposition": "monitor",
                    "composite_score": 0.6,
                    "summary": "Capacity constraint",
                }
            ],
            "items_ignored": [],
            "confidence": "medium",
        },
    }
    sections = sections_from_executive_brief(brief)
    assert any("Labour shortage" in line for line in sections["biggest_risk"])
