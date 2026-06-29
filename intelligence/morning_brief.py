"""
intelligence/morning_brief.py
─────────────────────────────
Generate and send TenderScope morning brief emails via the voice agent + Resend.
"""
from __future__ import annotations

import html
import logging
import re
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import requests

from config.env import get_env
from db.connection import get_session
from db.models import Company
from intelligence.resend import send_email

logger = logging.getLogger(__name__)

DEFAULT_VOICE_AGENT_URL = "https://voice-n8n-agent-production.up.railway.app"
VANCOUVER_TZ = ZoneInfo("America/Vancouver")

_SECTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "pursue",
        re.compile(
            r"^\s*(?:#{1,3}\s*)?(?:\*\*)?\s*PURSUE(?:\s+NOW)?(?:\*\*)?\s*:?\s*$",
            re.IGNORECASE,
        ),
    ),
    (
        "monitor",
        re.compile(
            r"^\s*(?:#{1,3}\s*)?(?:\*\*)?\s*MONITOR(?:\*\*)?\s*:?\s*$",
            re.IGNORECASE,
        ),
    ),
    (
        "competitor_alerts",
        re.compile(
            r"^\s*(?:#{1,3}\s*)?(?:\*\*)?\s*(?:COMPETITOR(?:\s+ALERTS?)?|COMPETITIVE\s+THREATS?)(?:\*\*)?\s*:?\s*$",
            re.IGNORECASE,
        ),
    ),
    (
        "early_signals",
        re.compile(
            r"^\s*(?:#{1,3}\s*)?(?:\*\*)?\s*EARLY\s+SIGNALS?(?:\*\*)?\s*:?\s*$",
            re.IGNORECASE,
        ),
    ),
)

_SECTION_LABELS = {
    "pursue": "PURSUE NOW",
    "monitor": "MONITOR",
    "competitor_alerts": "COMPETITOR ALERTS",
    "early_signals": "EARLY SIGNALS",
}

_SECTION_COLORS = {
    "pursue": "#22c55e",
    "monitor": "#f59e0b",
    "competitor_alerts": "#ef4444",
    "early_signals": "#3b82f6",
}


def _voice_agent_url() -> str:
    return get_env("VOICE_AGENT_URL", DEFAULT_VOICE_AGENT_URL).rstrip("/")


def _fetch_morning_brief(company_id: int) -> dict[str, Any]:
    """Call voice-n8n-agent /api/chat for the morning brief narrative."""
    url = f"{_voice_agent_url()}/api/chat"
    payload = {
        "message": f"Generate morning brief for company {company_id}",
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
    }


def _match_section(line: str) -> str | None:
    for key, pattern in _SECTION_PATTERNS:
        if pattern.match(line.strip()):
            return key
    return None


def parse_brief_sections(text: str) -> dict[str, list[str]]:
    """Split agent response into morning-brief sections by header lines."""
    sections: dict[str, list[str]] = {key: [] for key, _ in _SECTION_PATTERNS}
    current: str | None = None

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            if current and sections[current]:
                sections[current].append("")
            continue

        section_key = _match_section(line)
        if section_key:
            current = section_key
            continue

        if current:
            sections[current].append(line)
        else:
            sections["pursue"].append(line)

    return sections


def _lines_to_html(lines: list[str]) -> str:
    if not lines:
        return '<p style="color:#888;margin:0;">Nothing flagged in this category today.</p>'

    blocks: list[str] = []
    list_items: list[str] = []

    def flush_list() -> None:
        nonlocal list_items
        if list_items:
            blocks.append(
                '<ul style="margin:8px 0 0;padding-left:20px;">'
                + "".join(list_items)
                + "</ul>"
            )
            list_items = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            flush_list()
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
                f'<p style="margin:8px 0 0;line-height:1.5;">{_inline_format(stripped)}</p>'
            )

    flush_list()
    return "".join(blocks) if blocks else '<p style="color:#888;margin:0;">—</p>'


def _inline_format(text: str) -> str:
    escaped = html.escape(text)
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
    return escaped


def _company_display_name(company_id: int) -> str:
    session = get_session()
    try:
        company = session.get(Company, company_id)
        if company and company.name:
            return company.name
    finally:
        session.close()
    return f"Company #{company_id}"


def render_morning_brief_html(
    *,
    company_id: int,
    company_name: str,
    brief_text: str,
    brief_date: datetime | None = None,
) -> str:
    """Render a dark-themed HTML email body for the morning brief."""
    when = brief_date or datetime.now(VANCOUVER_TZ)
    date_label = when.strftime("%A, %B %d, %Y")
    sections = parse_brief_sections(brief_text)

    section_blocks: list[str] = []
    for key, _ in _SECTION_PATTERNS:
        label = _SECTION_LABELS[key]
        accent = _SECTION_COLORS[key]
        body = _lines_to_html(sections[key])
        section_blocks.append(
            f"""
  <div style="margin-bottom:28px;">
    <h2 style="font-size:13px;letter-spacing:0.08em;text-transform:uppercase;
               color:{accent};margin:0 0 10px;font-weight:600;">
      {label}
    </h2>
    <div style="background:#141414;border:1px solid #262626;border-radius:8px;padding:16px 18px;">
      {body}
    </div>
  </div>"""
        )

    return f"""\
<!DOCTYPE html>
<html>
<body style="margin:0;padding:0;background:#050505;">
<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif;
            max-width:640px;margin:0 auto;padding:32px 24px;color:#e5e5e5;background:#0a0a0a;">
  <div style="border-bottom:1px solid #262626;padding-bottom:20px;margin-bottom:28px;">
    <p style="margin:0 0 6px;font-size:12px;letter-spacing:0.12em;text-transform:uppercase;color:#737373;">
      TenderScope Morning Brief
    </p>
    <h1 style="margin:0;font-size:24px;font-weight:600;color:#fafafa;">
      {html.escape(company_name)}
    </h1>
    <p style="margin:8px 0 0;color:#a3a3a3;font-size:14px;">{html.escape(date_label)}</p>
  </div>
{"".join(section_blocks)}
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
    agent_result = _fetch_morning_brief(company_id)
    company_name = _company_display_name(company_id)
    brief_date = datetime.now(VANCOUVER_TZ)
    subject = f"TenderScope Morning Brief — {brief_date.strftime('%B %d, %Y')}"
    html_body = render_morning_brief_html(
        company_id=company_id,
        company_name=company_name,
        brief_text=agent_result["response"],
        brief_date=brief_date,
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
    }
