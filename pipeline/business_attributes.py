"""Derive business attributes from text and permit records."""

from __future__ import annotations

import re
from collections import Counter

NAME_TRADE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("electrical", re.compile(r"\belectric|\belectrical\b", re.I)),
    ("mechanical", re.compile(r"\bmechanical\b|\bhvac\b|\bplumbing\b|\bplumb\b", re.I)),
    ("concrete", re.compile(r"\bconcrete\b|\bfoundation\b|\bformwork\b", re.I)),
    ("demolition", re.compile(r"\bdemolition\b|\bdemolish\b", re.I)),
    ("roofing", re.compile(r"\broofing\b|\broof\b", re.I)),
    ("engineering", re.compile(r"\bengineering\b|\bengineer\b", re.I)),
    ("architecture", re.compile(r"\barchitect|\barchitecture\b", re.I)),
    ("consulting", re.compile(r"\bconsult|\badvisory\b", re.I)),
    ("development", re.compile(r"\bdeveloper\b|\bdevelopment\b|\bproperties\b|\bholdings\b", re.I)),
    ("landscaping", re.compile(r"\blandscape\b|\blandscaping\b", re.I)),
    ("civil", re.compile(r"\bcivil\b|\binfrastructure\b", re.I)),
    ("structural", re.compile(r"\bstructural\b", re.I)),
]

SECTOR_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("residential", re.compile(r"\bresidential\b|\bhouse\b|\bdwelling\b|\blaneway\b|\bsingle.?family\b|\bmultifamily\b|\bcondo\b|\bhousing\b", re.I)),
    ("commercial", re.compile(r"\bcommercial\b|\bretail\b|\boffice\b|\bstore\b|\brestaurant\b", re.I)),
    ("institutional", re.compile(r"\binstitutional\b|\bhospital\b|\bschool\b|\buniversity\b|\bcivic\b|\blibrary\b|\bfacility\b", re.I)),
    ("industrial", re.compile(r"\bindustrial\b|\bwarehouse\b|\bfactory\b|\bmanufacturing\b", re.I)),
    ("public", re.compile(r"\bfederal\b|\bprovincial\b|\bmunicipal\b|\bcity of\b|\btranslink\b|\bbc housing\b", re.I)),
]

DELIVERY_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("maintenance", re.compile(r"\bmaintenance\b|\bSOA\b|\bRFSA\b|\brepair,\s*maintenance\b|\bstanding offer\b", re.I)),
    ("design", re.compile(r"\bdesign\b|\barchitectural\b|\bfeasibility\b|\bconsulting services\b|\bprime consultant\b", re.I)),
    ("demolition", re.compile(r"\bdemolition\b|\bdeconstruction\b", re.I)),
    ("renovation", re.compile(r"\brenovation\b|\balteration\b|\bretrofit\b|\bupgrade\b|\brefurbish\b|\btenant improvement\b", re.I)),
    ("new_build", re.compile(r"\bnew building\b|\bnew construction\b|\bnew develop\b", re.I)),
    ("civil", re.compile(r"\bcivil\b|\broad\b|\bbridge\b|\butility\b|\bsanitary\b|\binfrastructure\b", re.I)),
]

PERMIT_TYPE_DELIVERY: dict[str, str] = {
    "new building": "new_build",
    "new construction": "new_build",
    "new home": "new_build",
    "single family": "new_build",
    "single-family": "new_build",
    "two family": "new_build",
    "multiplex": "new_build",
    "laneway": "new_build",
    "demolition": "demolition",
    "alteration": "renovation",
    "renovation": "renovation",
    "tenant improvement": "renovation",
    "addition": "renovation",
    "change of use": "renovation",
    "repair": "renovation",
}

PERMIT_TYPE_SECTOR: dict[str, str] = {
    "single family": "residential",
    "single-family": "residential",
    "two family": "residential",
    "duplex": "residential",
    "multiplex": "residential",
    "laneway": "residential",
    "house": "residential",
    "dwelling": "residential",
    "new home": "residential",
    "commercial": "commercial",
    "institutional": "institutional",
    "industrial": "industrial",
}

MAINTENANCE_RE = re.compile(
    r"\bmaintenance\b|\bSOA\b|\bRFSA\b|\bstanding offer\b|\brepair,\s*maintenance\b",
    re.I,
)

BC_CITIES = (
    "vancouver", "burnaby", "richmond", "surrey", "coquitlam", "langley",
    "delta", "north vancouver", "west vancouver", "new westminster", "port coquitlam",
    "maple ridge", "abbotsford", "victoria", "kelowna", "kamloops", "penticton",
)

ENTITY_CLASS_MAP = {
    "General Contractor": "contractor",
    "Trade Contractor": "contractor",
    "Architect": "designer",
    "Engineering": "consultant",
    "Consultant": "consultant",
    "Developer": "developer",
    "Unknown": "contractor",
}


def trade_from_name(name: str) -> str | None:
    for trade, pattern in NAME_TRADE_PATTERNS:
        if pattern.search(name or ""):
            return trade
    return None


def match_patterns(text: str, patterns: list[tuple[str, re.Pattern[str]]]) -> list[str]:
    matched: list[str] = []
    for label, pattern in patterns:
        if pattern.search(text or ""):
            matched.append(label)
    return matched


def infer_sector(text: str) -> str:
    hits = match_patterns(text, SECTOR_PATTERNS)
    return hits[0] if hits else "commercial"


def infer_sector_from_permit(permit_type: str = "", description: str = "", address: str = "") -> str:
    blob = f"{permit_type} {description} {address}".lower()
    for key, sector in PERMIT_TYPE_SECTOR.items():
        if key in blob:
            return sector
    hits = match_patterns(blob, SECTOR_PATTERNS)
    if hits:
        return hits[0]
    addr = (address or "").lower()
    if any(k in addr for k in ("ave", "street", "drive", "crescent", "road")) and "commercial" not in blob:
        if "mixed" not in blob and "commercial" not in (permit_type or "").lower():
            return "residential"
    return infer_sector(blob)


def infer_delivery(text: str, permit_type: str = "") -> str:
    pt = (permit_type or "").lower()
    for key, delivery in PERMIT_TYPE_DELIVERY.items():
        if key in pt:
            return delivery
    hits = match_patterns(text, DELIVERY_PATTERNS)
    if hits:
        return hits[0]
    if "building permit" in pt or "new" in pt:
        return "new_build"
    return "renovation"


def infer_orientation(texts: list[str]) -> str:
    if not texts:
        return "construction"
    maintenance = sum(1 for t in texts if MAINTENANCE_RE.search(t or ""))
    design = sum(1 for t in texts if re.search(r"\bdesign\b|\barchitectural\b|\bconsulting services\b", t or "", re.I))
    total = len(texts)
    if maintenance / total >= 0.35:
        return "maintenance"
    if design / total >= 0.35:
        return "design"
    if maintenance / total >= 0.15 and design / total >= 0.15:
        return "mixed"
    return "construction"


def parse_city_from_address(address: str) -> str:
    if not address:
        return ""
    lower = address.lower()
    for city in BC_CITIES:
        if city in lower:
            return city.title() if " " not in city else " ".join(w.title() for w in city.split())
    parts = [p.strip() for p in address.split(",")]
    for part in parts:
        pl = part.lower()
        if pl in {"bc", "british columbia", "canada"}:
            continue
        if re.match(r"^[A-Z]\d[A-Z]", part.replace(" ", "")):
            continue
        if part and len(part) < 40:
            return part
    return ""


def normalize_permit_project_type(permit_type: str, description: str = "", address: str = "") -> str:
    blob = f"{permit_type} {description}".lower()
    delivery = infer_delivery(blob, permit_type)
    sector = infer_sector_from_permit(permit_type, description, address)
    return f"{delivery}_{sector}"


def sector_distribution(texts: list[str]) -> dict[str, float]:
    if not texts:
        return {}
    counts: Counter[str] = Counter(infer_sector(t) for t in texts)
    total = sum(counts.values())
    return {k: round(v / total, 3) for k, v in counts.most_common()}


def value_percentiles(values: list[float]) -> tuple[float, float, float, float]:
    if not values:
        return 0.0, 0.0, 0.0, 0.0
    sorted_vals = sorted(v for v in values if v > 0)
    if not sorted_vals:
        return 0.0, 0.0, 0.0, 0.0

    def pct(p: float) -> float:
        idx = int(round((len(sorted_vals) - 1) * p))
        return sorted_vals[max(0, min(idx, len(sorted_vals) - 1))]

    return pct(0.25), pct(0.5), pct(0.75), sorted_vals[-1]


def deal_size_band(median: float) -> str:
    if median <= 0:
        return "unknown"
    if median < 100_000:
        return "micro"
    if median < 500_000:
        return "small"
    if median < 2_000_000:
        return "mid"
    if median < 10_000_000:
        return "large"
    return "mega"


def geographic_reach(city_shares: dict[str, float]) -> str:
    if not city_shares:
        return "local"
    top = max(city_shares.values()) if city_shares else 0
    if top >= 0.6:
        return "local"
    if top >= 0.35:
        return "regional"
    if len(city_shares) <= 3:
        return "regional"
    return "provincial"


def specialization_confidence(sector_focus: dict[str, float], cluster_count: int) -> float:
    if not sector_focus:
        return 0.35
    top = max(sector_focus.values())
    base = 0.4 + top * 0.45
    if cluster_count >= 2:
        base += 0.1
    return round(min(0.95, base), 2)


def buyer_type_from_org(org: str, source: str = "") -> str:
    org_l = (org or "").lower()
    src = (source or "").lower()
    if "canada" in org_l or "federal" in src or "gc.ca" in src:
        return "federal"
    if "province" in org_l or "bc " in org_l or "provincial" in src:
        return "provincial"
    if "city of" in org_l or "district of" in org_l or "municipality" in org_l:
        return "municipal"
    if any(k in org_l for k in ("hospital", "school", "university", "health authority")):
        return "institutional"
    return "private"
