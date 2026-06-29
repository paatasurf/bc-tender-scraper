"""Unit tests for morning brief HTML parsing and formatting."""

from intelligence.morning_brief import parse_brief_sections, render_morning_brief_html


def test_parse_brief_sections_splits_headers() -> None:
    text = """\
PURSUE NOW
- Bid on Surrey civic centre package
- Follow up on Burnaby RFP

MONITOR
- Vancouver school board tender closing Friday

COMPETITOR ALERTS
- Acme Construction won 2 awards this week

EARLY SIGNALS
- Rezoning application at Main & 12th
"""
    sections = parse_brief_sections(text)
    assert any("Surrey" in line for line in sections["pursue"])
    assert any("Vancouver school" in line for line in sections["monitor"])
    assert any("Acme" in line for line in sections["competitor_alerts"])
    assert any("Rezoning" in line for line in sections["early_signals"])


def test_render_morning_brief_html_includes_sections_and_footer() -> None:
    html_body = render_morning_brief_html(
        company_id=8638,
        company_name="Pontem Group",
        brief_text="PURSUE NOW\n- Test opportunity",
    )
    assert "PURSUE NOW" in html_body
    assert "MONITOR" in html_body
    assert "COMPETITOR ALERTS" in html_body
    assert "EARLY SIGNALS" in html_body
    assert "Pontem Group" in html_body
    assert "Powered by TenderScope" in html_body
    assert "tenderscope.ca" in html_body
