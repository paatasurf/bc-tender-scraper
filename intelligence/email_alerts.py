"""
intelligence/email_alerts.py
────────────────────────────
Generate and send personalized email digests for TenderScope clients.

Each digest combines:
  - company_wiki profile (or company DB fallback)
  - new tenders matching client regions / specializations / value band
  - competitor activity from competitive intelligence
"""
from __future__ import annotations

import html
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from db.connection import get_session
from db.models import ClientProfile, Company, CompanyWiki, Tender
from intelligence.resend import send_email
from intelligence.telegram import send_telegram_message
from pipeline.competitive_intel.service import get_competitive_intelligence
from pipeline.early_signals import get_early_signals_for_profile

logger = logging.getLogger(__name__)

TENDER_LOOKBACK_DAYS = 7
MAX_TENDERS = 8
MAX_COMPETITORS = 5
MAX_EARLY_SIGNALS = 8
EARLY_SIGNAL_LOOKBACK_DAYS = 7


def _wiki_for_company(session: Session, company_id: int) -> CompanyWiki | None:
    return session.scalar(
        select(CompanyWiki)
        .where(CompanyWiki.company_id == company_id)
        .where(CompanyWiki.company_kind == "construction")
    )


def _tender_matches_profile(tender: Tender, profile: ClientProfile) -> bool:
    if profile.regions:
        location = (tender.location or "").lower()
        if not any(region.lower() in location for region in profile.regions):
            return False

    if profile.specializations:
        blob = f"{tender.title} {tender.category}".lower()
        if not any(spec.lower() in blob for spec in profile.specializations):
            return False

    value = tender.estimated_value_numeric
    if value is not None:
        if profile.min_project_value is not None and value < profile.min_project_value:
            return False
        if profile.max_project_value is not None and value > profile.max_project_value:
            return False

    return True


def _fetch_matching_tenders(session: Session, profile: ClientProfile) -> list[Tender]:
    since = datetime.now(timezone.utc) - timedelta(days=TENDER_LOOKBACK_DAYS)
    query = (
        select(Tender)
        .where(Tender.scraped_at >= since)
        .order_by(Tender.scraped_at.desc())
        .limit(100)
    )

    if profile.regions:
        region_filters = [
            Tender.location.ilike(f"%{region}%") for region in profile.regions if region
        ]
        if region_filters:
            query = query.where(or_(*region_filters))

    rows = session.scalars(query).all()
    matched = [row for row in rows if _tender_matches_profile(row, profile)]
    return matched[:MAX_TENDERS]


def _fetch_competitor_activity(session: Session, company_id: int) -> list[dict[str, Any]]:
    try:
        ci = get_competitive_intelligence(session, company_id=company_id, kind="construction")
    except Exception as exc:  # noqa: BLE001
        logger.warning("[EmailAlerts] competitive intel failed for company_id=%s: %s", company_id, exc)
        return []

    peers = ci.get("top_competitors") if isinstance(ci, dict) else None
    if not isinstance(peers, list):
        return []
    return peers[:MAX_COMPETITORS]



def _company_summary_block(session: Session, profile: ClientProfile) -> str:
    wiki = _wiki_for_company(session, profile.company_id)
    if wiki and wiki.summary:
        return wiki.summary

    company = session.get(Company, profile.company_id)
    if company and company.ai_summary:
        return company.ai_summary

    name = profile.company_name or (company.name if company else "your company")
    return f"Market intelligence digest for {name}."


def _render_tenders_section(tenders: list[Tender]) -> str:
    if not tenders:
        return "<p><em>No new tenders matched your filters this week.</em></p>"

    items: list[str] = []
    for tender in tenders:
        value = tender.estimated_value or tender.ai_budget_estimate or "—"
        items.append(
            "<li>"
            f"<strong>{html.escape(tender.title)}</strong><br>"
            f"{html.escape(tender.location or 'BC')} · "
            f"Closes {html.escape(tender.closing_date or 'TBD')} · "
            f"Value {html.escape(str(value))}"
            f' · <a href="{html.escape(tender.url)}">View tender</a>'
            "</li>"
        )
    return "<ul>" + "".join(items) + "</ul>"


def _render_competitors_section(competitors: list[dict[str, Any]]) -> str:
    if not competitors:
        return "<p><em>Competitor activity data is limited for your market segment.</em></p>"

    items: list[str] = []
    for peer in competitors:
        name = html.escape(str(peer.get("name") or "Unknown"))
        threat = peer.get("threat_score")
        projects = peer.get("total_projects", 0)
        awards = peer.get("award_count", 0)
        threat_label = f" · Threat score {threat}" if threat is not None else ""
        items.append(
            f"<li><strong>{name}</strong> — "
            f"{projects} projects · {awards} awards{threat_label}</li>"
        )
    return "<ul>" + "".join(items) + "</ul>"


def _render_early_signals_section(signals: list[dict[str, Any]]) -> str:
    if not signals:
        return "<p><em>No new permit applications matched your regions this week.</em></p>"

    items: list[str] = []
    for signal in signals[:MAX_EARLY_SIGNALS]:
        area = signal.get("local_area") or signal.get("city") or "Vancouver"
        value = signal.get("estimated_value")
        value_label = f"${value:,.0f}" if isinstance(value, (int, float)) and value else "—"
        applied = html.escape(str(signal.get("application_date") or "—"))
        issued = html.escape(str(signal.get("issue_date") or "—"))
        contractor = html.escape(str(signal.get("contractor") or ""))
        permit_type = html.escape(str(signal.get("permit_type") or "Permit"))
        lag = signal.get("pipeline_lag_days")
        lag_label = f" · {lag}d application lead" if lag else ""
        contractor_line = f"<br>Contractor: {contractor}" if contractor else ""
        items.append(
            "<li>"
            f"<strong>{permit_type}</strong> · {html.escape(str(area))}{lag_label}<br>"
            f"Applied {applied} · Issued {issued} · Value {value_label}"
            f"{contractor_line}"
            "</li>"
        )
    return "<ul>" + "".join(items) + "</ul>"


def generate_digest(session: Session, profile: ClientProfile) -> dict[str, str]:
    """Build subject + HTML body for one client profile."""
    company_name = profile.company_name or f"Company #{profile.company_id}"
    tenders = _fetch_matching_tenders(session, profile)
    early_signals = get_early_signals_for_profile(
        session,
        profile,
        lookback_days=EARLY_SIGNAL_LOOKBACK_DAYS,
        limit=MAX_EARLY_SIGNALS,
    )
    competitors = _fetch_competitor_activity(session, profile.company_id)
    summary = _company_summary_block(session, profile)
    regions = ", ".join(profile.regions) if profile.regions else "British Columbia"
    today = datetime.now(timezone.utc).strftime("%B %d, %Y")

    subject = f"TenderScope Weekly Digest — {company_name}"
    body_html = f"""\
<div style="font-family: Arial, sans-serif; max-width: 640px; color: #1a1a1a;">
  <h1 style="font-size: 22px; margin-bottom: 4px;">TenderScope Market Digest</h1>
  <p style="color: #666; margin-top: 0;">{html.escape(today)} · {html.escape(company_name)}</p>

  <h2 style="font-size: 16px; border-bottom: 1px solid #e5e5e5; padding-bottom: 4px;">
    Your Market Position
  </h2>
  <p>{html.escape(summary)}</p>

  <h2 style="font-size: 16px; border-bottom: 1px solid #e5e5e5; padding-bottom: 4px;">
    Early Permit Signals ({html.escape(regions)})
  </h2>
  {_render_early_signals_section(early_signals)}

  <h2 style="font-size: 16px; border-bottom: 1px solid #e5e5e5; padding-bottom: 4px;">
    New Tenders ({html.escape(regions)})
  </h2>
  {_render_tenders_section(tenders)}

  <h2 style="font-size: 16px; border-bottom: 1px solid #e5e5e5; padding-bottom: 4px;">
    Competitor Activity
  </h2>
  {_render_competitors_section(competitors)}

  <p style="color: #888; font-size: 12px; margin-top: 32px;">
    You are receiving this because alerts are enabled on your TenderScope profile.
    Regions: {html.escape(regions)}.
  </p>
</div>
"""
    return {
        "subject": subject,
        "html": body_html,
        "tender_count": str(len(tenders)),
        "early_signal_count": str(len(early_signals)),
        "competitor_count": str(len(competitors)),
    }


def send_digest_for_profile(session: Session, profile: ClientProfile) -> dict[str, Any]:
    """Generate and send one digest email. Returns per-client result metadata."""
    digest = generate_digest(session, profile)
    try:
        result = send_email(
            to=profile.email,
            subject=digest["subject"],
            html=digest["html"],
        )
        return {
            "profile_id": profile.id,
            "email": profile.email,
            "company_id": profile.company_id,
            "status": "sent",
            "resend_id": result.get("id"),
            "tender_count": int(digest["tender_count"]),
            "early_signal_count": int(digest.get("early_signal_count", 0)),
            "competitor_count": int(digest["competitor_count"]),
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[EmailAlerts] Failed for profile_id=%s email=%s: %s",
            profile.id,
            profile.email,
            exc,
        )
        return {
            "profile_id": profile.id,
            "email": profile.email,
            "company_id": profile.company_id,
            "status": "failed",
            "error": str(exc)[:300],
        }


def send_all_alert_digests(*, notify_telegram: bool = True) -> dict[str, Any]:
    """
    Loop all client_profiles with alerts_enabled=true, generate and send digests.

    Returns summary counts and per-client results.
    """
    session = get_session()
    try:
        profiles = session.scalars(
            select(ClientProfile)
            .where(ClientProfile.alerts_enabled.is_(True))
            .order_by(ClientProfile.id)
        ).all()

        results: list[dict[str, Any]] = []
        sent = failed = 0

        for profile in profiles:
            outcome = send_digest_for_profile(session, profile)
            results.append(outcome)
            if outcome["status"] == "sent":
                sent += 1
            else:
                failed += 1

        summary = {
            "status": "complete",
            "total": len(profiles),
            "sent": sent,
            "failed": failed,
            "results": results,
        }

        if notify_telegram:
            send_telegram_message(
                "\u2705 *Email Alert Batch Complete*\n"
                f"Profiles: {len(profiles)}\n"
                f"Sent: {sent}\n"
                f"Failed: {failed}"
            )

        logger.info(
            "[EmailAlerts] Batch complete — total=%s sent=%s failed=%s",
            len(profiles),
            sent,
            failed,
        )
        return summary
    finally:
        session.close()
