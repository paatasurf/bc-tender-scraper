"""Verification Hub read API helpers."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import CompanyRegistryLink
from pipeline.registry_verification.payload import registry_link_to_verification_payload
from pipeline.registry_verification.summary import compute_verification_summary


def get_company_registry_verification(
    session: Session,
    company_id: int,
) -> dict[str, Any] | None:
    """Return best verification evidence for a company profile (evidence only)."""
    links = session.scalars(
        select(CompanyRegistryLink)
        .where(CompanyRegistryLink.company_id == company_id)
        .order_by(CompanyRegistryLink.confidence.desc(), CompanyRegistryLink.linked_at.desc())
    ).all()
    if not links:
        return None

    primary = links[0]
    payload = registry_link_to_verification_payload(primary)
    if len(links) > 1:
        payload["additional_links"] = [
            registry_link_to_verification_payload(link) for link in links[1:]
        ]
    return payload


def get_company_verification_hub(
    session: Session,
    company_id: int,
) -> dict[str, Any]:
    """Return Verification Hub payload for a company profile."""
    from pipeline.registry_verification.hub import build_provider_profiles

    links = session.scalars(
        select(CompanyRegistryLink)
        .where(CompanyRegistryLink.company_id == company_id)
        .order_by(CompanyRegistryLink.confidence.desc(), CompanyRegistryLink.linked_at.desc())
    ).all()

    verification_summary = compute_verification_summary(list(links))
    verification_sources = build_provider_profiles(session, company_id)
    registry_verification = get_company_registry_verification(session, company_id)

    return {
        "verification_summary": verification_summary,
        "verification_sources": verification_sources,
        "registry_verification": registry_verification,
    }
