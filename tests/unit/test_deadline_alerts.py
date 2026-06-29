"""Unit tests for deadline alert helpers."""

from __future__ import annotations

from datetime import date, timedelta

from intelligence.deadline_alerts import (
    ALERT_DAYS,
    DeadlineAlert,
    _days_until_close,
    render_deadline_alert_html,
)


def test_days_until_close_matches_alert_windows() -> None:
    target = date.today() + timedelta(days=3)
    assert _days_until_close(target.isoformat()) == 3
    assert 3 in ALERT_DAYS


def test_render_deadline_alert_html_includes_cta_and_metadata() -> None:
    alert = DeadlineAlert(
        profile_id=1,
        email="client@example.com",
        company_id=8638,
        company_name="Pontem Group",
        tender_source="federal",
        tender_id=42,
        title="Surrey Civic Centre Renovation",
        deadline="2026-07-01",
        days_left=3,
        budget_label="$12M · est. $11.5M",
        match_score=82,
        tender_url="https://example.com/tender/42",
    )
    html_body = render_deadline_alert_html(alert)
    assert "Surrey Civic Centre Renovation" in html_body
    assert "Match score" in html_body
    assert "82" in html_body
    assert "Submit your proposal now" in html_body
    assert "https://example.com/tender/42" in html_body
    assert "tenderscope.ca" in html_body
