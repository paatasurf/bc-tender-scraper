"""
intelligence/deadline_alerts.py
───────────────────────────────
Email alerts for matched tenders closing in 1, 3, or 7 days.
"""
from __future__ import annotations

import html
import logging
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.connection import get_session
from db.models import ClientProfile, TenderMatch
from intelligence.resend import send_email
from pipeline.ai_matching import _load_tender_row
from pipeline.opportunity_discovery import _is_tender_open, _parse_date

logger = logging.getLogger(__name__)

ALERT_DAYS = frozenset({1, 3, 7})
DEFAULT_MIN_MATCH_SCORE = 50


@dataclass(frozen=True)
class DeadlineAlert:
    profile_id: int
    email: str
    company_id: int
    company_name: str
    tender_source: str
    tender_id: int
    title: str
    deadline: str
    days_left: int
    budget_label: str
    match_score: int
    tender_url: str


def _days_until_close(deadline: str) -> int | None:
    parsed = _parse_date(deadline)
    if parsed is None:
        return None
    return (parsed - date.today()).days


def _budget_label(tender: Any) -> str:
    primary = (
        getattr(tender, "estimated_value", None)
        or getattr(tender, "value", None)
        or ""
    )
    secondary = getattr(tender, "ai_budget_estimate", None) or ""
    primary_text = str(primary).strip()
    secondary_text = str(secondary).strip()
    if primary_text and secondary_text and secondary_text != primary_text:
        return f"{primary_text} · est. {secondary_text}"
    return primary_text or secondary_text or "—"


def _collect_profile_alerts(
    session: Session,
    profile: ClientProfile,
    *,
    min_match_score: int = DEFAULT_MIN_MATCH_SCORE,
) -> list[DeadlineAlert]:
    rows = session.scalars(
        select(TenderMatch)
        .where(
            TenderMatch.company_kind == "construction",
            TenderMatch.company_id == profile.company_id,
            TenderMatch.score >= min_match_score,
        )
        .order_by(TenderMatch.score.desc(), TenderMatch.id.desc())
    ).all()

    alerts: list[DeadlineAlert] = []
    seen: set[tuple[str, int, int]] = set()

    for row in rows:
        tender = _load_tender_row(session, row.tender_source, row.tender_id)
        if tender is None:
            continue

        deadline = (
            getattr(tender, "closing_date", None)
            or getattr(tender, "deadline", None)
            or ""
        )
        deadline = str(deadline).strip()
        if not deadline or not _is_tender_open(deadline):
            continue

        days_left = _days_until_close(deadline)
        if days_left is None or days_left not in ALERT_DAYS:
            continue

        dedupe_key = (row.tender_source, row.tender_id, days_left)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)

        title = str(getattr(tender, "title", "") or "Untitled tender").strip()
        tender_url = str(getattr(tender, "url", "") or "").strip()
        if not tender_url:
            continue

        alerts.append(
            DeadlineAlert(
                profile_id=profile.id,
                email=profile.email,
                company_id=profile.company_id,
                company_name=profile.company_name,
                tender_source=row.tender_source,
                tender_id=row.tender_id,
                title=title,
                deadline=deadline,
                days_left=days_left,
                budget_label=_budget_label(tender),
                match_score=int(row.score),
                tender_url=tender_url,
            )
        )

    return alerts


def render_deadline_alert_html(alert: DeadlineAlert) -> str:
    deadline_label = html.escape(alert.deadline)
    title = html.escape(alert.title)
    budget = html.escape(alert.budget_label)
    company = html.escape(alert.company_name)
    tender_url = html.escape(alert.tender_url, quote=True)

    return f"""\
<!DOCTYPE html>
<html>
<body style="margin:0;padding:0;background:#050505;">
<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif;
            max-width:640px;margin:0 auto;padding:32px 24px;color:#e5e5e5;background:#0a0a0a;">
  <p style="margin:0 0 8px;font-size:12px;letter-spacing:0.12em;text-transform:uppercase;color:#ef4444;">
    TenderScope Deadline Alert
  </p>
  <h1 style="margin:0 0 8px;font-size:22px;line-height:1.3;color:#fafafa;">{title}</h1>
  <p style="margin:0 0 20px;color:#a3a3a3;font-size:14px;">
    Closing in <strong style="color:#f59e0b;">{alert.days_left} day{"s" if alert.days_left != 1 else ""}</strong>
    · Deadline {deadline_label}
  </p>
  <div style="background:#141414;border:1px solid #262626;border-radius:8px;padding:18px 20px;margin-bottom:24px;">
    <p style="margin:0 0 10px;color:#737373;font-size:12px;text-transform:uppercase;letter-spacing:0.08em;">
      For {company}
    </p>
    <p style="margin:0 0 8px;color:#fafafa;"><strong>Budget range:</strong> {budget}</p>
    <p style="margin:0;color:#fafafa;"><strong>Match score:</strong> {alert.match_score}</p>
  </div>
  <p style="margin:0 0 16px;color:#d4d4d4;">Submit your proposal now before the window closes.</p>
  <a href="{tender_url}"
     style="display:inline-block;background:#22c55e;color:#052e16;text-decoration:none;
            font-weight:600;padding:12px 18px;border-radius:8px;">
    Submit your proposal now
  </a>
  <p style="margin:24px 0 0;font-size:12px;color:#525252;text-align:center;">
    Powered by TenderScope · <a href="https://tenderscope.ca" style="color:#737373;text-decoration:none;">tenderscope.ca</a>
  </p>
</div>
</body>
</html>"""


def send_deadline_alert(alert: DeadlineAlert) -> dict[str, Any]:
    subject = f"⚠️ Tender closing in {alert.days_left} days: {alert.title}"
    html_body = render_deadline_alert_html(alert)
    result = send_email(to=alert.email, subject=subject, html=html_body)
    return {
        "status": "sent",
        "email": alert.email,
        "company_id": alert.company_id,
        "tender_source": alert.tender_source,
        "tender_id": alert.tender_id,
        "days_left": alert.days_left,
        "title": alert.title,
        "resend_id": result.get("id"),
    }


def send_all_deadline_alerts(
    *,
    min_match_score: int = DEFAULT_MIN_MATCH_SCORE,
) -> dict[str, Any]:
    """Scan client profiles and email deadline alerts for 1/3/7-day windows."""
    session = get_session()
    sent = failed = alerts_found = 0
    results: list[dict[str, Any]] = []

    try:
        profiles = session.scalars(
            select(ClientProfile)
            .where(ClientProfile.alerts_enabled.is_(True))
            .order_by(ClientProfile.id)
        ).all()

        for profile in profiles:
            alerts = _collect_profile_alerts(
                session,
                profile,
                min_match_score=min_match_score,
            )
            alerts_found += len(alerts)

            for alert in alerts:
                try:
                    outcome = send_deadline_alert(alert)
                    results.append(outcome)
                    sent += 1
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "[DeadlineAlerts] Failed profile_id=%s tender=%s/%s: %s",
                        profile.id,
                        alert.tender_source,
                        alert.tender_id,
                        exc,
                    )
                    results.append(
                        {
                            "status": "failed",
                            "email": alert.email,
                            "company_id": alert.company_id,
                            "tender_source": alert.tender_source,
                            "tender_id": alert.tender_id,
                            "days_left": alert.days_left,
                            "title": alert.title,
                            "error": str(exc)[:300],
                        }
                    )
                    failed += 1

        return {
            "success": failed == 0,
            "profiles_scanned": len(profiles),
            "alerts_found": alerts_found,
            "sent": sent,
            "failed": failed,
            "alert_days": sorted(ALERT_DAYS),
            "results": results,
        }
    finally:
        session.close()
