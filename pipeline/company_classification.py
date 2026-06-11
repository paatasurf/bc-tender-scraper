from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import Float, func, select
from sqlalchemy.orm import Session

from db.models import Company, Permit

ACTIVE_LIFECYCLE_DAYS = 365
STATS_WINDOW_DAYS = 730
CLASSIFY_BATCH_SIZE = 500

MARKET_CATEGORIES = (
    "General Contractor",
    "Trade Contractor",
    "Developer",
    "Architect",
    "Engineering",
    "Consultant",
    "Homeowner",
    "Unknown",
)

GC_TRADE_TYPES = {"General Contractor", "Trade Contractor"}

BIZ_SUFFIX = re.compile(
    r"\b(inc|ltd|corp|llc|lp|limited|company|co\.|holdings|developments|"
    r"construction|builder|contractor|architect|engineering|design|consulting|group|studio|homes)\b",
    re.I,
)

KNOWN_FIRMS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"d'?\s*arcy\s+jones", re.I), "Architect"),
    (re.compile(r"gustavson\s+wylie", re.I), "Architect"),
    (re.compile(r"w\.?\s*t\.?\s*leung\s+architects?", re.I), "Architect"),
    (re.compile(r"archivolt", re.I), "Architect"),
    (re.compile(r"perkins\s*\+\s*will", re.I), "Architect"),
    (re.compile(r"stuart\s+howard", re.I), "Architect"),
    (re.compile(r"\bdsai\b", re.I), "Architect"),
    (re.compile(r"govan\s+brown", re.I), "General Contractor"),
    (re.compile(r"acres\s+enterprises", re.I), "General Contractor"),
    (re.compile(r"priority\s+projects", re.I), "General Contractor"),
    (re.compile(r"syncor\s+solutions", re.I), "General Contractor"),
    (re.compile(r"jakobsen\s+associates", re.I), "General Contractor"),
    (re.compile(r"eyco\s+building\s+group", re.I), "General Contractor"),
    (re.compile(r"raffaele\s+(?:&|and)\s+associates", re.I), "General Contractor"),
    (re.compile(r"structus\s*design", re.I), "Engineering Firm"),
    (re.compile(r"building\s+envelope\s+engineers?", re.I), "Engineering Firm"),
    (re.compile(r"\bmcm\b", re.I), "General Contractor"),
]


def _compile_rules() -> list[dict[str, Any]]:
    raw_rules = [
        {
            "category": "Building Code Consultant",
            "patterns": [
                r"\bbuilding code\b",
                r"\blmdg\b",
                r"\bcertified professional\b",
                r"\bthorson consulting\b",
                r"\bpioneer code\b",
                r"\bcode consultant",
                r"\bcp\b.*\bconsult",
                r"\bbuilding code consultant",
            ],
        },
        {
            "category": "Architect",
            "patterns": [
                r"\barchitects?\b",
                r"\barchitecture\b",
                r"\baibc\b",
                r"\barchitectural design\b",
                r"\bmeasured architecture\b",
                r"\bkasian\b",
                r"\bdialog\b",
                r"\bwiedemann\b",
                r"\blineform architecture\b",
                r"\biredale architecture\b",
                r"\bproscenium architecture\b",
                r"\bacton ostry\b",
                r"\bperkins\s*\+\s*will\b",
                r"\bgensler\b",
                r"\bm\s*squared architecture\b",
                r"\bd['']?\s*arcy\s+jones\b",
                r"\bgustavson\s+wylie\b",
                r"\barchivolt\b",
            ],
            "exclude": [r"\binterior"],
        },
        {
            "category": "Engineering Firm",
            "patterns": [
                r"\bengineering\b",
                r"\bengineers?\b",
                r"\bstructural (?:eng|consult)",
                r"\bgeotechnical\b",
                r"\bbuilding science\b",
                r"\bbuilding envelope engineers?\b",
                r"\bjensen hughes\b",
                r"\bghl consultant",
                r"\bcamphora engineering\b",
                r"\bstrata engineering\b",
                r"\bcft engineering\b",
                r"\bmccuaig and associates engineering\b",
                r"\bleeson engineering\b",
                r"\bwsp canada\b",
                r"\baecom\b",
                r"\bprotection engineering\b",
                r"\bfire engineering\b",
                r"\bard \+ h\b",
                r"\bomicron architecture engineering construction\b",
                r"\barcadis\b",
                r"\bstructus\s*design\b",
            ],
            "exclude": [r"\bbuilding code\b", r"\blmdg\b", r"\bcertified professional\b"],
        },
        {
            "category": "Design Firm",
            "patterns": [
                r"\bhome design\b",
                r"\bbuilding design\b",
                r"\bdesign studio\b",
                r"\bdrafting\b",
                r"\bresidential design\b",
                r"\bdesign group\b",
                r"\bdesign ltd\b",
                r"\bdesign inc\b",
                r"\bdesigner\b",
                r"\bdesign work group\b",
                r"\bdesign build\b",
                r"\binterior",
                r"\bssdg interiors\b",
                r"\bmak interiors\b",
                r"\baura office environments\b",
                r"\blandscape design\b",
                r"\bdesign and drafting\b",
                r"\bcadlab design\b",
                r"\bzoomlink design\b",
                r"\bptl design\b",
                r"\bterra firma design\b",
                r"\barchitrix design\b",
                r"\barchitectural collective\b",
                r"\bintarsia design\b",
                r"\bbla design\b",
                r"\bformwerks\b",
                r"\blanefab\b",
                r"\bsmallworks studios\b",
            ],
            "exclude": [r"\barchitects?\b", r"\barchitecture\b"],
        },
        {
            "category": "Supplier / Manufacturer",
            "patterns": [
                r"\bsupplier\b",
                r"\bmanufactur",
                r"\bdistribut",
                r"\bwholesale\b",
                r"\brentals?\b",
                r"\brebar ltd\b",
                r"\bphoenix tent\b",
                r"\bbrewing co\b",
                r"\bself storage\b",
                r"\bequipment\b",
                r"\bmaterials\b",
                r"\bsteel fabricat",
            ],
        },
        {
            "category": "Trade Contractor",
            "patterns": [
                r"\bplumbing\b",
                r"\belectrical\b",
                r"\belectric ltd\b",
                r"\bhvac\b",
                r"\broofing\b",
                r"\bframing\b",
                r"\bdrywall\b",
                r"\bmasonry\b",
                r"\bconcrete ltd\b",
                r"\bexcavat",
                r"\blandscap(?:ing|e) (?:ltd|inc|contractor)",
                r"\bdemolition\b",
                r"\babatement\b",
                r"\bsprinkler\b",
                r"\bflooring\b",
                r"\bglazing\b",
                r"\bmechanical ltd\b",
                r"\bmechanical inc\b",
                r"\bmechanical contracting\b",
                r"\bpainting\b",
                r"\bfire protection\b",
                r"\bgeothermal\b",
            ],
        },
        {
            "category": "General Contractor",
            "patterns": [
                r"\bgeneral contractors?\b",
                r"\bconstruction ltd\b",
                r"\bconstruction inc\b",
                r"\bconstruction corp\b",
                r"\bconstruction group\b",
                r"\bbuilding group\b",
                r"\bbuilding co\b",
                r"\bbuilders ltd\b",
                r"\bbuilders inc\b",
                r"\bcontractors? ltd\b",
                r"\bcontractors? inc\b",
                r"\bcontracting ltd\b",
                r"\bcontracting inc\b",
                r"\bcustom homes\b",
                r"\bhome builders\b",
                r"\bdesign and construction\b",
                r"\bturner construction\b",
                r"\bkerr construction\b",
                r"\bwales mclelland\b",
                r"\bwoodbine builders\b",
                r"\bsasco contractors?\b",
                r"\bvpac construction\b",
                r"\bicon west construction\b",
                r"\bmann bros construction\b",
                r"\brewtech construction\b",
                r"\bfusion projects\b",
                r"\brichtown construction\b",
                r"\bapple construction\b",
                r"\bchandos construction\b",
                r"\bpcl constructor",
                r"\bmarino general contracting\b",
                r"\brenovat",
                r"\bremodel",
                r"\bhomes ltd\b",
                r"\bhomes inc\b",
                r"\b\w+\s+homes\b",
                r"\bselhomes\b",
                r"\b88 homes\b",
                r"\btrans pacific homes\b",
                r"\bembark homes\b",
                r"\bcoreval homes\b",
                r"\bfixright construction\b",
                r"\bquality construction\b",
                r"\bheadland construction\b",
                r"\bfort modular\b",
                r"\beyco building\b",
            ],
        },
        {
            "category": "Developer",
            "patterns": [
                r"\bdevelopments?\b",
                r"\bdevelopers?\b",
                r"\bholdings?\b",
                r"\bproperties\b",
                r"\bventures\b",
                r"\binvestments?\b",
                r"\brealty\b",
                r"\breal estate\b",
                r"\bwestbank\b",
                r"\blanda global\b",
                r"\blightwell developments\b",
                r"\bpuzzle developments\b",
                r"\bproperty group\b",
            ],
            "exclude": [r"\bconstruction\b", r"\bcontractor\b", r"\bbuilder\b", r"\bcustom homes\b"],
        },
        {
            "category": "Other",
            "patterns": [
                r"\bproject management\b",
                r"\bconsulting\b",
                r"\bconsultant",
                r"\bschool board\b",
                r"\bhospital\b",
                r"\bfoundation\b",
                r"\bcorporation\b",
                r"\buniversity\b",
                r"\bchurch\b",
                r"\bassociation\b",
                r"\bpermitsets\.com\b",
                r"\btfmd consulting\b",
                r"\bcentral west project management\b",
                r"\bmgm\b",
                r"\boperations inc\b",
                r"\badmin\b",
                r"\bmcauley\b",
                r"\bpermit consultant",
                r"\bsenez\b",
                r"\bexpeditor\b",
            ],
            "exclude": [
                r"\bengineering\b",
                r"\barchitects?\b",
                r"\bconstruction\b",
                r"\bbuilding code\b",
            ],
        },
    ]

    compiled: list[dict[str, Any]] = []
    for rule in raw_rules:
        compiled.append(
            {
                "category": rule["category"],
                "patterns": [re.compile(p, re.I) for p in rule["patterns"]],
                "exclude": [re.compile(p, re.I) for p in rule.get("exclude", [])],
            }
        )
    return compiled


CLASSIFICATION_RULES = _compile_rules()


@dataclass
class ClassificationResult:
    internal_category: str
    market_category: str
    confidence: float
    method: str


def parse_name(raw: str) -> dict[str, Any]:
    name = (raw or "").strip()
    dba_match = re.search(r"DBA:\s*(.+)$", name, re.I)
    if dba_match:
        return {
            "legal": re.sub(r"\s*DBA:.+$", "", name, flags=re.I).strip(),
            "dba": dba_match.group(1).strip(),
            "has_dba": True,
            "full": name,
        }
    slash = re.match(r"^(.+?)\s*/\s*(.+)$", name)
    if slash:
        return {
            "legal": slash.group(1).strip(),
            "dba": slash.group(2).strip(),
            "has_dba": True,
            "full": name,
        }
    return {"legal": name, "dba": "", "has_dba": False, "full": name}


def normalize_match_text(texts: list[str]) -> list[str]:
    normalized: list[str] = []
    for text in texts:
        if not text:
            continue
        normalized.append(
            re.sub(
                r"(design|structus|architect)(group|studio|inc)",
                r"\1 \2",
                re.sub(r"([a-z])([A-Z])", r"\1 \2", text),
                flags=re.I,
            )
        )
    return normalized


def to_market_category(internal_category: str) -> str:
    mapping = {
        "General Contractor": "General Contractor",
        "Trade Contractor": "Trade Contractor",
        "Developer": "Developer",
        "Architect": "Architect",
        "Engineering Firm": "Engineering",
        "Building Code Consultant": "Consultant",
        "Design Firm": "Consultant",
        "Supplier / Manufacturer": "Consultant",
        "Other": "Consultant",
        "Property Owner / Homeowner": "Homeowner",
    }
    return mapping.get(internal_category, "Unknown")


def _ai_summary_general_contractor(summary: str) -> ClassificationResult | None:
    if not summary:
        return None
    exclude = re.compile(
        r"\b(architecture firm|architect\b|engineering firm|engineering company|"
        r"permit consultant|expeditor|design firm|design studio|consulting firm)\b",
        re.I,
    )
    if exclude.search(summary):
        return None
    if re.search(
        r"\b(construction company|general contractor|commercial contractor|"
        r"renovation contractor|custom home builder|home builder|building contractor|"
        r"contractor with|contractor focused|contractor specializing|contractor operating|"
        r"established contractor)\b",
        summary,
        re.I,
    ):
        return ClassificationResult(
            internal_category="General Contractor",
            market_category="General Contractor",
            confidence=0.82,
            method="ai_summary",
        )
    return None


def _first_match(
    texts: list[str],
    blob: str,
    rules: list[dict[str, Any]],
) -> ClassificationResult | None:
    for rule in rules:
        if any(ex.search(blob) for ex in rule["exclude"]):
            continue
        for pattern in rule["patterns"]:
            if any(pattern.search(text) for text in texts):
                confidence = 0.85
                category = rule["category"]
                return ClassificationResult(
                    internal_category=category,
                    market_category=to_market_category(category),
                    confidence=confidence,
                    method="name_keyword",
                )
    return None


def classify_business_type(
    company: Company,
    stats24: dict[str, float | int],
    *,
    reference_date: date | None = None,
) -> ClassificationResult:
    parsed = parse_name(company.name)
    raw_name_texts = [company.name, parsed["dba"], parsed["legal"]]
    name_texts = normalize_match_text([text for text in raw_name_texts if text])
    blob = " | ".join(name_texts).lower()
    summary = (company.ai_summary or "").lower()
    types = company.project_types or []
    projects = company.total_projects or 0
    count24 = int(stats24.get("permit_count_24mo", 0))
    value24 = float(stats24.get("permit_value_24mo", 0))

    for pattern, category in KNOWN_FIRMS:
        if pattern.search(" | ".join(raw_name_texts + name_texts)):
            return ClassificationResult(
                internal_category=category,
                market_category=to_market_category(category),
                confidence=0.9,
                method="known_firm",
            )

    hit = _first_match(name_texts, blob, CLASSIFICATION_RULES)
    if hit:
        if hit.internal_category.lower() in summary:
            hit.confidence = 0.92
        return hit

    ai_gc = _ai_summary_general_contractor(summary)
    if ai_gc:
        return ai_gc

    if "architecture firm" in summary or re.search(r"\barchitects?\b", summary):
        return ClassificationResult("Architect", "Architect", 0.8, "ai_summary")
    if "engineering firm" in summary or "engineering company" in summary:
        return ClassificationResult("Engineering Firm", "Engineering", 0.8, "ai_summary")
    if "building code" in summary:
        return ClassificationResult("Building Code Consultant", "Consultant", 0.8, "ai_summary")
    if "design firm" in summary or "design studio" in summary:
        return ClassificationResult("Design Firm", "Consultant", 0.75, "ai_summary")
    if "permit consultant" in summary or "expeditor" in summary:
        return ClassificationResult("Other", "Consultant", 0.78, "ai_summary")

    person_like = (
        not parsed["has_dba"]
        and len(parsed["legal"].split()) <= 3
        and not BIZ_SUFFIX.search(blob)
    )
    if person_like and (count24 <= 2 or projects <= 2):
        return ClassificationResult(
            "Property Owner / Homeowner",
            "Homeowner",
            0.7,
            "person_name_low_volume",
        )
    if count24 == 1 and value24 < 500_000 and person_like:
        return ClassificationResult(
            "Property Owner / Homeowner",
            "Homeowner",
            0.65,
            "single_permit_person",
        )

    if "developer" in summary or "development company" in summary:
        return ClassificationResult("Developer", "Developer", 0.75, "ai_summary")

    if count24 >= 15 and re.search(r"\bdesign\b", blob) and not re.search(r"\barchitects?\b", blob):
        return ClassificationResult("Design Firm", "Consultant", 0.5, "volume_design_name")
    if (
        "Salvage and Abatement" in types
        and count24 >= 10
        and not re.search(r"\bdesign\b", blob)
        and not re.search(r"\barchitects?\b", blob)
    ):
        return ClassificationResult("Trade Contractor", "Trade Contractor", 0.55, "project_types")

    return ClassificationResult("Unknown", "Unknown", 0.35, "no_match")


def compute_company_lifecycle(last_project_date: str, *, reference_date: date | None = None) -> str:
    today = reference_date or datetime.now(timezone.utc).date()
    if not last_project_date:
        return "unknown"
    try:
        last_date = date.fromisoformat(str(last_project_date)[:10])
    except ValueError:
        return "unknown"
    days = (today - last_date).days
    if days <= ACTIVE_LIFECYCLE_DAYS:
        return "active"
    return "inactive"


def compute_company_tier(
    market_category: str,
    lifecycle: str,
    stats24: dict[str, float | int],
) -> str:
    if lifecycle != "active" or market_category not in GC_TRADE_TYPES:
        return ""
    count24 = int(stats24.get("permit_count_24mo", 0))
    value24 = float(stats24.get("permit_value_24mo", 0))
    if count24 >= 6 and value24 >= 1_000_000:
        return "tier_a"
    if count24 >= 2 and value24 >= 100_000:
        return "tier_b"
    return ""


def compute_enrichment_status(company: Company) -> str:
    has_google = company.google_reviews_count is not None
    has_ai = bool((company.ai_summary or "").strip())
    if has_google and has_ai:
        return "complete"
    if has_google or has_ai:
        return "partial"
    return "pending"


def compute_permit_stats_24mo(session: Session, *, reference_date: date | None = None) -> dict[str, dict[str, float | int]]:
    today = reference_date or datetime.now(timezone.utc).date()
    cutoff = (today - timedelta(days=STATS_WINDOW_DAYS)).isoformat()
    rows = session.execute(
        select(
            Permit.applicant,
            func.count(Permit.id),
            func.sum(func.cast(func.nullif(Permit.project_value, ""), Float)),
        )
        .where(
            Permit.applicant != "",
            Permit.applicant.isnot(None),
            Permit.issue_date >= cutoff,
        )
        .group_by(Permit.applicant)
    ).all()

    stats: dict[str, dict[str, float | int]] = {}
    for applicant, count, value in rows:
        name = (applicant or "").strip()
        if not name:
            continue
        stats[name] = {
            "permit_count_24mo": int(count or 0),
            "permit_value_24mo": float(value or 0),
        }
    return stats


def classify_companies(session: Session) -> int:
    """Classify every company row and persist intelligence fields."""
    print("[Companies] Classifying all companies...")
    reference_date = datetime.now(timezone.utc).date()
    stats24 = compute_permit_stats_24mo(session, reference_date=reference_date)
    default_stats = {"permit_count_24mo": 0, "permit_value_24mo": 0.0}

    classified = 0
    offset = 0
    while True:
        companies = session.scalars(
            select(Company).order_by(Company.id).offset(offset).limit(CLASSIFY_BATCH_SIZE)
        ).all()
        if not companies:
            break

        for company in companies:
            company_stats = stats24.get(company.name, default_stats)
            result = classify_business_type(company, company_stats, reference_date=reference_date)
            lifecycle = compute_company_lifecycle(
                company.last_project_date,
                reference_date=reference_date,
            )
            company.company_type = result.market_category
            company.confidence_score = round(result.confidence, 4)
            company.company_lifecycle = lifecycle
            company.company_tier = compute_company_tier(
                result.market_category,
                lifecycle,
                company_stats,
            )
            company.enrichment_status = compute_enrichment_status(company)
            classified += 1

        session.commit()
        offset += CLASSIFY_BATCH_SIZE

    print(f"[Companies] Classification complete: {classified} companies")
    return classified
