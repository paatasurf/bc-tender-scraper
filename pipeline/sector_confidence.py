"""Classify dominant_sector inference confidence (high / medium / low)."""

from __future__ import annotations

from collections import Counter
from typing import Literal, Protocol

from pipeline.business_attributes import PERMIT_TYPE_SECTOR, SECTOR_PATTERNS, infer_sector_from_permit, match_patterns

SectorConfidence = Literal["high", "medium", "low"]

PermitInferencePath = Literal["keyword", "pattern", "address", "default"]


class _PermitLike(Protocol):
    permit_type: str | None
    description: str | None
    address: str | None


def permit_inference_path(
    permit_type: str = "",
    description: str = "",
    address: str = "",
) -> PermitInferencePath:
    """How sector was inferred from a single permit row."""
    blob = f"{permit_type} {description} {address}".lower()
    for key in PERMIT_TYPE_SECTOR:
        if key in blob:
            return "keyword"
    if match_patterns(blob, SECTOR_PATTERNS):
        return "pattern"
    addr = (address or "").lower()
    if any(k in addr for k in ("ave", "street", "drive", "crescent", "road")) and "commercial" not in blob:
        if "mixed" not in blob and "commercial" not in (permit_type or "").lower():
            return "address"
    return "default"


def _permit_sector_and_path(
    permit_type: str = "",
    description: str = "",
    address: str = "",
) -> tuple[str, PermitInferencePath]:
    return (
        infer_sector_from_permit(permit_type, description, address),
        permit_inference_path(permit_type, description, address),
    )


def _commercial_wins_in_sector_focus(sector_focus: dict[str, float]) -> bool:
    commercial = sector_focus.get("commercial", 0)
    others = [value for key, value in sector_focus.items() if key != "commercial"]
    if not others:
        return bool(commercial)
    return commercial >= max(others)


def _is_mixed_commercial_wins(
    *,
    sector_focus: dict[str, float],
    permits: list[_PermitLike],
) -> bool:
    """Mixed permit sectors where commercial wins in sector_focus (audit bucket ~376)."""
    if max(sector_focus, key=sector_focus.get) != "commercial":
        return False
    if not _commercial_wins_in_sector_focus(sector_focus):
        return False

    paths: Counter[str] = Counter()
    sectors: Counter[str] = Counter()
    first_five_sectors: list[str] = []
    for permit in permits:
        sector, path = _permit_sector_and_path(
            permit.permit_type or "",
            permit.description or "",
            permit.address or "",
        )
        sectors[sector] += 1
        paths[path] += 1
        if len(first_five_sectors) < 5:
            first_five_sectors.append(sector)

    if paths["default"] == sum(paths.values()):
        return False
    if not sectors.get("commercial"):
        return False
    if paths["keyword"] + paths["pattern"] <= 0:
        return False
    if "commercial" not in first_five_sectors:
        return False
    if not any(sectors.get(name, 0) for name in ("residential", "industrial", "institutional")):
        return False
    return True


def classify_sector_confidence(
    *,
    sector_focus: dict[str, float],
    permits: list[_PermitLike],
) -> SectorConfidence:
    """Map sector inference path to confidence tier.

    high   — explicit permit keyword/pattern/address signal (no mixed-default path)
    medium — award/category text only (no permits), or mixed permits where commercial wins
    low    — empty sector_focus default, all-permit soft default, or mixed with defaults
    """
    if not sector_focus:
        return "low"
    if not permits:
        return "medium"

    paths = [
        permit_inference_path(
            p.permit_type or "",
            p.description or "",
            p.address or "",
        )
        for p in permits
    ]
    if all(path == "default" for path in paths):
        return "low"
    if _is_mixed_commercial_wins(sector_focus=sector_focus, permits=permits):
        return "medium"
    if any(path == "default" for path in paths):
        return "low"
    return "high"
