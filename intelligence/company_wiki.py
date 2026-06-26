"""
intelligence/company_wiki.py
────────────────────────────
Generate an AI-powered company intelligence wiki for a given company.

Constitution compliance:
  - Claude API: human-readable text only — no scores or numbers in the prompt
  - Scoring logic stays in Python, not in prompts
  - Location matching: city/region level only
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Literal

import anthropic
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from config.env import get_anthropic_api_key
from db.connection import get_session
from db.models import ArchCompany, Company, CompanyWiki, ContractAward, Permit

logger = logging.getLogger(__name__)

CLAUDE_MODEL = "claude-sonnet-4-6"
CompanyKind = Literal["construction", "architecture"]


# ──────────────────────────────────────────────
# Data collection helpers
# ──────────────────────────────────────────────

def _get_company(session: Session, company_id: int, kind: CompanyKind) -> Company | ArchCompany:
    model = ArchCompany if kind == "architecture" else Company
    obj = session.get(model, company_id)
    if obj is None:
        raise ValueError(f"No {kind} company with id={company_id}")
    return obj


def _get_permits(session: Session, company_name: str, limit: int = 50) -> list[dict[str, Any]]:
    rows = session.execute(
        select(
            Permit.address,
            Permit.permit_type,
            Permit.project_value,
            Permit.issue_date,
            Permit.city,
        )
        .where(Permit.applicant.ilike(f"%{company_name}%"))
        .order_by(Permit.issue_date.desc())
        .limit(limit)
    ).all()
    return [
        {
            "address": r.address,
            "type": r.permit_type,
            "value": r.project_value,
            "date": r.issue_date,
            "city": r.city,
        }
        for r in rows
    ]


def _get_awards(session: Session, company_id: int, limit: int = 30) -> list[dict[str, Any]]:
    rows = session.execute(
        select(
            ContractAward.title,
            ContractAward.buyer_organization,
            ContractAward.award_value,
            ContractAward.award_date,
            ContractAward.procurement_category,
            ContractAward.delivery_region,
        )
        .where(ContractAward.company_id == company_id)
        .order_by(ContractAward.award_date.desc())
        .limit(limit)
    ).all()
    return [
        {
            "title": r.title,
            "buyer": r.buyer_organization,
            "value": r.award_value,
            "date": r.award_date,
            "category": r.procurement_category,
            "region": r.delivery_region,
        }
        for r in rows
    ]


def _get_cip(company: Company | ArchCompany) -> dict[str, Any] | None:
    return getattr(company, "cip_json", None)


def _build_data_snapshot(
    company: Company | ArchCompany,
    permits: list[dict[str, Any]],
    awards: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "total_projects": getattr(company, "total_projects", 0),
        "total_value": getattr(company, "total_value", 0.0),
        "avg_project_value": getattr(company, "avg_project_value", 0.0),
        "project_types": getattr(company, "project_types", []),
        "neighborhoods": getattr(company, "neighborhoods", []),
        "first_project_date": getattr(company, "first_project_date", ""),
        "last_project_date": getattr(company, "last_project_date", ""),
        "award_count": getattr(company, "award_count", 0),
        "total_award_value": getattr(company, "total_award_value", 0.0),
        "award_categories": getattr(company, "award_categories", []),
        "award_clients": getattr(company, "award_clients", []),
        "primary_trade": getattr(company, "primary_trade", ""),
        "trade_tags": getattr(company, "trade_tags", []),
        "dominant_sector": getattr(company, "dominant_sector", ""),
        "geographic_reach": getattr(company, "geographic_reach", ""),
        "company_tier": getattr(company, "company_tier", ""),
        "primary_city": getattr(company, "primary_city", ""),
        "recent_permits_count": len(permits),
        "recent_awards_count": len(awards),
    }


# ──────────────────────────────────────────────
# Prompt construction (text-only, per constitution)
# ──────────────────────────────────────────────

def _build_prompt(
    company: Company | ArchCompany,
    kind: CompanyKind,
    permits: list[dict[str, Any]],
    awards: list[dict[str, Any]],
    cip: dict[str, Any] | None,
) -> str:
    name = company.name
    trade = getattr(company, "primary_trade", "") or "general contractor"
    sector = getattr(company, "dominant_sector", "") or "construction"
    city = getattr(company, "primary_city", "") or "British Columbia"
    project_types = getattr(company, "project_types", []) or []
    neighborhoods = getattr(company, "neighborhoods", []) or []
    trade_tags = getattr(company, "trade_tags", []) or []
    award_clients = getattr(company, "award_clients", []) or []
    award_categories = getattr(company, "award_categories", []) or []

    permit_lines = "\n".join(
        f"  - {p['date']}: {p['type']} at {p['city']} (value: {p['value']})"
        for p in permits[:15]
    ) or "  (no permit data available)"

    award_lines = "\n".join(
        f"  - {a['date']}: {a['title']} for {a['buyer']} in {a['region']}"
        for a in awards[:15]
    ) or "  (no contract award data available)"

    cip_section = ""
    if cip:
        cip_section = f"""
Company Intelligence Profile (structured data):
{json.dumps(cip, indent=2, default=str)[:2000]}
"""

    return f"""You are a business intelligence analyst specializing in the British Columbia construction market.

Write a comprehensive company intelligence wiki for **{name}**, a {kind} firm active in BC.

Use only the data provided below. Do not invent facts or add speculation. Write in professional, factual prose.

---
COMPANY OVERVIEW
- Name: {name}
- Primary trade / specialization: {trade}
- Dominant sector: {sector}
- Base city / region: {city}
- Geographic reach: {getattr(company, 'geographic_reach', '') or 'regional'}
- Company tier: {getattr(company, 'company_tier', '') or 'unknown'}
- Project types: {', '.join(project_types[:10]) or 'various'}
- Neighborhoods active in: {', '.join(neighborhoods[:10]) or 'not specified'}
- Trade tags: {', '.join(trade_tags[:10]) or 'not specified'}

CONTRACT AWARDS (recent)
{award_lines}

KEY CLIENTS: {', '.join(award_clients[:10]) or 'not available'}
AWARD CATEGORIES: {', '.join(award_categories[:8]) or 'not available'}

PERMIT ACTIVITY (recent)
{permit_lines}
{cip_section}
---

Write the wiki using these exact section headers (use markdown ##):

## Summary
One to two paragraph factual overview of the company: who they are, what they do, where they operate.

## Specializations
What types of work do they do? What trades, sectors, or project types define them?

## Market Position
How do they sit in the BC construction market? What client types do they serve? What is their typical project profile?

## Geographic Focus
Which cities, regions, or neighbourhoods are they most active in?

## Competitive Profile
What makes this company distinctive? What are their known strengths based on the data?

Keep each section to two to four sentences. Do not include numeric scores, ratings, or percentages — describe qualitatively only."""


# ──────────────────────────────────────────────
# Claude API call
# ──────────────────────────────────────────────

def _call_claude(prompt: str) -> str:
    api_key = get_anthropic_api_key()
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not configured")

    client = anthropic.Anthropic(api_key=api_key)
    message = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text


# ──────────────────────────────────────────────
# Section parsing
# ──────────────────────────────────────────────

def _extract_section(markdown: str, header: str) -> str:
    """Pull the text under a ## Section header until the next ## or end of string."""
    import re
    pattern = rf"##\s+{re.escape(header)}\s*\n(.*?)(?=\n##|\Z)"
    m = re.search(pattern, markdown, re.DOTALL | re.IGNORECASE)
    return m.group(1).strip() if m else ""


def _parse_wiki_sections(markdown: str) -> dict[str, str]:
    return {
        "summary": _extract_section(markdown, "Summary"),
        "specializations": _extract_section(markdown, "Specializations"),
        "market_position": _extract_section(markdown, "Market Position"),
        "geographic_focus": _extract_section(markdown, "Geographic Focus"),
        "competitive_profile": _extract_section(markdown, "Competitive Profile"),
    }


# ──────────────────────────────────────────────
# Upsert
# ──────────────────────────────────────────────

def _upsert_wiki(
    session: Session,
    company_id: int,
    kind: CompanyKind,
    company_name: str,
    markdown: str,
    sections: dict[str, str],
    snapshot: dict[str, Any],
) -> CompanyWiki:
    now = datetime.now(timezone.utc)
    stmt = (
        insert(CompanyWiki)
        .values(
            company_id=company_id,
            company_kind=kind,
            company_name=company_name,
            wiki_markdown=markdown,
            summary=sections["summary"],
            specializations=sections["specializations"],
            market_position=sections["market_position"],
            geographic_focus=sections["geographic_focus"],
            competitive_profile=sections["competitive_profile"],
            data_snapshot=snapshot,
            model_used=CLAUDE_MODEL,
            generated_at=now,
            updated_at=now,
        )
        .on_conflict_do_update(
            index_elements=["company_id", "company_kind"],
            set_={
                "company_name": company_name,
                "wiki_markdown": markdown,
                "summary": sections["summary"],
                "specializations": sections["specializations"],
                "market_position": sections["market_position"],
                "geographic_focus": sections["geographic_focus"],
                "competitive_profile": sections["competitive_profile"],
                "data_snapshot": snapshot,
                "model_used": CLAUDE_MODEL,
                "generated_at": now,
                "updated_at": now,
            },
        )
        .returning(CompanyWiki)
    )
    result = session.execute(stmt)
    session.commit()
    return result.scalar_one()


# ──────────────────────────────────────────────
# Public interface
# ──────────────────────────────────────────────

def generate_company_wiki(
    company_id: int,
    kind: CompanyKind = "construction",
    session: Session | None = None,
) -> dict[str, Any]:
    """
    Generate and persist an AI company wiki for the given company.

    Parameters
    ----------
    company_id : int
        Primary key in companies (kind='construction') or arch_companies (kind='architecture').
    kind : 'construction' | 'architecture'
        Which company table to pull from.
    session : Session | None
        Optional existing SQLAlchemy session; a new one is created if not provided.

    Returns
    -------
    dict with keys: company_id, company_name, kind, wiki_markdown, sections, generated_at
    """
    owns_session = session is None
    if owns_session:
        session = get_session()

    try:
        company = _get_company(session, company_id, kind)
        permits = _get_permits(session, company.name)
        awards = _get_awards(session, company_id)
        cip = _get_cip(company)
        snapshot = _build_data_snapshot(company, permits, awards)

        prompt = _build_prompt(company, kind, permits, awards, cip)
        logger.info("[CompanyWiki] calling Claude for company_id=%s kind=%s", company_id, kind)
        markdown = _call_claude(prompt)

        sections = _parse_wiki_sections(markdown)
        wiki = _upsert_wiki(
            session,
            company_id=company_id,
            kind=kind,
            company_name=company.name,
            markdown=markdown,
            sections=sections,
            snapshot=snapshot,
        )

        logger.info("[CompanyWiki] saved wiki id=%s for %s", wiki.id, company.name)
        return {
            "company_id": company_id,
            "company_name": company.name,
            "kind": kind,
            "wiki_id": wiki.id,
            "wiki_markdown": markdown,
            "sections": sections,
            "data_snapshot": snapshot,
            "model_used": CLAUDE_MODEL,
            "generated_at": wiki.generated_at.isoformat(),
        }
    finally:
        if owns_session:
            session.close()
