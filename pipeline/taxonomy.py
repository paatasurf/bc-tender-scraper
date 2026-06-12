"""Rule-based trade and project-type taxonomy for BD intelligence."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

TRADES = (
    "general_building",
    "concrete",
    "structural",
    "civil",
    "electrical",
    "mechanical",
    "hvac",
    "plumbing",
    "demolition",
    "excavation",
    "roofing",
    "glazing",
    "landscaping",
    "development",
    "architecture",
    "interior_design",
    "engineering",
    "consulting",
)

ADJACENT_TRADES: dict[str, tuple[str, ...]] = {
    "concrete": ("structural", "civil", "general_building", "excavation"),
    "structural": ("concrete", "civil", "general_building", "engineering"),
    "civil": ("concrete", "structural", "excavation", "landscaping"),
    "electrical": ("mechanical", "general_building"),
    "mechanical": ("hvac", "plumbing", "electrical"),
    "hvac": ("mechanical", "plumbing"),
    "plumbing": ("mechanical", "hvac"),
    "demolition": ("excavation", "general_building"),
    "excavation": ("demolition", "civil", "concrete"),
    "general_building": ("concrete", "structural", "demolition", "development"),
    "architecture": ("interior_design", "engineering", "consulting"),
    "interior_design": ("architecture", "general_building"),
    "engineering": ("consulting", "architecture", "civil", "structural"),
    "consulting": ("engineering", "architecture"),
    "development": ("general_building", "landscaping"),
    "roofing": ("general_building", "glazing"),
    "glazing": ("roofing", "general_building"),
    "landscaping": ("civil", "development"),
}

TRADE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("concrete", re.compile(r"\bconcrete\b|\bfoundation\b|\bformwork\b", re.I)),
    ("structural", re.compile(r"\bstructural\b|\bsteel\b|\bseismic\b", re.I)),
    ("civil", re.compile(r"\bcivil\b|\broad\b|\bbridge\b|\binfrastructure\b|\butility\b", re.I)),
    ("electrical", re.compile(r"\belectrical\b|\belectric\b|\blighting\b|\bpower\b", re.I)),
    ("mechanical", re.compile(r"\bmechanical\b|\bmech\b", re.I)),
    ("hvac", re.compile(r"\bhvac\b|\bventilation\b|\bheating\b|\bcooling\b", re.I)),
    ("plumbing", re.compile(r"\bplumbing\b|\bpipe\b|\bdrainage\b", re.I)),
    ("demolition", re.compile(r"\bdemolition\b|\bdeconstruction\b|\babatement\b", re.I)),
    ("excavation", re.compile(r"\bexcavat|\bearthwork\b|\bgrading\b|\bsite prep\b", re.I)),
    ("roofing", re.compile(r"\broof|\broofing\b|\benvelope\b", re.I)),
    ("glazing", re.compile(r"\bglazing\b|\bcurtain wall\b|\bwindow wall\b", re.I)),
    ("landscaping", re.compile(r"\blandscape\b|\blandscaping\b|\bsite work\b", re.I)),
    ("development", re.compile(r"\bdeveloper\b|\bdevelopment\b|\breal estate\b", re.I)),
    ("architecture", re.compile(r"\barchitect|\barchitectural\b|\bdesign firm\b", re.I)),
    ("interior_design", re.compile(r"\binterior design\b|\binteriors\b", re.I)),
    ("engineering", re.compile(r"\bengineering\b|\bengineer\b|\bstructural engineer\b", re.I)),
    ("consulting", re.compile(r"\bconsult|\badvisory\b|\bprofessional services\b", re.I)),
    ("general_building", re.compile(r"\bconstruction\b|\bgeneral contractor\b|\bbuilding\b|\brenovation\b", re.I)),
]

PROJECT_TYPE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("new_building", re.compile(r"\bnew building\b|\bnew construction\b", re.I)),
    ("renovation", re.compile(r"\brenovation\b|\balteration\b|\bretrofit\b|\bupgrade\b|\brepair\b", re.I)),
    ("demolition", re.compile(r"\bdemolition\b|\bdeconstruction\b", re.I)),
    ("design_services", re.compile(r"\bdesign\b|\barchitectural\b|\bconsulting services\b|\bfeasibility\b", re.I)),
    ("civil_works", re.compile(r"\bcivil\b|\binfrastructure\b|\broad\b|\bbridge\b", re.I)),
    ("institutional", re.compile(r"\bhospital\b|\bschool\b|\bcivic\b|\binstitutional\b|\blibrary\b", re.I)),
    ("residential", re.compile(r"\bhousing\b|\bresidential\b|\bmultifamily\b|\bcondo\b", re.I)),
    ("commercial", re.compile(r"\bcommercial\b|\bretail\b|\boffice\b", re.I)),
]

COMPANY_TYPE_TRADE: dict[str, str] = {
    "General Contractor": "general_building",
    "Trade Contractor": "general_building",
    "Architect": "architecture",
    "Engineering": "engineering",
    "Consultant": "consulting",
    "Developer": "development",
}

SOURCE_TO_SEGMENT: dict[str, str] = {
    "civicinfo": "municipal",
    "bidcentral": "commercial",
    "bc_housing": "provincial",
    "buyandsell.gc.ca": "federal",
    "canadabuys.canada.ca": "federal",
    "merx": "commercial",
    "arch": "commercial",
    "federal": "federal",
    "commercial": "commercial",
}


@dataclass
class TradeTagResult:
    primary_trade: str
    secondary_trades: list[str] = field(default_factory=list)
    all_tags: list[str] = field(default_factory=list)
    confidence: float = 0.0


@dataclass
class OpportunityTags:
    trade_tags: list[str]
    project_type_tags: list[str]
    market_segment: str


def _match_patterns(text: str, patterns: list[tuple[str, re.Pattern[str]]]) -> list[str]:
    matched: list[str] = []
    for label, pattern in patterns:
        if pattern.search(text or ""):
            matched.append(label)
    return matched


def tag_company(
    *,
    name: str = "",
    company_type: str = "",
    project_types: list[str] | None = None,
    award_categories: list[str] | None = None,
    specializations: list[str] | None = None,
) -> TradeTagResult:
    blob = " ".join(
        [
            name,
            company_type,
            " ".join(project_types or []),
            " ".join(award_categories or []),
            " ".join(specializations or []),
        ]
    )
    hits = _match_patterns(blob, TRADE_PATTERNS)
    if company_type in COMPANY_TYPE_TRADE and COMPANY_TYPE_TRADE[company_type] not in hits:
        hits.append(COMPANY_TYPE_TRADE[company_type])

    if not hits:
        primary = COMPANY_TYPE_TRADE.get(company_type, "general_building")
        return TradeTagResult(primary_trade=primary, all_tags=[primary], confidence=0.35)

    counts: dict[str, int] = {}
    for tag in hits:
        counts[tag] = counts.get(tag, 0) + 1
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    primary = ranked[0][0]
    secondary = [tag for tag, _ in ranked[1:4] if tag != primary]
    all_tags = [primary, *[t for t in secondary if t not in {primary}]]
    confidence = min(0.95, 0.45 + 0.1 * len(set(hits)))
    return TradeTagResult(
        primary_trade=primary,
        secondary_trades=secondary,
        all_tags=all_tags,
        confidence=confidence,
    )


def tag_opportunity_text(
    *,
    title: str = "",
    category: str = "",
    description: str = "",
    permit_type: str = "",
    source: str = "",
) -> OpportunityTags:
    blob = " ".join(filter(None, [title, category, description, permit_type]))
    trade_tags = _match_patterns(blob, TRADE_PATTERNS)
    project_type_tags = _match_patterns(blob, PROJECT_TYPE_PATTERNS)
    segment = SOURCE_TO_SEGMENT.get((source or "").lower(), "commercial")
    if "federal" in blob.lower() or "canada" in (title or "").lower():
        segment = "federal"
    if "municipal" in blob.lower() or "city of" in blob.lower():
        segment = "municipal"
    return OpportunityTags(
        trade_tags=trade_tags or ["unclassified"],
        project_type_tags=project_type_tags,
        market_segment=segment,
    )


def capability_match_score(company_primary: str, company_tags: list[str], opp_tags: list[str]) -> int:
    if not opp_tags:
        return 40
    if company_primary in opp_tags:
        return 100
    if any(tag in company_tags for tag in opp_tags):
        return 75
    adjacent = set(ADJACENT_TRADES.get(company_primary, ()))
    if any(tag in adjacent for tag in opp_tags):
        return 60
    if company_primary == "general_building":
        return 50
    return 15


def adjacent_trades(primary: str) -> list[str]:
    return list(ADJACENT_TRADES.get(primary, ()))
