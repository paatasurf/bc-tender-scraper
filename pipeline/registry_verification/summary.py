"""Compute platform verification summary from provider evidence."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from db.models import CompanyRegistryLink
from db.registry_constants import (
    AUTO_MATCH_TIERS,
    OFFICIAL_REGISTRY_SOURCES,
    VERIFICATION_CONFIRMED_ACTIVE,
    VERIFICATION_CONFIRMED_INACTIVE,
    VERIFICATION_LEVEL_MULTI_SOURCE,
    VERIFICATION_LEVEL_NONE,
    VERIFICATION_LEVEL_OFFICIAL_REGISTRY,
    VERIFICATION_LEVEL_VERIFIED,
)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def _is_confirmed_verification(link: CompanyRegistryLink) -> bool:
    return link.verification_status in {
        VERIFICATION_CONFIRMED_ACTIVE,
        VERIFICATION_CONFIRMED_INACTIVE,
    }


def compute_verification_summary(links: list[CompanyRegistryLink]) -> dict[str, Any]:
    """Derive platform verification level from stored provider evidence."""
    if not links:
        return {
            "verification_level": VERIFICATION_LEVEL_NONE,
            "verified_sources": [],
            "last_verified_at": None,
        }

    confirmed = [
        link
        for link in links
        if _is_confirmed_verification(link) and link.match_tier in AUTO_MATCH_TIERS
    ]
    verified_sources = sorted({link.source for link in confirmed})
    last_verified_at = max(link.linked_at for link in links if link.linked_at is not None)

    if not confirmed:
        level = VERIFICATION_LEVEL_NONE
    elif len(verified_sources) >= 2:
        level = VERIFICATION_LEVEL_MULTI_SOURCE
    elif verified_sources and verified_sources[0] in OFFICIAL_REGISTRY_SOURCES:
        level = VERIFICATION_LEVEL_OFFICIAL_REGISTRY
    else:
        level = VERIFICATION_LEVEL_VERIFIED

    return {
        "verification_level": level,
        "verified_sources": verified_sources,
        "last_verified_at": _iso(last_verified_at),
    }
