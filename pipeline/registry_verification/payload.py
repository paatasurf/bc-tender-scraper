"""Verification evidence payload helpers."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from db.models import CompanyRegistryLink
from db.registry_constants import (
    REGISTRY_SOURCE_DISPLAY_NAMES,
    VERIFICATION_CONFIRMED_ACTIVE,
    VERIFICATION_CONFIRMED_INACTIVE,
)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def registry_link_to_verification_payload(link: CompanyRegistryLink) -> dict[str, Any]:
    metadata = link.metadata_json or {}
    source_label = REGISTRY_SOURCE_DISPLAY_NAMES.get(link.source, link.source)
    verified = link.verification_status in {
        VERIFICATION_CONFIRMED_ACTIVE,
        VERIFICATION_CONFIRMED_INACTIVE,
    }
    payload: dict[str, Any] = {
        "verified": verified,
        "source": source_label,
        "source_key": link.source,
        "external_id": link.external_id,
        "status": metadata.get("status", ""),
        "naics": metadata.get("naics", ""),
        "provider": metadata.get("provider", ""),
        "licence_number": metadata.get("licence_number", ""),
        "business_name": metadata.get("business_name", ""),
        "legal_name": metadata.get("legal_name", metadata.get("business_name", "")),
        "business_number": metadata.get("business_number", ""),
        "registry_id": metadata.get("registry_id", ""),
        "entity_type": metadata.get("entity_type", ""),
        "city": metadata.get("city", ""),
        "province": metadata.get("province", ""),
        "match_tier": link.match_tier,
        "confidence": link.confidence,
        "verification_status": link.verification_status,
        "multi_location": link.multi_location,
        "multi_location_cities": metadata.get("multi_location_cities", []),
        "linked_at": _iso(link.linked_at),
    }
    if metadata.get("dba_names"):
        payload["dba_names"] = metadata.get("dba_names", [])
    return payload
