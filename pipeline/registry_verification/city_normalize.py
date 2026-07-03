"""City normalization for registry verification matching."""

from __future__ import annotations

import re

CITY_PREFIX_RE = re.compile(r"^(city of|township of|district of|municipality of)\s+", re.I)

CITY_ALIASES: dict[str, str] = {
    "vancouver": "vancouver",
    "city of vancouver": "vancouver",
    "burnaby": "burnaby",
    "surrey": "surrey",
    "victoria": "victoria",
    "kelowna": "kelowna",
    "township of langley": "langley",
    "langley": "langley",
    "nanaimo": "nanaimo",
    "new westminster": "newwestminster",
    "prince george": "princegeorge",
    "chilliwack": "chilliwack",
    "coquitlam": "coquitlam",
    "richmond": "richmond",
    "north vancouver": "northvancouver",
    "west vancouver": "westvancouver",
}


def normalize_city(raw: str) -> str:
    """Normalize municipality name to a compact lookup key."""
    cleaned = (raw or "").strip().lower()
    if not cleaned:
        return ""
    cleaned = CITY_PREFIX_RE.sub("", cleaned)
    cleaned = re.sub(r"[^a-z0-9 ]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if cleaned in CITY_ALIASES:
        return CITY_ALIASES[cleaned]
    return cleaned.replace(" ", "")


def extract_city_from_address(address: str) -> str:
    """Best-effort city extraction from a comma-separated address string."""
    parts = [part.strip() for part in (address or "").split(",") if part.strip()]
    for part in reversed(parts):
        upper = part.upper()
        if upper in {"BC", "BRITISH COLUMBIA", "CANADA"} or re.match(r"^[A-Z]\d[A-Z]\s*\d[A-Z]\d$", upper):
            continue
        if re.search(r"\b(bc|british columbia|canada)\b", part, re.I):
            continue
        city = normalize_city(part)
        if city:
            return city
    return ""
