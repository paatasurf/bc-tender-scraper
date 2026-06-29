"""Phase X.1.2 — Morning brief + canonical brief parity tests."""

from __future__ import annotations

from intelligence.canonical_brief import disposition_for_entity, opportunity_labels_by_disposition
from intelligence.morning_brief import (
    render_morning_brief_html_from_executive_brief,
    sections_from_executive_brief,
)


FIXTURE_BRIEF = {
    "brief_id": "edb-test",
    "investigation_id": "inv-test",
    "engine_version": "x1.1",
    "primary_objective": "Prioritize Surrey civic package",
    "overall_confidence": "medium",
    "company_health": {"summary": "Strong Lower Mainland pipeline"},
    "top_opportunities": {
        "section_title": "Top Opportunities",
        "items": [
            {
                "decision_id": "dec-1",
                "rank": 1,
                "label": "Surrey Civic Centre Renovation",
                "entity_type": "tender",
                "entity_id": 42,
                "disposition": "pursue",
                "composite_score": 0.82,
                "summary": "High fit; deadline approaching",
            },
            {
                "decision_id": "dec-2",
                "rank": 2,
                "label": "Burnaby RFP — Mechanical",
                "entity_type": "tender",
                "entity_id": 99,
                "disposition": "prepare",
                "composite_score": 0.71,
                "summary": "Prepare estimating package",
            },
        ],
        "items_ignored": [],
        "confidence": "medium",
    },
    "competitive_threats": {
        "section_title": "Competitive Threats",
        "items": [
            {
                "decision_id": "dec-comp-1",
                "rank": 1,
                "label": "Kevin To Construction",
                "entity_type": "competitor",
                "entity_id": 1001,
                "disposition": "monitor",
                "composite_score": 0.76,
                "summary": "Threat score 76",
            }
        ],
        "items_ignored": [],
        "confidence": "medium",
    },
    "permit_pipeline": None,
    "top_risks": None,
    "executive_priorities": {
        "immediate_actions": [
            {
                "decision_id": "dec-action-1",
                "rank": 1,
                "label": "Call Surrey project manager",
                "entity_type": "action",
                "entity_id": None,
                "disposition": "pursue",
                "composite_score": 0.9,
                "summary": "",
            }
        ],
        "medium_term_actions": [],
        "strategic_investments": [],
        "business_risks": [],
        "missed_opportunities": [],
        "ceo_decisions": [],
    },
    "ignored_opportunities": [],
}


def test_sections_from_executive_brief_uses_dispositions_not_regex() -> None:
    sections = sections_from_executive_brief(FIXTURE_BRIEF)
    assert any("Surrey Civic" in line for line in sections["pursue_now"])
    assert any("Burnaby" in line for line in sections["prepare_next"])
    assert any("Kevin To" in line for line in sections["top_competitor"])
    assert any("Call Surrey" in line for line in sections["ceo_action_plan"])


def test_morning_brief_html_from_executive_brief_includes_pursue_section() -> None:
    html_body = render_morning_brief_html_from_executive_brief(
        company_id=8638,
        company_name="Pontem Group",
        executive_brief=FIXTURE_BRIEF,
    )
    assert "PURSUE NOW" in html_body
    assert "Surrey Civic Centre Renovation" in html_body
    assert "Kevin To Construction" in html_body
    assert "#22c55e" in html_body


def test_opportunity_ordering_matches_agent_disposition_buckets() -> None:
    buckets = opportunity_labels_by_disposition(FIXTURE_BRIEF)
    assert buckets["pursue"] == ["Surrey Civic Centre Renovation"]
    assert buckets["prepare"] == ["Burnaby RFP — Mechanical"]


def test_disposition_for_entity_from_canonical_brief() -> None:
    disp = disposition_for_entity(FIXTURE_BRIEF, entity_type="tender", entity_id=42)
    assert disp == "pursue"
