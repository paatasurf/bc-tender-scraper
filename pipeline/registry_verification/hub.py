"""Verification Hub — orchestrates provider execution and evidence exposure."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from db.registry_constants import REGISTRY_SOURCE_ODBUS, REGISTRY_SOURCE_ORGBOOK
from pipeline.registry_verification.base import VerificationProvider
from pipeline.registry_verification.providers.odbus_provider import OdbusProvider
from pipeline.registry_verification.providers.orgbook_provider import OrgbookProvider

PROVIDERS: dict[str, VerificationProvider] = {
    REGISTRY_SOURCE_ODBUS: OdbusProvider(),
    REGISTRY_SOURCE_ORGBOOK: OrgbookProvider(),
}


def list_provider_sources() -> list[str]:
    return sorted(PROVIDERS.keys())


def get_provider(source: str) -> VerificationProvider:
    provider = PROVIDERS.get(source)
    if provider is None:
        raise KeyError(f"Unknown verification provider: {source}")
    return provider


def import_reference_data(session: Session, *, source: str, path: str) -> dict[str, Any]:
    return get_provider(source).import_reference_data(session, path=path)


def match_company(
    session: Session,
    company_id: int,
    *,
    source: str,
    include_review_tiers: bool = False,
) -> dict[str, Any] | None:
    return get_provider(source).match_company(
        session,
        company_id,
        include_review_tiers=include_review_tiers,
    )


def batch_match(
    session: Session,
    *,
    sources: list[str] | None = None,
    company_ids: list[int] | None = None,
    include_review_tiers: bool = False,
) -> dict[str, Any]:
    """Run batch matching across one or more providers."""
    active_sources = sources or list_provider_sources()
    results: dict[str, Any] = {}
    for source in active_sources:
        provider = get_provider(source)
        results[source] = provider.batch_match(
            session,
            company_ids=company_ids,
            include_review_tiers=include_review_tiers,
        )
    return {
        "sources": active_sources,
        "providers": results,
        "include_review_tiers": include_review_tiers,
    }


def build_provider_profiles(session: Session, company_id: int) -> list[dict[str, Any]]:
    """Return successful provider profiles for a company."""
    profiles: list[dict[str, Any]] = []
    for source, provider in sorted(PROVIDERS.items()):
        profile = provider.build_profile(session, company_id)
        if profile is not None:
            profiles.append(profile)
    return profiles
