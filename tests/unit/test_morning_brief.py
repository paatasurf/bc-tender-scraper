"""Unit tests for morning brief CEO dashboard HTML formatting."""

from intelligence.morning_brief import parse_brief_sections, render_morning_brief_html


def test_parse_brief_sections_splits_ceo_headers() -> None:
    text = """\
EXECUTIVE SUMMARY: Pontem is well positioned in Vancouver civic work.

PURSUE NOW:
- Surrey civic centre — deadline Apr 15, $12M, score 82

TOP COMPETITOR:
Kevin To threat score 76 — winning school board packages.

CEO ACTION PLAN:
1. Call Surrey project manager
2. Review Burnaby RFP draft
3. Brief estimating team
"""
    sections = parse_brief_sections(text.splitlines())
    assert any("Pontem" in line for line in sections["executive_summary"])
    assert any("Surrey civic" in line for line in sections["pursue_now"])
    assert any("Kevin To" in line for line in sections["top_competitor"])
    assert any("Call Surrey" in line for line in sections["ceo_action_plan"])


def test_render_morning_brief_html_renders_styled_section_boxes() -> None:
    html_body = render_morning_brief_html(
        company_id=8638,
        company_name="Pontem Group",
        brief_text=(
            "EXECUTIVE SUMMARY: Strong pipeline in Lower Mainland.\n\n"
            "PURSUE NOW:\n"
            "- Surrey civic centre package\n\n"
            "TOP COMPETITOR:\n"
            "Kevin To threat score 76\n\n"
            "CEO ACTION PLAN:\n"
            "1. Review Surrey bid\n"
        ),
    )
    assert "CEO Dashboard" in html_body or "CEO ACTION PLAN" in html_body
    assert "EXECUTIVE SUMMARY" in html_body
    assert "PURSUE NOW" in html_body
    assert "TOP COMPETITOR" in html_body
    assert "Kevin To threat score 76" in html_body
    assert "Surrey civic centre" in html_body
    assert "Pontem Group" in html_body
    assert "#22c55e" in html_body
    assert "#ef4444" in html_body
    assert "Powered by TenderScope" in html_body


def test_render_morning_brief_html_fallback_without_headers() -> None:
    html_body = render_morning_brief_html(
        company_id=8638,
        company_name="Pontem Group",
        brief_text="Unstructured agent output with no section headers.",
    )
    assert "Unstructured agent output" in html_body
    assert "EXECUTIVE SUMMARY" in html_body
