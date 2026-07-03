"""Abstract geographic presence for Construction Tier scoring.

The scoring engine consumes GeographicPresence only — not neighborhoods,
municipalities, or other source-specific fields directly.
"""

from __future__ import annotations

from dataclasses import dataclass

from db.models import Company
from pipeline.construction_tier_config import GEOGRAPHIC_PRESENCE_SOURCE

# Current implementation derives presence from permit-derived neighborhoods.
# Future sources: municipalities, regional districts, service areas.


@dataclass(frozen=True)
class GeographicPresence:
    """Source-agnostic geographic footprint for scoring."""

    location_count: int
    source: str
    locations: tuple[str, ...] = ()


def derive_geographic_presence(company: Company) -> GeographicPresence:
    """Build abstract geographic presence from the current company data source."""
    if GEOGRAPHIC_PRESENCE_SOURCE == "neighborhoods":
        locations = tuple(sorted({n.strip() for n in (company.neighborhoods or []) if n and n.strip()}))
        return GeographicPresence(
            location_count=len(locations),
            source=GEOGRAPHIC_PRESENCE_SOURCE,
            locations=locations,
        )

    # Future: municipalities, regional_districts, service_areas
    return GeographicPresence(location_count=0, source=GEOGRAPHIC_PRESENCE_SOURCE, locations=())
