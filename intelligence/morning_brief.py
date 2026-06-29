"""
intelligence/morning_brief.py
─────────────────────────────
Generate and send TenderScope morning brief emails via the voice agent + Resend.
"""
from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import requests

from config.env import get_env
from db.connection import get_session
from db.models import Company
from intelligence.resend import send_email

logger = logging.getLogger(__name__)
VANCOUVER_TZ = ZoneInfo("America/Vancouver")
DEFAULT_VOICE_AGENT_URL = "https://voice-n8n-agent-production.up.railway.app"


@dataclass(frozen=True)
class BriefSectionSpec:
    key: str
    label: str
    color: str
    italic_header: bool = False


_BRIEF_SECTIONS: tuple[BriefSectionSpec, ...] = (
    BriefSectionSpec("executive_summary", "EXECUTIVE SUMMARY", "#737373"),
    BriefSectionSpec("pursue_now", "PURSUE NOW", "#22c55e"),
    BriefSectionSpec("prepare_next", "PREPARE NEXT", "#eab308"),
    BriefSectionSpec("monitor", "MONITOR", "#3b82f6"),
    BriefSectionSpec("ignore", "IGNORE", "#737373"),
    BriefSectionSpec("top_competitor", "TOP COMPETITOR", "#ef4444"),
    BriefSectionSpec("top_permit_pipeline", "TOP PERMIT PIPELINE", "#f97316"),
    BriefSectionSpec("biggest_risk", "BIGGEST RISK", "#ef4444"),
    BriefSectionSpec("ceo_action_plan", "CEO ACTION PLAN", "#f59e0b"),
    BriefSectionSpec("why", "WHY", "#737373", italic_header=True),
)

_SECTION_LABELS = {spec.label.upper(): spec for spec in _BRIEF_SECTIONS}


def _voice_agent_url() -> str:
    return get_env("VOICE_AGENT_URL", DEFAULT_VOICE_AGENT_URL).rstrip("/")


def _build_agent_message(company_name: str, company_id: int) -> str:
    return (
        f"Generate morning brief for {company_name} (company_id={company_id}). "
        f"You MUST structure your response with these exact sections in this order:\n\n"
        f"EXECUTIVE SUMMARY: 2-3 sentences on company position today.\n\n"
        f"PURSUE NOW: Top 2-3 opportunities to bid immediately. For each: name, deadline, budget, score, one reason why.\n\n"
        f"PREPARE NEXT: 1-2 opportunities coming soon that need preparation now.\n\n"
        f"MONITOR: 2-3 tenders or signals to watch but not act on yet.\n\n"
        f"IGNORE: 1-2 opportunities to skip and why.\n\n"
        f"TOP COMPETITOR: The single biggest threat this week with threat score and what they're winning.\n\n"
        f"TOP PERMIT PIPELINE: The most promising early signal that may become a tender in 30-90 days.\n\n"
        f"BIGGEST RISK: One strategic risk for the company this month.\n\n"
        f"CEO ACTION PLAN: Exactly 3 actions the CEO should take today, numbered 1-2-3.\n\n"
        f"WHY: One paragraph explaining the data sources and confidence level behind this brief."
    )


def _fetch_morning_brief(company_id: int, company_name: str) -> dict[str, Any]:
    """Call voice-n8n-agent /api/chat for the morning brief narrative."""
    url = f"{_voice_agent_url()}/api/chat"
    payload = {
        "message": _build_agent_message(company_name, company_id),
        "session_id": f"morning-brief-{company_id}",
        "context": {"company_id": company_id},
    }
    try:
        resp = requests.post(url, json=payload, timeout=300)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        detail = ""
        if getattr(exc, "response", None) is not None:
            detail = exc.response.text[:300]
        logger.warning(
            "[MorningBrief] Voice agent request failed company_id=%s: %s %s",
            company_id,
            exc,
            detail,
        )
        raise RuntimeError(f"Voice agent request failed: {exc}") from exc

    response_text = str(data.get("response") or "").strip()
    if not response_text:
        raise RuntimeError("Voice agent returned an empty morning brief")

    return {
        "response": response_text,
        "investigation_id": data.get("investigation_id"),
        "playbook_id": data.get("playbook_id"),
        "executive_decision_brief": data.get("executive_decision_brief"),
    }


def _match_section_header(line: str) -> tuple[BriefSectionSpec, str] | None:
    stripped = line.strip()
    if not stripped:
        return None

    normalized = re.sub(r"^\s*(?:#{1,3}\s*)", "", stripped)
    normalized = normalized.removeprefix("**").removesuffix("**").strip()

    for label, spec in _SECTION_LABELS.items():
        pattern = re.compile(
            rf"^{re.escape(label)}\s*:?\s*(.*)$",
            re.IGNORECASE,
        )
        match = pattern.match(normalized)
        if match:
            return spec, match.group(1).strip()
    return None


def parse_brief_sections(lines: list[str]) -> dict[str, list[str]]:
    """Split agent response lines into CEO dashboard sections by header."""
    sections: dict[str, list[str]] = {spec.key: [] for spec in _BRIEF_SECTIONS}
    current: str | None = None

    for raw_line in lines:
        line = raw_line.rstrip()
        if not line.strip():
            if current:
                sections[current].append("")
            continue

        matched = _match_section_header(line)
        if matched:
            spec, remainder = matched
            current = spec.key
            if remainder:
                sections[current].append(remainder)
            continue

        if current:
            sections[current].append(line)
        else:
            sections["executive_summary"].append(line)

    return sections


def _inline_format(text: str) -> str:
    escaped = html.escape(text)
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
    return escaped


def _content_lines_to_html(lines: list[str]) -> str:
    if not lines:
        return '<p style="color:#a3a3a3;margin:0;">No content for this section.</p>'

    blocks: list[str] = []
    list_items: list[str] = []

    def flush_list() -> None:
        nonlocal list_items
        if list_items:
            blocks.append(
                '<ul style="margin:8px 0 0;padding-left:20px;color:#fafafa;">'
                + "".join(list_items)
                + "</ul>"
            )
            list_items = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            flush_list()
            blocks.append('<p style="margin:0;height:8px;"></p>')
            continue

        bullet_match = re.match(r"^[-*•]\s+(.*)$", stripped)
        numbered_match = re.match(r"^\d+[.)]\s+(.*)$", stripped)
        if bullet_match or numbered_match:
            content = bullet_match.group(1) if bullet_match else numbered_match.group(1)
            list_items.append(
                f'<li style="margin-bottom:6px;">{_inline_format(content)}</li>'
            )
        else:
            flush_list()
            blocks.append(
                f'<p style="margin:8px 0 0;line-height:1.6;color:#fafafa;">'
                f"{_inline_format(stripped)}</p>"
            )

    flush_list()
    return "".join(blocks) if blocks else '<p style="color:#a3a3a3;margin:0;">—</p>'


def _render_section_box(spec: BriefSectionSpec, lines: list[str]) -> str:
    content = _content_lines_to_html(lines)
    italic = "italic" if spec.italic_header else "normal"
    return f"""
  <div style="margin-bottom:20px;">
    <h2 style="font-size:12px;letter-spacing:0.08em;text-transform:uppercase;
               color:{spec.color};margin:0 0 10px;font-weight:600;font-style:{italic};">
      {spec.label}
    </h2>
    <div style="background:#141414;border:1px solid #262626;border-radius:8px;padding:16px 18px;">
      {content}
    </div>
  </div>"""


def _lines_to_html(lines: list[str]) -> str:
    """Detect CEO dashboard section headers and render each in a styled box."""
    if not lines:
        return '<p style="color:#888;margin:0;">No brief content returned.</p>'

    sections = parse_brief_sections(lines)
    rendered: list[str] = []
    matched_any = any(sections[spec.key] for spec in _BRIEF_SECTIONS[1:])

    for spec in _BRIEF_SECTIONS:
        content_lines = sections[spec.key]
        if not content_lines:
            continue
        if not matched_any and spec.key != "executive_summary":
            continue
        rendered.append(_render_section_box(spec, content_lines))

    if rendered:
        return "".join(rendered)

    return _render_section_box(
        BriefSectionSpec("executive_summary", "EXECUTIVE SUMMARY", "#737373"),
        lines,
    )


def _company_display_name(company_id: int) -> str:
    session = get_session()
    try:
        company = session.get(Company, company_id)
        if company and company.name:
            return company.name
    finally:
        session.close()
    return f"Company #{company_id}"


def _format_ranked_items(items: list[dict[str, Any]] | None) -> list[str]:
    lines: list[str] = []
    if not items:
        return lines
    for item in items:
        label = str(item.get("label") or "").strip()
        if not label:
            continue
        summary = str(item.get("summary") or "").strip()
        rank = item.get("rank")
        prefix = f"{rank}. " if rank else "- "
        if summary:
            lines.append(f"{prefix}{label} — {summary}")
        else:
            lines.append(f"{prefix}{label}")
    return lines


def sections_from_executive_brief(
    executive_brief: dict[str, Any],
    *,
    narrative_summary: str | None = None,
) -> dict[str, list[str]]:
    """Map canonical ExecutiveDecisionBrief fields to CEO dashboard sections."""
    from intelligence.canonical_brief import opportunity_labels_by_disposition

    sections: dict[str, list[str]] = {spec.key: [] for spec in _BRIEF_SECTIONS}
    buckets = opportunity_labels_by_disposition(executive_brief)

    summary_bits: list[str] = []
    if narrative_summary:
        summary_bits.append(narrative_summary.strip())
    primary = str(executive_brief.get("primary_objective") or "").strip()
    if primary:
        summary_bits.append(primary)
    health = executive_brief.get("company_health") or {}
    health_summary = str(health.get("summary") or "").strip()
    if health_summary:
        summary_bits.append(health_summary)
    sections["executive_summary"] = summary_bits

    top = executive_brief.get("top_opportunities") or {}
    pursue_items = [
        i
        for i in (top.get("items") or [])
        if str(i.get("disposition") or "").lower() == "pursue"
    ]
    prepare_items = [
        i
        for i in (top.get("items") or [])
        if str(i.get("disposition") or "").lower() == "prepare"
    ]
    monitor_items = [
        i
        for i in (top.get("items") or [])
        if str(i.get("disposition") or "").lower() == "monitor"
    ]
    sections["pursue_now"] = _format_ranked_items(pursue_items) or [
        f"- {label}" for label in buckets.get("pursue", [])
    ]
    sections["prepare_next"] = _format_ranked_items(prepare_items) or [
        f"- {label}" for label in buckets.get("prepare", [])
    ]
    sections["monitor"] = _format_ranked_items(monitor_items) or [
        f"- {label}" for label in buckets.get("monitor", [])
    ]
    sections["ignore"] = _format_ranked_items(
        list(top.get("items_ignored") or [])
        + list(executive_brief.get("ignored_opportunities") or [])
    ) or [f"- {label}" for label in buckets.get("ignore", [])]

    comp = executive_brief.get("competitive_threats") or {}
    sections["top_competitor"] = _format_ranked_items(comp.get("items"))

    permit = executive_brief.get("permit_pipeline") or {}
    sections["top_permit_pipeline"] = _format_ranked_items(permit.get("items"))

    risks = executive_brief.get("top_risks") or {}
    risk_lines = _format_ranked_items(risks.get("items"))
    priorities = executive_brief.get("executive_priorities") or {}
    if not risk_lines:
        risk_lines = _format_ranked_items(priorities.get("business_risks"))
    sections["biggest_risk"] = risk_lines[:1]

    ceo_actions = _format_ranked_items(priorities.get("immediate_actions"))
    if not ceo_actions:
        ceo_actions = _format_ranked_items(priorities.get("ceo_decisions"))
    sections["ceo_action_plan"] = ceo_actions

    why_bits: list[str] = []
    missing = executive_brief.get("missing_information") or []
    if missing:
        why_bits.append("Missing information: " + "; ".join(str(m) for m in missing[:3]))
    why_bits.append(
        f"Overall confidence: {executive_brief.get('overall_confidence', 'unknown')}."
    )
    why_bits.append(
        f"Engine version: {executive_brief.get('engine_version', 'unknown')}."
    )
    sections["why"] = why_bits

    return sections


def render_morning_brief_html_from_executive_brief(
    *,
    company_id: int,
    company_name: str,
    executive_brief: dict[str, Any],
    narrative_summary: str | None = None,
    brief_date: datetime | None = None,
) -> str:
    """Render CEO dashboard email from canonical ExecutiveDecisionBrief."""
    when = brief_date or datetime.now(VANCOUVER_TZ)
    date_label = when.strftime("%A, %B %d, %Y")
    sections = sections_from_executive_brief(
        executive_brief,
        narrative_summary=narrative_summary,
    )
    rendered: list[str] = []
    for spec in _BRIEF_SECTIONS:
        content_lines = sections.get(spec.key) or []
        if not content_lines:
            continue
        rendered.append(_render_section_box(spec, content_lines))

    section_blocks = "".join(rendered) if rendered else _lines_to_html([])

    return f"""\
<!DOCTYPE html>
<html>
<body style="margin:0;padding:0;background:#050505;">
<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif;
            max-width:640px;margin:0 auto;padding:32px 24px;color:#e5e5e5;background:#0a0a0a;">
  <div style="border-bottom:1px solid #262626;padding-bottom:20px;margin-bottom:28px;">
    <p style="margin:0 0 6px;font-size:12px;letter-spacing:0.12em;text-transform:uppercase;color:#737373;">
      TenderScope CEO Dashboard
    </p>
    <h1 style="margin:0;font-size:24px;font-weight:600;color:#fafafa;">
      {html.escape(company_name)}
    </h1>
    <p style="margin:8px 0 0;color:#a3a3a3;font-size:14px;">{html.escape(date_label)}</p>
  </div>
{section_blocks}
  <div style="border-top:1px solid #262626;padding-top:20px;margin-top:8px;">
    <p style="margin:0;font-size:12px;color:#525252;text-align:center;">
      Powered by TenderScope · <a href="https://tenderscope.ca" style="color:#737373;text-decoration:none;">tenderscope.ca</a>
    </p>
  </div>
</div>
</body>
</html>"""


def render_morning_brief_html(
    *,
    company_id: int,
    company_name: str,
    brief_text: str,
    brief_date: datetime | None = None,
    executive_brief: dict[str, Any] | None = None,
) -> str:
    """Render a dark-themed CEO dashboard email body for the morning brief."""
    if executive_brief:
        narrative = brief_text.splitlines()[0].strip() if brief_text else None
        return render_morning_brief_html_from_executive_brief(
            company_id=company_id,
            company_name=company_name,
            executive_brief=executive_brief,
            narrative_summary=narrative,
            brief_date=brief_date,
        )

    when = brief_date or datetime.now(VANCOUVER_TZ)
    date_label = when.strftime("%A, %B %d, %Y")
    section_blocks = _lines_to_html(brief_text.splitlines())

    return f"""\
<!DOCTYPE html>
<html>
<body style="margin:0;padding:0;background:#050505;">
<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif;
            max-width:640px;margin:0 auto;padding:32px 24px;color:#e5e5e5;background:#0a0a0a;">
  <div style="border-bottom:1px solid #262626;padding-bottom:20px;margin-bottom:28px;">
    <p style="margin:0 0 6px;font-size:12px;letter-spacing:0.12em;text-transform:uppercase;color:#737373;">
      TenderScope CEO Dashboard
    </p>
    <h1 style="margin:0;font-size:24px;font-weight:600;color:#fafafa;">
      {html.escape(company_name)}
    </h1>
    <p style="margin:8px 0 0;color:#a3a3a3;font-size:14px;">{html.escape(date_label)}</p>
  </div>
{section_blocks}
  <div style="border-top:1px solid #262626;padding-top:20px;margin-top:8px;">
    <p style="margin:0;font-size:12px;color:#525252;text-align:center;">
      Powered by TenderScope · <a href="https://tenderscope.ca" style="color:#737373;text-decoration:none;">tenderscope.ca</a>
    </p>
  </div>
</div>
</body>
</html>"""


def send_morning_brief(*, company_id: int, email: str) -> dict[str, Any]:
    """Generate morning brief via voice agent, format HTML, send via Resend."""
    company_name = _company_display_name(company_id)
    agent_result = _fetch_morning_brief(company_id, company_name)
    brief_date = datetime.now(VANCOUVER_TZ)
    subject = f"TenderScope Morning Brief — {brief_date.strftime('%B %d, %Y')}"
    executive_brief = agent_result.get("executive_decision_brief")
    html_body = render_morning_brief_html(
        company_id=company_id,
        company_name=company_name,
        brief_text=agent_result["response"],
        brief_date=brief_date,
        executive_brief=executive_brief if isinstance(executive_brief, dict) else None,
    )

    resend_result = send_email(to=email, subject=subject, html=html_body)
    logger.info(
        "[MorningBrief] Sent company_id=%s email=%s resend_id=%s",
        company_id,
        email,
        resend_result.get("id"),
    )

    return {
        "success": True,
        "email": email,
        "company_id": company_id,
        "company_name": company_name,
        "subject": subject,
        "resend_id": resend_result.get("id"),
        "investigation_id": agent_result.get("investigation_id"),
        "playbook_id": agent_result.get("playbook_id"),
        "used_executive_brief": bool(executive_brief),
    }
