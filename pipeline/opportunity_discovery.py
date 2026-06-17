"""Internal opportunity discovery for company profiles."""

from __future__ import annotations

import logging
import math
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Literal

from sqlalchemy import func, inspect as sa_inspect, or_, select
from sqlalchemy.orm import Session

from config.env import env_flag
from db.connection import session_scope
from db.models import (
    ArchCompany,
    ArchTender,
    CommercialTender,
    Company,
    ContractAward,
    Permit,
    Tender,
)
from pipeline.company_matching import normalize_vendor_name
from pipeline.ai_matching import (
    HYBRID_AI_CANDIDATE_LIMIT,
    HYBRID_INLINE_SCORE_CAP,
    TenderPairCandidate,
    _load_tender_row,
    breakdown_json_to_construction_api_breakdown,
    build_match_reason_from_rules,
    load_fresh_company_tender_matches,
    resolve_hybrid_tender_score,
    score_tender_pairs,
)

from pipeline.scoring.construction_match_scoring import score_construction_match

logger = logging.getLogger(__name__)

Kind = Literal["construction", "architecture"]
OpportunityType = Literal["tender", "permit", "contract_award"]

# Construction Intelligence — independent thresholds (not comparable across types)
CONSTRUCTION_TENDER_AI_THRESHOLD = 65
CONSTRUCTION_TENDER_RULES_THRESHOLD = 50
CONSTRUCTION_TENDER_STRETCH_THRESHOLD = 40
CONSTRUCTION_PERMIT_MARKET_THRESHOLD = 70
CONSTRUCTION_PERMIT_MARKET_STRETCH = 65
CONSTRUCTION_PERMIT_OWN_THRESHOLD = 65
CONSTRUCTION_AWARD_THRESHOLD = 60
CONSTRUCTION_TENDER_RESERVED_SLOTS = 5
CONSTRUCTION_PERMIT_RESERVED_SLOTS = 5
CONSTRUCTION_AWARD_MAX_SLOTS = 3
CONSTRUCTION_OWN_PERMIT_MAX_SLOTS = 2
CONSTRUCTION_OWN_PERMIT_BONUS = 4
CONSTRUCTION_DEFAULT_MIN_SCORE = 50

# Architecture Intelligence — independent thresholds (not comparable to construction)
ARCHITECTURE_DEFAULT_MIN_SCORE = 40
ARCHITECTURE_TENDER_AI_THRESHOLD = 25
ARCHITECTURE_TENDER_STRETCH_THRESHOLD = 25
ARCHITECTURE_PERMIT_MARKET_THRESHOLD = 50
ARCHITECTURE_PERMIT_MARKET_STRETCH = 45
ARCHITECTURE_PERMIT_OWN_THRESHOLD = 55
ARCHITECTURE_OWN_PERMIT_BONUS = 12
ARCHITECTURE_TENDER_RESERVED_SLOTS = 5
ARCHITECTURE_PERMIT_RESERVED_SLOTS = 5

ARCHITECTURE_PERMIT_TYPE_RE = re.compile(
    r"\bnew building\b|\baddition\b|\balteration\b|\brenovation\b|\bdemolition\b|\bheritage\b"
    r"|\bmulti.?family\b|\bmixed.?use\b|\binstitutional\b|\btownhouse\b|\bhousing\b",
    re.I,
)
BC_METRO_GEO_RE = re.compile(
    r"\bbc\b|\bvancouver\b|\bburnaby\b|\brichmond\b|\bsurrey\b|\bvictoria\b|\bkelowna\b|\bnanaimo\b",
    re.I,
)


@dataclass
class RuleTenderCandidate:
    tender_source: str
    tender_id: int
    payload: dict[str, Any]
    rule_score: int
    reasons: list[str]


@dataclass
class DiscoveryReadBundle:
    """ORM data loaded in read phase; entities expunged before session closes."""

    company: Company | ArchCompany
    signals: CompanySignals
    tender_rows: list[tuple[Any, str]]
    permit_rows: list[tuple[Permit, bool]]
    award_rows: list[tuple[ContractAward, str]]
    fresh_cache: dict[tuple[str, int], Any]
    cached_tender_rows: dict[tuple[str, int], Any] = field(default_factory=dict)


@dataclass
class SessionPhaseMetrics:
    read_ms: float = 0.0
    hybrid_write_ms: float = 0.0
    final_db_ms: float = 0.0

    @property
    def db_total_ms(self) -> float:
        return self.read_ms + self.hybrid_write_ms + self.final_db_ms

    def log(self, company_id: int, kind: Kind, cpu_total_ms: float) -> None:
        print(
            f"[OpportunityDiscovery] company={company_id} kind={kind} "
            f"db_phases_total={self.db_total_ms / 1000:.2f}s "
            f"cpu_phases_total={cpu_total_ms / 1000:.2f}s"
        )


def _log_discover_step(
    step: str,
    company_id: int,
    kind: Kind,
    started: float,
    *,
    extra: str = "",
) -> None:
    elapsed = time.perf_counter() - started
    suffix = f" {extra}" if extra else ""
    print(
        f"[OpportunityDiscovery] step={step} company={company_id} kind={kind} "
        f"{elapsed:.3f}s{suffix}"
    )


def _applicant_search_tokens(name: str) -> list[str]:
    tokens: list[str] = []
    for part in re.split(r"\W+", name.lower()):
        if len(part) > 3 and part not in STOP_WORDS:
            tokens.append(part)
    return tokens[:4]


CONSTRUCTION_TITLE_RE = re.compile(
    r"\bconstruction\b|\brenovation\b|\bretrofit\b|\bbuilding\b|\bcivil\b|\binfrastructure\b"
    r"|\broof\b|\bpaving\b|\bhvac\b|\belectrical\b|\bmechanical\b|\btenant\b|\bdemolition\b"
    r"|\bdesign.?build\b|\bgeneral contractor\b",
    re.I,
)
CONSULTING_ONLY_RE = re.compile(
    r"\bconsultant\b|\bprofessional services\b|\bengineering services\b",
    re.I,
)
_NON_CONSTRUCTION_PROCUREMENT_RE = re.compile(
    r"\btruck\b|\bvehicle\b|\bfleet\b|\bwrecker\b|\bbus\b|\bautomobile\b|\bpolice\b|\bambulance\b"
    r"|\bvending\b|\bfood\s+service\b|\bbeverage\b|\bcatering\b|\bmeal\b"
    r"|\bfurniture\b|\buniform\b|\bgenerator\b"
    r"|\bsoftware\s+license\b|\bsaas\b"
    r"|\btimber\s+sale\b|\btimber\s+license\b|\bcutting\s+permit\b",
    re.I,
)
ADDRESS_NOISE_RE = re.compile(
    r"^\d+(st|nd|rd|th)$|^(street|st|avenue|ave|drive|dr|boulevard|blvd|road|rd|way|lane|ln)$",
    re.I,
)

STOP_WORDS = frozenset(
    {
        "the", "and", "ltd", "inc", "dba", "of", "for", "a", "an", "to", "in",
        "no", "not", "with", "by", "on", "or", "co", "corp", "company", "limited",
        "services", "service", "group", "bc", "vancouver",
    }
)

KEYWORD_EXPANSIONS: dict[str, list[str]] = {
    "building": ["construction", "build", "facility", "structure"],
    "alteration": ["renovation", "retrofit", "upgrade", "repair", "restoration"],
    "addition": ["expansion", "extension", "renovation"],
    "demolition": ["deconstruction", "removal", "abatement"],
    "concrete": ["paving", "foundation", "structural", "civil"],
    "electrical": ["electric", "lighting", "power"],
    "plumbing": ["mechanical", "hvac", "pipe"],
}


@dataclass
class CompanySignals:
    name: str
    project_types: list[str]
    neighborhoods: list[str]
    google_address: str
    avg_project_value: float
    avg_award_value: float
    award_categories: list[str]
    award_clients: list[str]
    buyer_levels: list[str]
    ai_reliability_score: int | None
    houzz_project_types: list[str] = field(default_factory=list)
    houzz_service_areas: list[str] = field(default_factory=list)
    normalized_name: str = ""

    @classmethod
    def from_company(cls, company: Company) -> CompanySignals:
        avg_award = float(company.avg_award_value or 0)
        avg_project = float(company.avg_project_value or 0)
        return cls(
            name=company.name,
            project_types=list(company.project_types or []),
            neighborhoods=list(company.neighborhoods or []),
            google_address=company.google_address or "",
            avg_project_value=avg_project,
            avg_award_value=avg_award if avg_award > 0 else avg_project,
            award_categories=list(company.award_categories or []),
            award_clients=list(company.award_clients or []),
            buyer_levels=list(company.buyer_levels or []),
            ai_reliability_score=company.ai_reliability_score,
            normalized_name=normalize_vendor_name(company.name),
        )

    @classmethod
    def from_arch_company(cls, company: ArchCompany) -> CompanySignals:
        return cls(
            name=company.name,
            project_types=list(company.project_types or []),
            neighborhoods=list(company.neighborhoods or []),
            google_address=company.google_address or "",
            avg_project_value=float(company.avg_project_value or 0),
            avg_award_value=float(company.avg_project_value or 0),
            award_categories=list(company.website_specializations or []),
            award_clients=[],
            buyer_levels=[],
            ai_reliability_score=company.ai_reliability_score,
            houzz_project_types=list(company.houzz_project_types or []),
            houzz_service_areas=list(company.houzz_service_areas or []),
            normalized_name=normalize_vendor_name(company.name),
        )


def _tokenize(text: str) -> set[str]:
    tokens = re.split(r"[^a-z0-9]+", (text or "").lower())
    return {t for t in tokens if len(t) > 2 and t not in STOP_WORDS}


def _expand_keywords(roots: set[str]) -> set[str]:
    expanded = set(roots)
    for token in list(roots):
        for synonym in KEYWORD_EXPANSIONS.get(token, []):
            expanded.add(synonym)
    return expanded


def _company_keywords(signals: CompanySignals) -> set[str]:
    roots: set[str] = set()
    for source in (
        [signals.name],
        signals.project_types,
        signals.neighborhoods,
        [signals.google_address],
        signals.award_categories,
        signals.houzz_project_types,
        signals.houzz_service_areas,
    ):
        for text in source:
            roots.update(_tokenize(str(text)))
    return _expand_keywords(roots)


def _tender_company_keywords(signals: CompanySignals) -> set[str]:
    """Construction tender keywords — exclude street-number noise from google_address."""
    roots: set[str] = set()
    for source in (
        [signals.name],
        signals.project_types,
        signals.neighborhoods,
        signals.award_categories,
    ):
        for text in source:
            roots.update(_tokenize(str(text)))
    for token in _tokenize(signals.google_address):
        if token.isdigit() or ADDRESS_NOISE_RE.match(token):
            continue
        roots.add(token)
    return _expand_keywords(roots)


def _parse_value(raw: str | float | None) -> float:
    if raw is None:
        return 0.0
    if isinstance(raw, (int, float)):
        return float(raw)
    try:
        return float(str(raw).replace(",", "").replace("$", "").strip())
    except ValueError:
        return 0.0


def _parse_date(value: str) -> date | None:
    if not value:
        return None
    cleaned = value.replace("/", "-").strip()[:10]
    try:
        return datetime.strptime(cleaned, "%Y-%m-%d").date()
    except ValueError:
        return None


def _value_fit_score(company_avg: float, opportunity_value: float) -> tuple[int, str | None]:
    if company_avg <= 0 or opportunity_value <= 0:
        return 0, None
    ratio = opportunity_value / company_avg
    if 0.5 <= ratio <= 2.0:
        return 15, "Value aligned with company typical project size"
    if 0.25 <= ratio <= 4.0:
        return 9, "Value within company broader range"
    if 0.1 <= ratio <= 10.0:
        return 3, None
    return 0, None


def _reliability_points(signals: CompanySignals, has_relevance: bool) -> int:
    if not has_relevance or signals.ai_reliability_score is None:
        return 0
    return round((signals.ai_reliability_score / 100) * 5)


def _overlap_points(haystack: str, needles: list[str], max_points: int = 20) -> tuple[int, list[str]]:
    hay_tokens = _tokenize(haystack)
    matched: list[str] = []
    for needle in needles:
        if not needle:
            continue
        needle_tokens = _tokenize(needle)
        if not needle_tokens:
            continue
        if needle_tokens & hay_tokens or needle.lower() in haystack.lower():
            matched.append(needle)
    if not matched:
        return 0, []
    return min(max_points, 8 + len(matched) * 4), matched


def _keyword_points(haystack: str, keywords: set[str]) -> tuple[int, list[str]]:
    hay_tokens = _tokenize(haystack)
    matched = sorted(hay_tokens & keywords)
    if not matched:
        return 0, []
    return min(25, 10 + len(matched) * 3), matched


def _is_tender_open(deadline: str) -> bool:
    parsed = _parse_date(deadline)
    if parsed is None:
        return True
    return parsed >= date.today()


def _buyer_relevance_points(signals: CompanySignals, organization: str) -> tuple[int, str | None]:
    org_l = (organization or "").lower().strip()
    if not org_l:
        return 0, None
    for client in signals.award_clients:
        c = client.strip().lower()
        if not c:
            continue
        if c in org_l or org_l in c:
            return 15, f"Known buyer: {client[:60]}"
    for level in signals.buyer_levels:
        if level and level.lower() in org_l:
            return 10, f"Buyer level fit: {level}"
    return 0, None


def _construction_tender_relevance_v1_enabled() -> bool:
    return env_flag("CONSTRUCTION_TENDER_RELEVANCE_V1", default=False)


def _is_non_construction_procurement(title: str) -> bool:
    """True when title clearly indicates goods/vehicles/food/IT/forestry procurement."""
    return bool(_NON_CONSTRUCTION_PROCUREMENT_RE.search(title or ""))


def _tender_row_parsed_value(row: Any, source: str) -> float:
    numeric = getattr(row, "estimated_value_numeric", None)
    if numeric is not None and float(numeric) > 0:
        return float(numeric)
    if source == "federal":
        return _parse_value(getattr(row, "estimated_value", "") or "")
    return _parse_value(getattr(row, "value", "") or "")


def _location_overlaps_neighborhoods(location: str, neighborhoods: list[str]) -> bool:
    loc_tokens = _tokenize(location)
    if not loc_tokens:
        return False
    for neighborhood in neighborhoods:
        if not neighborhood:
            continue
        if loc_tokens & _tokenize(str(neighborhood)):
            return True
    return False


def _score_construction_tender_rules(
    signals: CompanySignals,
    payload: dict[str, Any],
) -> tuple[int, list[str]]:
    """Tender Pursuit Rank (rules fallback) for commercial construction companies."""
    title = payload.get("title", "")
    category = payload.get("category", "")
    org = payload.get("company", "")
    deadline = payload.get("deadline", "")
    value = float(payload.get("value") or 0)
    haystack = " ".join(filter(None, [title, category, org]))
    hay_l = haystack.lower()

    keywords = _tender_company_keywords(signals)
    kw_pts, kw_matched = _keyword_points(haystack, keywords)
    cat_pts, cat_matched = _overlap_points(haystack, signals.project_types + signals.award_categories, 18)
    loc_pts, loc_matched = _overlap_points(haystack, signals.neighborhoods, 10)
    val_pts, val_reason = _value_fit_score(signals.avg_project_value or signals.avg_award_value, value)
    buyer_pts, buyer_reason = _buyer_relevance_points(signals, org)

    trade_hit = bool(CONSTRUCTION_TITLE_RE.search(hay_l))
    cat_hit = any(pt.lower() in hay_l or pt.lower() in category.lower() for pt in signals.project_types if pt)
    scope_pts = 0
    if category.lower() == "construction" and (trade_hit or cat_hit):
        scope_pts = 14
    elif trade_hit and cat_hit:
        scope_pts = 12
    elif trade_hit:
        scope_pts = 8

    fresh_pts = 0
    fresh_reason = None
    parsed = _parse_date(deadline)
    if parsed and (parsed - date.today()).days <= 30:
        fresh_pts = 10
        fresh_reason = "Closing within 30 days"

    has_rel = bool(kw_matched or cat_matched or loc_matched or buyer_pts)
    rel_pts = _reliability_points(signals, has_rel)

    penalty = 0
    if CONSULTING_ONLY_RE.search(hay_l) and not trade_hit:
        penalty = 10

    region_penalty = 0
    if _construction_tender_relevance_v1_enabled():
        neighborhoods = [n for n in signals.neighborhoods if n and str(n).strip()]
        if (
            payload.get("tender_source") == "federal"
            and neighborhoods
            and not _location_overlaps_neighborhoods(payload.get("location") or "", neighborhoods)
        ):
            region_penalty = 10

    score = min(
        100,
        max(
            0,
            kw_pts + cat_pts + loc_pts + val_pts + buyer_pts + scope_pts + rel_pts + fresh_pts
            - penalty
            - region_penalty,
        ),
    )

    reasons: list[str] = []
    if scope_pts >= 12:
        reasons.append("Construction scope alignment")
    if buyer_reason:
        reasons.append(buyer_reason)
    if kw_matched:
        reasons.append(f"Trade keyword match: {', '.join(kw_matched[:4])}")
    if cat_matched:
        reasons.append(f"Category fit: {', '.join(cat_matched[:3])}")
    if loc_matched:
        reasons.append(f"Location overlap: {', '.join(loc_matched[:3])}")
    if val_reason:
        reasons.append(val_reason)
    if fresh_reason:
        reasons.append(fresh_reason)
    return score, reasons or ["Open construction tender"]


def _score_tender(signals: CompanySignals, haystack: str, value: float, deadline: str) -> tuple[int, list[str]]:
    """Legacy unified tender scorer (architecture / fallback)."""
    keywords = _company_keywords(signals)
    kw_pts, kw_matched = _keyword_points(haystack, keywords)
    cat_pts, cat_matched = _overlap_points(haystack, signals.project_types + signals.award_categories, 18)
    loc_pts, loc_matched = _overlap_points(haystack, signals.neighborhoods + [signals.google_address], 15)
    val_pts, val_reason = _value_fit_score(signals.avg_project_value or signals.avg_award_value, value)
    has_rel = bool(kw_matched or cat_matched or loc_matched)
    rel_pts = _reliability_points(signals, has_rel)
    fresh_pts = 0
    fresh_reason = None
    parsed = _parse_date(deadline)
    if parsed and (parsed - date.today()).days <= 30:
        fresh_pts = 10
        fresh_reason = "Closing within 30 days"

    score = min(100, kw_pts + cat_pts + loc_pts + val_pts + rel_pts + fresh_pts)
    reasons: list[str] = []
    if kw_matched:
        reasons.append(f"Keyword match: {', '.join(kw_matched[:4])}")
    if cat_matched:
        reasons.append(f"Category fit: {', '.join(cat_matched[:3])}")
    if loc_matched:
        reasons.append(f"Location overlap: {', '.join(loc_matched[:3])}")
    if val_reason:
        reasons.append(val_reason)
    if fresh_reason:
        reasons.append(fresh_reason)
    return score, reasons


def _permit_haystack(permit: Permit) -> str:
    return " ".join(
        filter(None, [permit.permit_type, permit.address, permit.description, permit.applicant])
    )


def _score_construction_permit(
    signals: CompanySignals,
    permit: Permit,
    *,
    own: bool,
) -> tuple[int, list[str]]:
    haystack = _permit_haystack(permit)
    keywords = _company_keywords(signals)
    kw_pts, kw_matched = _keyword_points(haystack, keywords)
    cat_pts, cat_matched = _overlap_points(haystack, signals.project_types, 20)
    loc_pts, loc_matched = _overlap_points(haystack, signals.neighborhoods + [signals.google_address], 15)
    value = _parse_value(permit.project_value)
    val_pts, val_reason = _value_fit_score(signals.avg_project_value, value)
    base = CONSTRUCTION_OWN_PERMIT_BONUS if own else 0
    score = min(100, base + kw_pts + cat_pts + loc_pts + val_pts)
    reasons: list[str] = []
    if own:
        reasons.append("Company permit history")
    else:
        reasons.append("Comparable market permit activity")
    if cat_matched:
        reasons.append(f"Permit type fit: {', '.join(cat_matched[:3])}")
    if loc_matched:
        reasons.append(f"Area overlap: {', '.join(loc_matched[:3])}")
    if kw_matched:
        reasons.append(f"Trade keyword match: {', '.join(kw_matched[:3])}")
    if val_reason:
        reasons.append(val_reason)
    return score, reasons


def _score_architecture_permit(
    signals: CompanySignals,
    permit: Permit,
    *,
    own: bool,
) -> tuple[int, list[str]]:
    haystack = _permit_haystack(permit)
    keywords = _company_keywords(signals)
    kw_pts, kw_matched = _keyword_points(haystack, keywords)
    spec_pts, spec_matched = _overlap_points(
        haystack,
        signals.project_types + signals.award_categories + signals.houzz_project_types,
        22,
    )
    loc_pts, loc_matched = _overlap_points(
        haystack,
        signals.neighborhoods + signals.houzz_service_areas + [signals.google_address],
        18,
    )
    value = _parse_value(permit.project_value)
    val_pts, val_reason = _value_fit_score(signals.avg_project_value, value)
    type_pts = 20 if ARCHITECTURE_PERMIT_TYPE_RE.search(haystack) else 0
    geo_pts = 0
    geo_reason: str | None = None
    if signals.google_address and BC_METRO_GEO_RE.search(haystack) and BC_METRO_GEO_RE.search(signals.google_address):
        geo_pts = 12
        geo_reason = "Regional design-market permit activity"
    base = ARCHITECTURE_OWN_PERMIT_BONUS if own else 0
    score = min(100, base + kw_pts + spec_pts + loc_pts + val_pts + type_pts + geo_pts)
    reasons: list[str] = []
    if own:
        reasons.append("Company permit history")
    else:
        reasons.append("Comparable market permit activity")
    if type_pts:
        reasons.append("Design-relevant permit type")
    if geo_reason:
        reasons.append(geo_reason)
    if spec_matched:
        reasons.append(f"Practice fit: {', '.join(spec_matched[:3])}")
    if loc_matched:
        reasons.append(f"Area overlap: {', '.join(loc_matched[:3])}")
    if kw_matched:
        reasons.append(f"Keyword match: {', '.join(kw_matched[:3])}")
    if val_reason:
        reasons.append(val_reason)
    return score, reasons


def _score_contract_award(
    signals: CompanySignals,
    award: ContractAward,
    *,
    context: str,
) -> tuple[int, list[str]]:
    haystack = " ".join(
        filter(
            None,
            [
                award.title,
                award.description,
                award.procurement_category,
                award.buyer_organization,
                award.winner_company,
            ],
        )
    )
    keywords = _company_keywords(signals)
    kw_pts, kw_matched = _keyword_points(haystack, keywords)
    cat_pts, cat_matched = _overlap_points(
        haystack, signals.project_types + signals.award_categories, 20
    )
    client_pts, client_matched = _overlap_points(haystack, signals.award_clients, 18)
    buyer_pts, buyer_matched = _overlap_points(haystack, signals.buyer_levels, 10)
    value = float(award.award_value or 0)
    val_pts, val_reason = _value_fit_score(signals.avg_award_value, value)
    context_bonus = {"own_history": 15, "peer_award": 8, "client_history": 12}.get(context, 5)
    score = min(100, context_bonus + kw_pts + cat_pts + client_pts + buyer_pts + val_pts)
    reasons: list[str] = []
    if context == "own_history":
        reasons.append("Company contract award history")
    elif context == "peer_award":
        reasons.append("Similar company award in same category")
    elif context == "client_history":
        reasons.append("Award from a known client/buyer")
    if cat_matched:
        reasons.append(f"Category fit: {', '.join(cat_matched[:3])}")
    if client_matched:
        reasons.append(f"Client overlap: {', '.join(client_matched[:2])}")
    if buyer_matched:
        reasons.append(f"Buyer level fit: {', '.join(buyer_matched[:2])}")
    if kw_matched:
        reasons.append(f"Keyword match: {', '.join(kw_matched[:3])}")
    if val_reason:
        reasons.append(val_reason)
    return score, reasons


def _tender_payload(row: Any, source: str) -> dict[str, Any]:
    relevance_v1 = _construction_tender_relevance_v1_enabled()
    if source == "federal":
        org = row.organization
        deadline = row.closing_date
        value = (
            _tender_row_parsed_value(row, source)
            if relevance_v1
            else _parse_value(row.estimated_value)
        )
        budget = (row.ai_budget_estimate or "").strip() or None
    elif source == "commercial":
        org = row.company
        deadline = row.deadline
        value = (
            _tender_row_parsed_value(row, source)
            if relevance_v1
            else _parse_value(row.value)
        )
        budget = (row.ai_budget_estimate or "").strip() or None
    else:
        org = row.company
        deadline = row.deadline
        value = _parse_value(row.value)
        budget = (row.ai_budget_estimate or "").strip() or None

    payload: dict[str, Any] = {
        "id": row.id,
        "title": row.title,
        "company": org or "",
        "value": value,
        "deadline": (deadline or "").replace("/", "-")[:10],
        "category": row.category or "Uncategorized",
        "budget_estimate": budget,
        "url": getattr(row, "url", "") or "",
        "tender_source": source,
    }
    if relevance_v1 and source == "federal":
        payload["location"] = getattr(row, "location", "") or ""
    return payload


def _permit_payload(permit: Permit) -> dict[str, Any]:
    return {
        "id": permit.id,
        "address": permit.address,
        "type": permit.permit_type,
        "value": _parse_value(permit.project_value),
        "date": (permit.issue_date or "").replace("/", "-")[:10],
        "status": "Issued" if permit.issue_date else "Pending",
        "applicant": permit.applicant,
        "description": permit.description,
    }


def _award_payload(award: ContractAward) -> dict[str, Any]:
    return {
        "id": award.id,
        "title": award.title,
        "award_date": (award.award_date or "").replace("/", "-")[:10],
        "client": award.buyer_organization,
        "category": award.procurement_category,
        "value": float(award.award_value) if award.award_value is not None else None,
        "currency": award.currency or "CAD",
        "winner_company": award.winner_company,
        "source": award.source,
        "url": award.url,
        "buyer_level": award.buyer_level,
    }


def _load_tender_candidates(session: Session, kind: Kind, limit: int) -> list[tuple[Any, str]]:
    rows: list[tuple[Any, str]] = []
    if kind == "construction":
        relevance_v1 = _construction_tender_relevance_v1_enabled()
        federal = session.scalars(select(Tender).order_by(Tender.id.desc()).limit(limit)).all()
        commercial = session.scalars(
            select(CommercialTender).order_by(CommercialTender.id.desc()).limit(limit)
        ).all()
        rows.extend((row, "federal") for row in federal)
        rows.extend((row, "commercial") for row in commercial)
        if relevance_v1:
            before = len(rows)
            rows = [
                (row, source)
                for row, source in rows
                if not _is_non_construction_procurement(row.title or "")
            ]
            logger.info("F005 pool: %d → %d after exclusion filter", before, len(rows))
    else:
        arch = session.scalars(select(ArchTender).order_by(ArchTender.id.desc()).limit(limit)).all()
        rows.extend((row, "arch") for row in arch)
    return rows


def _safe_expunge(session: Session, instance: Any) -> None:
    """Expunge only if instance is still bound to this session (avoids double-expunge)."""
    if sa_inspect(instance).session is session:
        session.expunge(instance)


def _finalize_read_bundle(session: Session, bundle: DiscoveryReadBundle) -> None:
    """Detach ORM entities so CPU phases can run without a checked-out connection."""
    _safe_expunge(session, bundle.company)
    for row, _ in bundle.tender_rows:
        _safe_expunge(session, row)
    for permit, _ in bundle.permit_rows:
        _safe_expunge(session, permit)
    for award, _ in bundle.award_rows:
        _safe_expunge(session, award)
    for row in bundle.fresh_cache.values():
        _safe_expunge(session, row)
    for row in bundle.cached_tender_rows.values():
        _safe_expunge(session, row)


def _load_construction_read_bundle(
    session: Session,
    company_id: int,
    max_candidates: int,
) -> DiscoveryReadBundle:
    kind: Kind = "construction"
    started = time.perf_counter()
    company = session.get(Company, company_id)
    if company is None:
        raise ValueError(f"Company {company_id} not found")
    _log_discover_step("read.company", company_id, kind, started)

    signals_started = time.perf_counter()
    signals = CompanySignals.from_company(company)
    _log_discover_step("read.signals", company_id, kind, signals_started)

    tender_started = time.perf_counter()
    tender_rows = _load_tender_candidates(session, "construction", max_candidates)
    _log_discover_step(
        "read.tenders", company_id, kind, tender_started, extra=f"rows={len(tender_rows)}"
    )

    permit_started = time.perf_counter()
    permit_rows = _load_permit_candidates(session, signals, max_candidates // 2)
    _log_discover_step(
        "read.permits", company_id, kind, permit_started, extra=f"rows={len(permit_rows)}"
    )

    award_started = time.perf_counter()
    award_rows = _load_award_candidates(session, company, max_candidates // 2)
    _log_discover_step(
        "read.awards", company_id, kind, award_started, extra=f"rows={len(award_rows)}"
    )

    matches_started = time.perf_counter()
    fresh_cache = {
        (row.tender_source, row.tender_id): row
        for row in load_fresh_company_tender_matches(
            session, company_kind="construction", company_id=company_id
        )
    }
    _log_discover_step(
        "read.tender_matches",
        company_id,
        kind,
        matches_started,
        extra=f"rows={len(fresh_cache)}",
    )
    return DiscoveryReadBundle(
        company=company,
        signals=signals,
        tender_rows=tender_rows,
        permit_rows=permit_rows,
        award_rows=award_rows,
        fresh_cache=fresh_cache,
    )


def _load_architecture_read_bundle(
    session: Session,
    company_id: int,
    max_candidates: int,
) -> DiscoveryReadBundle:
    kind: Kind = "architecture"
    started = time.perf_counter()
    company = session.get(ArchCompany, company_id)
    if company is None:
        raise ValueError(f"Architecture company {company_id} not found")
    _log_discover_step("read.company", company_id, kind, started)

    signals_started = time.perf_counter()
    signals = CompanySignals.from_arch_company(company)
    _log_discover_step("read.signals", company_id, kind, signals_started)

    tender_started = time.perf_counter()
    tender_rows = _load_tender_candidates(session, "architecture", max_candidates)
    _log_discover_step(
        "read.tenders", company_id, kind, tender_started, extra=f"rows={len(tender_rows)}"
    )

    permit_started = time.perf_counter()
    permit_rows = _load_permit_candidates(session, signals, max_candidates // 2)
    _log_discover_step(
        "read.permits", company_id, kind, permit_started, extra=f"rows={len(permit_rows)}"
    )

    matches_started = time.perf_counter()
    fresh_cache = {
        (row.tender_source, row.tender_id): row
        for row in load_fresh_company_tender_matches(
            session, company_kind="architecture", company_id=company_id
        )
    }
    _log_discover_step(
        "read.tender_matches",
        company_id,
        kind,
        matches_started,
        extra=f"rows={len(fresh_cache)}",
    )

    batch_started = time.perf_counter()
    cache_keys = list(fresh_cache.keys())
    cached_tender_rows = _batch_load_tender_rows(session, cache_keys) if cache_keys else {}
    _log_discover_step(
        "read.cached_tenders",
        company_id,
        kind,
        batch_started,
        extra=f"rows={len(cached_tender_rows)}",
    )
    return DiscoveryReadBundle(
        company=company,
        signals=signals,
        tender_rows=tender_rows,
        permit_rows=permit_rows,
        award_rows=[],
        fresh_cache=fresh_cache,
        cached_tender_rows=cached_tender_rows,
    )


def _scan_construction_rule_tenders_from_rows(
    tender_rows: list[tuple[Any, str]],
    signals: CompanySignals,
) -> list[RuleTenderCandidate]:
    results: list[RuleTenderCandidate] = []
    for row, source in tender_rows:
        deadline = getattr(row, "closing_date", None) or getattr(row, "deadline", "") or ""
        if not _is_tender_open(deadline):
            continue
        payload = _tender_payload(row, source)
        score, reasons = _score_construction_tender_rules(signals, payload)
        results.append(
            RuleTenderCandidate(
                tender_source=source,
                tender_id=payload["id"],
                payload=payload,
                rule_score=score,
                reasons=reasons,
            )
        )
    return results


def _scan_architecture_rule_tenders_from_rows(
    tender_rows: list[tuple[Any, str]],
    signals: CompanySignals,
) -> list[RuleTenderCandidate]:
    results: list[RuleTenderCandidate] = []
    for row, source in tender_rows:
        deadline = getattr(row, "closing_date", None) or getattr(row, "deadline", "") or ""
        if not _is_tender_open(deadline):
            continue
        if source != "arch":
            continue
        payload = _tender_payload(row, source)
        haystack = " ".join(
            filter(
                None,
                [payload["title"], payload["category"], payload["company"], payload.get("deadline", "")],
            )
        )
        score, reasons = _score_tender(signals, haystack, payload["value"], payload["deadline"])
        results.append(
            RuleTenderCandidate(
                tender_source=source,
                tender_id=payload["id"],
                payload=payload,
                rule_score=score,
                reasons=reasons or ["General market opportunity"],
            )
        )
    return results


def _scan_construction_rule_tenders(
    session: Session,
    signals: CompanySignals,
    max_candidates: int,
) -> list[RuleTenderCandidate]:
    return _scan_construction_rule_tenders_from_rows(
        _load_tender_candidates(session, "construction", max_candidates),
        signals,
    )


def _scan_architecture_rule_tenders(
    session: Session,
    signals: CompanySignals,
    max_candidates: int,
) -> list[RuleTenderCandidate]:
    return _scan_architecture_rule_tenders_from_rows(
        _load_tender_candidates(session, "architecture", max_candidates),
        signals,
    )


def _run_hybrid_tender_scoring(
    session: Session,
    company: Company | ArchCompany,
    kind: Kind,
    rule_candidates: list[RuleTenderCandidate],
    *,
    inline_cap: int = HYBRID_INLINE_SCORE_CAP,
    cached_matches: dict[tuple[str, int], Any] | None = None,
) -> dict[str, Any]:
    """Send rule top-N to Haiku scorer (cache-aware, capped). Never raises — rules remain fallback."""
    if not rule_candidates:
        return {
            "cache_hits": 0,
            "freshly_scored": 0,
            "skipped_cap": 0,
            "skipped_no_key": 0,
            "api_errors": 0,
            "api_key_missing": False,
            "candidates_considered": 0,
        }

    top = sorted(rule_candidates, key=lambda item: item.rule_score, reverse=True)[:HYBRID_AI_CANDIDATE_LIMIT]
    pair_candidates = [
        TenderPairCandidate(
            tender_source=item.tender_source,
            tender_id=item.tender_id,
            match_reason=build_match_reason_from_rules(item.reasons),
        )
        for item in top
    ]
    try:
        result = score_tender_pairs(
            session,
            company,
            kind,
            pair_candidates,
            persist=True,
            inline_cap=inline_cap,
            cached_matches=cached_matches,
        )
    except Exception as exc:
        print(f"[Hybrid Matching] Scoring skipped for company={company.id} kind={kind}: {exc}")
        return {
            "cache_hits": 0,
            "freshly_scored": 0,
            "skipped_cap": 0,
            "skipped_no_key": 0,
            "api_errors": 1,
            "api_key_missing": False,
            "candidates_considered": len(pair_candidates),
        }

    result["candidates_considered"] = len(pair_candidates)
    return result


def _rule_tenders_to_opportunity_items(
    session: Session | None,
    company_id: int,
    kind: Kind,
    rule_candidates: list[RuleTenderCandidate],
    *,
    rules_threshold: int,
    stretch_threshold: int,
    hybrid_pairs: dict[tuple[str, int], dict[str, Any]] | None = None,
    ai_rules_threshold: int | None = None,
    ai_stretch_threshold: int | None = None,
    fresh_cache: dict[tuple[str, int], Any] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    matches: list[dict[str, Any]] = []
    stretch: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    ai_match_floor = ai_rules_threshold if ai_rules_threshold is not None else rules_threshold
    ai_stretch_floor = ai_stretch_threshold if ai_stretch_threshold is not None else stretch_threshold
    if fresh_cache is None:
        if session is None:
            raise ValueError("session required when fresh_cache is not provided")
        fresh_cache = {
            (row.tender_source, row.tender_id): row
            for row in load_fresh_company_tender_matches(session, company_kind=kind, company_id=company_id)
        }

    for candidate in rule_candidates:
        key = (candidate.tender_source, candidate.tender_id)
        if key in seen:
            continue
        seen.add(key)

        score, reasons, source = resolve_hybrid_tender_score(
            session,
            company_kind=kind,
            company_id=company_id,
            tender_source=candidate.tender_source,
            tender_id=candidate.tender_id,
            rule_score=candidate.rule_score,
            rule_reasons=candidate.reasons,
            hybrid_pairs=hybrid_pairs,
            cached_matches=fresh_cache,
        )
        item = _tender_opportunity_item(candidate.payload, key, score, reasons, source)
        match_floor = ai_match_floor if source == "ai_match" else rules_threshold
        stretch_floor = ai_stretch_floor if source == "ai_match" else stretch_threshold
        if score >= match_floor:
            matches.append(item)
        elif score >= stretch_floor:
            item["context"] = "stretch_tender"
            stretch.append(item)

    return matches, stretch


def _tender_opportunity_item(
    payload: dict[str, Any],
    key: tuple[str, int],
    score: int,
    reasons: list[str],
    source: str,
) -> dict[str, Any]:
    item = {
        "type": "tender",
        "id": payload["id"],
        "score": score,
        "reasons": reasons,
        "source": source,
        "context": "cached_tender_match" if source == "ai_match" else "open_tender",
        "payload": payload,
        "_tender_key": key,
    }
    return item


def _apply_construction_pair_breakdown(
    item: dict[str, Any],
    key: tuple[str, int],
    *,
    hybrid_pairs: dict[tuple[str, int], dict[str, Any]] | None,
    fresh_cache: dict[tuple[str, int], Any] | None,
) -> None:
    """Copy hybrid or cached breakdown onto an item; skip if already present."""
    if item.get("breakdown"):
        return
    if hybrid_pairs and key in hybrid_pairs:
        pair = hybrid_pairs[key]
        breakdown = pair.get("breakdown")
        if breakdown:
            item["score"] = int(pair["score"])
            item["breakdown"] = breakdown
            reasoning = (pair.get("reasoning") or "").strip()
            if reasoning:
                item["reasons"] = [reasoning[:240]]
            return
    if fresh_cache and key in fresh_cache:
        row = fresh_cache[key]
        if row.breakdown_json:
            item["score"] = row.score
            item["breakdown"] = breakdown_json_to_construction_api_breakdown(row.breakdown_json)
            reasoning = (row.reasoning or "").strip()
            if reasoning:
                item["reasons"] = [reasoning[:240]]


def _batch_load_tender_rows(
    session: Session,
    keys: list[tuple[str, int]],
) -> dict[tuple[str, int], Tender | CommercialTender | ArchTender]:
    """Load tender rows for many (source, id) pairs in one query per source table."""
    by_source: dict[str, set[int]] = {}
    for source, tender_id in keys:
        by_source.setdefault(source, set()).add(tender_id)

    loaded: dict[tuple[str, int], Tender | CommercialTender | ArchTender] = {}
    federal_ids = by_source.get("federal") or set()
    if federal_ids:
        for row in session.scalars(select(Tender).where(Tender.id.in_(federal_ids))).all():
            loaded[("federal", row.id)] = row
    commercial_ids = by_source.get("commercial") or set()
    if commercial_ids:
        for row in session.scalars(
            select(CommercialTender).where(CommercialTender.id.in_(commercial_ids))
        ).all():
            loaded[("commercial", row.id)] = row
    arch_ids = by_source.get("arch") or set()
    if arch_ids:
        for row in session.scalars(select(ArchTender).where(ArchTender.id.in_(arch_ids))).all():
            loaded[("arch", row.id)] = row
    return loaded


def _attach_final_construction_tender_breakdowns(
    session: Session,
    company: Company,
    items: list[dict[str, Any]],
    *,
    hybrid_pairs: dict[tuple[str, int], dict[str, Any]] | None,
    fresh_cache: dict[tuple[str, int], Any] | None = None,
) -> int:
    """Attach breakdowns only to final returned construction tender items."""
    if fresh_cache is None:
        fresh_cache = {
            (row.tender_source, row.tender_id): row
            for row in load_fresh_company_tender_matches(
                session, company_kind="construction", company_id=company.id
            )
        }
    tender_items: list[dict[str, Any]] = []
    for item in items:
        if item.get("type") != "tender":
            continue
        key = item.get("_tender_key")
        if not key:
            payload = item.get("payload") or {}
            source = str(payload.get("tender_source") or "federal")
            tender_id = int(payload.get("id") or item.get("id") or 0)
            key = (source, tender_id)
            item["_tender_key"] = key
        _apply_construction_pair_breakdown(
            item,
            key,
            hybrid_pairs=hybrid_pairs,
            fresh_cache=fresh_cache,
        )
        tender_items.append(item)

    _fill_missing_construction_breakdowns(session, company, tender_items)
    return sum(1 for item in tender_items if item.get("breakdown"))


def _fill_missing_construction_breakdowns(
    session: Session,
    company: Company,
    items: list[dict[str, Any]],
) -> None:
    """Score only tender items still missing breakdown after hybrid/cache enrichment."""
    missing_keys: list[tuple[str, int]] = []
    items_by_key: dict[tuple[str, int], list[dict[str, Any]]] = {}

    for item in items:
        if item.get("type") != "tender" or item.get("breakdown"):
            continue
        key = item.get("_tender_key")
        if not key:
            payload = item.get("payload") or {}
            source = str(payload.get("tender_source") or "federal")
            tender_id = int(payload.get("id") or item.get("id") or 0)
            key = (source, tender_id)
            item["_tender_key"] = key
        if key not in items_by_key:
            missing_keys.append(key)
            items_by_key[key] = []
        items_by_key[key].append(item)

    if not missing_keys:
        return

    tenders = _batch_load_tender_rows(session, missing_keys)
    for key, group in items_by_key.items():
        tender = tenders.get(key)
        if tender is None:
            continue
        scored = score_construction_match(company, tender, key[0])
        for item in group:
            item["score"] = scored.score
            item["breakdown"] = scored.api_breakdown
            if scored.match_reason:
                item["reasons"] = [scored.match_reason[:240]]


def _cached_ai_tenders_to_opportunity_items(
    session: Session | None,
    company_id: int,
    kind: Kind,
    *,
    rules_threshold: int,
    stretch_threshold: int,
    seen_keys: set[tuple[str, int]],
    ai_rules_threshold: int | None = None,
    ai_stretch_threshold: int | None = None,
    fresh_cache: dict[tuple[str, int], Any] | None = None,
    cached_tender_rows: dict[tuple[str, int], Any] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Surface fresh tender_matches rows that were not in the rule scan."""
    matches: list[dict[str, Any]] = []
    stretch: list[dict[str, Any]] = []
    ai_match_floor = ai_rules_threshold if ai_rules_threshold is not None else rules_threshold
    ai_stretch_floor = ai_stretch_threshold if ai_stretch_threshold is not None else stretch_threshold

    if fresh_cache is None:
        if session is None:
            raise ValueError("session required when fresh_cache is not provided")
        fresh_rows = load_fresh_company_tender_matches(
            session, company_kind=kind, company_id=company_id
        )
    else:
        fresh_rows = fresh_cache.values()

    for row in fresh_rows:
        key = (row.tender_source, row.tender_id)
        if key in seen_keys:
            continue

        if cached_tender_rows is not None:
            tender = cached_tender_rows.get(key)
        elif session is not None:
            tender = _load_tender_row(session, row.tender_source, row.tender_id)
        else:
            continue
        if tender is None:
            continue
        deadline = getattr(tender, "closing_date", None) or getattr(tender, "deadline", "") or ""
        if not _is_tender_open(deadline):
            continue

        payload = _tender_payload(tender, row.tender_source)
        reasoning = (row.reasoning or "").strip()
        reasons = [reasoning[:240]] if reasoning else ["Cached AI tender match"]
        item = _tender_opportunity_item(payload, key, row.score, reasons, "ai_match")
        seen_keys.add(key)

        if row.score >= ai_match_floor:
            matches.append(item)
        elif row.score >= ai_stretch_floor:
            item["context"] = "stretch_tender"
            stretch.append(item)

    return matches, stretch


def _load_permit_candidates(session: Session, signals: CompanySignals, limit: int) -> list[tuple[Permit, bool]]:
    results: list[tuple[Permit, bool]] = []
    seen: set[int] = set()

    if signals.normalized_name:
        tokens = _applicant_search_tokens(signals.name)
        own_query = select(Permit).where(Permit.applicant != "")
        if tokens:
            own_query = own_query.where(
                or_(*[func.lower(Permit.applicant).contains(token) for token in tokens])
            )
        own_query = own_query.order_by(Permit.id.desc()).limit(limit)
        for permit in session.scalars(own_query).all():
            if permit.id in seen:
                continue
            if normalize_vendor_name(permit.applicant) == signals.normalized_name:
                results.append((permit, True))
                seen.add(permit.id)

    type_terms = [t.lower() for t in signals.project_types if t]
    # Use PK-ordered scan + Python filter — OR of LOWER(permit_type) LIKE '%term%'
    # cannot use btree indexes and can scan the full permits table.
    scan_limit = limit * 4 if not type_terms else limit * 8
    market_query = select(Permit).order_by(Permit.id.desc()).limit(scan_limit)
    for permit in session.scalars(market_query).all():
        if permit.id in seen:
            continue
        if type_terms:
            permit_type = (permit.permit_type or "").lower()
            if not any(term in permit_type for term in type_terms[:6]):
                continue
        own = (
            signals.normalized_name != ""
            and normalize_vendor_name(permit.applicant) == signals.normalized_name
        )
        results.append((permit, own))
        seen.add(permit.id)
        if len(results) >= limit:
            break
    return results[:limit]


def _load_award_candidates(session: Session, company: Company, limit: int) -> list[tuple[ContractAward, str]]:
    results: list[tuple[ContractAward, str]] = []
    seen: set[int] = set()

    def add(award: ContractAward, context: str) -> None:
        if award.id in seen:
            return
        results.append((award, context))
        seen.add(award.id)

    for award in session.scalars(
        select(ContractAward)
        .where(ContractAward.company_id == company.id)
        .order_by(ContractAward.award_date.desc(), ContractAward.id.desc())
        .limit(min(limit, 100))
    ).all():
        add(award, "own_history")

    categories = [c for c in (company.award_categories or []) if c]
    if categories:
        peer_ids = session.scalars(
            select(Company.id)
            .where(
                Company.id != company.id,
                Company.award_categories.op("&&")(categories),
            )
            .limit(40)
        ).all()
        if peer_ids:
            for award in session.scalars(
                select(ContractAward)
                .where(
                    ContractAward.company_id.in_(peer_ids),
                    ContractAward.company_id.isnot(None),
                )
                .order_by(ContractAward.award_date.desc(), ContractAward.id.desc())
                .limit(limit)
            ).all():
                add(award, "peer_award")

    clients = [c.strip().lower() for c in (company.award_clients or []) if c.strip()]
    if clients:
        client_clauses = [
            func.lower(ContractAward.buyer_organization).contains(client)
            for client in clients[:8]
        ]
        for award in session.scalars(
            select(ContractAward)
            .where(or_(*client_clauses))
            .order_by(ContractAward.award_date.desc(), ContractAward.id.desc())
            .limit(limit)
        ).all():
            add(award, "client_history")
            if len(results) >= limit * 2:
                break

    if len(results) < limit // 2 and categories:
        for award in session.scalars(
            select(ContractAward)
            .where(ContractAward.procurement_category.in_(categories[:10]))
            .order_by(ContractAward.award_date.desc(), ContractAward.id.desc())
            .limit(limit)
        ).all():
            add(award, "peer_award")

    return results[: limit * 2]


def _match_sort_key(item: dict[str, Any]) -> tuple[int, float]:
    return item["score"], item.get("payload", {}).get("value") or 0


def _percentile_rank(score: int, scores: list[int]) -> float:
    if not scores:
        return 0.0
    below = sum(1 for s in scores if s < score)
    return (below / len(scores)) * 100.0


def _business_value_index(
    item: dict[str, Any],
    *,
    tender_scores: list[int],
    permit_scores: list[int],
    award_scores: list[int],
) -> float:
    score = item["score"]
    typ = item["type"]
    if typ == "tender":
        pct = _percentile_rank(score, tender_scores)
        weight = 0.50
    elif typ == "permit":
        pct = _percentile_rank(score, permit_scores)
        weight = 0.30
    else:
        pct = _percentile_rank(score, award_scores)
        weight = 0.20

    value = float(item.get("payload", {}).get("value") or 0)
    value_bonus = min(10.0, math.log10(value + 1) * 2) if value > 0 else 0.0
    urgency = 0.0
    if typ == "tender":
        parsed = _parse_date(str(item.get("payload", {}).get("deadline", "")))
        if parsed and (parsed - date.today()).days <= 30:
            urgency = 5.0

    return pct * weight + value_bonus + urgency


def _assemble_construction_opportunities(
    tenders: list[dict[str, Any]],
    permits: list[dict[str, Any]],
    awards: list[dict[str, Any]],
    *,
    limit: int = 15,
    tender_stretch: list[dict[str, Any]] | None = None,
    permit_stretch: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Reserve tender and permit slots; backfill by business value index."""
    tender_stretch = tender_stretch or []
    permit_stretch = permit_stretch or []
    tenders = sorted(tenders, key=_match_sort_key, reverse=True)
    stretch_tenders = sorted(tender_stretch, key=_match_sort_key, reverse=True)
    market_permits = sorted(
        [p for p in permits if p.get("context") != "own_permit"],
        key=_match_sort_key,
        reverse=True,
    )
    own_permits = sorted(
        [p for p in permits if p.get("context") == "own_permit"],
        key=_match_sort_key,
        reverse=True,
    )
    stretch_market = sorted(
        [p for p in (permit_stretch or []) if p.get("context") != "own_permit"],
        key=_match_sort_key,
        reverse=True,
    )
    stretch_own = sorted(
        [p for p in (permit_stretch or []) if p.get("context") == "own_permit"],
        key=_match_sort_key,
        reverse=True,
    )
    awards = sorted(awards, key=_match_sort_key, reverse=True)

    selected: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()

    def add_item(item: dict[str, Any]) -> bool:
        key = (item["type"], item["id"])
        if key in seen:
            return False
        selected.append(item)
        seen.add(key)
        return True

    for item in tenders[:CONSTRUCTION_TENDER_RESERVED_SLOTS]:
        add_item(item)

    if len(selected) < CONSTRUCTION_TENDER_RESERVED_SLOTS:
        for item in tenders[CONSTRUCTION_TENDER_RESERVED_SLOTS:]:
            if len([x for x in selected if x["type"] == "tender"]) >= CONSTRUCTION_TENDER_RESERVED_SLOTS:
                break
            add_item(item)

    if len([x for x in selected if x["type"] == "tender"]) < CONSTRUCTION_TENDER_RESERVED_SLOTS:
        for item in stretch_tenders:
            if len([x for x in selected if x["type"] == "tender"]) >= CONSTRUCTION_TENDER_RESERVED_SLOTS:
                break
            add_item(item)

    permit_count = sum(1 for x in selected if x["type"] == "permit")
    own_count = sum(1 for x in selected if x.get("context") == "own_permit")
    permit_pool = market_permits + own_permits
    for item in permit_pool:
        if permit_count >= CONSTRUCTION_PERMIT_RESERVED_SLOTS:
            break
        if item.get("context") == "own_permit":
            if own_count >= CONSTRUCTION_OWN_PERMIT_MAX_SLOTS:
                continue
            own_count += 1
        if add_item(item):
            permit_count += 1

    if permit_count < CONSTRUCTION_PERMIT_RESERVED_SLOTS:
        for item in stretch_market + stretch_own:
            if permit_count >= CONSTRUCTION_PERMIT_RESERVED_SLOTS:
                break
            if item.get("context") == "own_permit":
                if own_count >= CONSTRUCTION_OWN_PERMIT_MAX_SLOTS:
                    continue
                own_count += 1
            if add_item(item):
                permit_count += 1

    award_count = 0
    for item in awards:
        if award_count >= CONSTRUCTION_AWARD_MAX_SLOTS:
            break
        if add_item(item):
            award_count += 1

    if len(selected) >= limit:
        return selected[:limit]

    tender_scores = [t["score"] for t in tenders + stretch_tenders]
    permit_scores = [p["score"] for p in permits + permit_stretch]
    award_scores = [a["score"] for a in awards]

    remainder = tenders + stretch_tenders + permits + permit_stretch + awards
    remainder.sort(
        key=lambda item: _business_value_index(
            item,
            tender_scores=tender_scores,
            permit_scores=permit_scores,
            award_scores=award_scores,
        ),
        reverse=True,
    )
    for item in remainder:
        if len(selected) >= limit:
            break
        if item.get("context") == "own_permit":
            if sum(1 for x in selected if x.get("context") == "own_permit") >= CONSTRUCTION_OWN_PERMIT_MAX_SLOTS:
                continue
        add_item(item)

    return selected[:limit]


def _apply_balanced_ranking(
    matches: list[dict[str, Any]],
    *,
    limit: int,
    active_types: list[OpportunityType],
) -> list[dict[str, Any]]:
    """Reserve slots per opportunity type, then re-rank the combined set by score."""
    if not matches or not active_types:
        return []

    by_type: dict[OpportunityType, list[dict[str, Any]]] = {t: [] for t in active_types}
    for match in matches:
        match_type = match.get("type")
        if match_type in by_type:
            by_type[match_type].append(match)

    for match_type in active_types:
        by_type[match_type].sort(key=_match_sort_key, reverse=True)

    type_count = len(active_types)
    base_quota = limit // type_count
    extra_slots = limit % type_count

    selected: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()

    for index, match_type in enumerate(active_types):
        quota = base_quota + (1 if index < extra_slots else 0)
        for match in by_type[match_type][:quota]:
            key = (match["type"], match["id"])
            if key in seen:
                continue
            selected.append(match)
            seen.add(key)

    if len(selected) < limit:
        for match in sorted(matches, key=_match_sort_key, reverse=True):
            key = (match["type"], match["id"])
            if key in seen:
                continue
            selected.append(match)
            seen.add(key)
            if len(selected) >= limit:
                break

    selected.sort(key=_match_sort_key, reverse=True)
    return selected[:limit]


def _assemble_architecture_opportunities(
    tenders: list[dict[str, Any]],
    permits: list[dict[str, Any]],
    *,
    limit: int = 15,
    tender_stretch: list[dict[str, Any]] | None = None,
    permit_stretch: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Reserve tender and permit slots; keep AI-scored tenders visible above permit backfill."""
    tender_stretch = tender_stretch or []
    permit_stretch = permit_stretch or []
    ai_tenders = sorted(
        [t for t in tenders if t.get("source") == "ai_match"],
        key=_match_sort_key,
        reverse=True,
    )
    rule_tenders = sorted(
        [t for t in tenders if t.get("source") != "ai_match"],
        key=_match_sort_key,
        reverse=True,
    )
    ordered_tenders = ai_tenders + rule_tenders
    stretch_ai = sorted(
        [t for t in tender_stretch if t.get("source") == "ai_match"],
        key=_match_sort_key,
        reverse=True,
    )
    stretch_rule = sorted(
        [t for t in tender_stretch if t.get("source") != "ai_match"],
        key=_match_sort_key,
        reverse=True,
    )
    stretch_tenders = stretch_ai + stretch_rule
    permits = sorted(permits, key=_match_sort_key, reverse=True)
    stretch_permits = sorted(permit_stretch, key=_match_sort_key, reverse=True)

    selected: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()

    def add_item(item: dict[str, Any]) -> bool:
        key = (item["type"], item["id"])
        if key in seen:
            return False
        selected.append(item)
        seen.add(key)
        return True

    for item in ordered_tenders[:ARCHITECTURE_TENDER_RESERVED_SLOTS]:
        add_item(item)

    if len([x for x in selected if x["type"] == "tender"]) < ARCHITECTURE_TENDER_RESERVED_SLOTS:
        for item in ordered_tenders[ARCHITECTURE_TENDER_RESERVED_SLOTS:]:
            if len([x for x in selected if x["type"] == "tender"]) >= ARCHITECTURE_TENDER_RESERVED_SLOTS:
                break
            add_item(item)

    if len([x for x in selected if x["type"] == "tender"]) < ARCHITECTURE_TENDER_RESERVED_SLOTS:
        for item in stretch_tenders:
            if len([x for x in selected if x["type"] == "tender"]) >= ARCHITECTURE_TENDER_RESERVED_SLOTS:
                break
            add_item(item)

    permit_count = sum(1 for x in selected if x["type"] == "permit")
    for item in permits:
        if permit_count >= ARCHITECTURE_PERMIT_RESERVED_SLOTS:
            break
        if add_item(item):
            permit_count += 1

    if permit_count < ARCHITECTURE_PERMIT_RESERVED_SLOTS:
        for item in stretch_permits:
            if permit_count >= ARCHITECTURE_PERMIT_RESERVED_SLOTS:
                break
            if add_item(item):
                permit_count += 1

    if len(selected) < limit:
        remainder = ordered_tenders + stretch_tenders + permits + stretch_permits
        for item in sorted(remainder, key=_match_sort_key, reverse=True):
            if len(selected) >= limit:
                break
            add_item(item)

    return selected[:limit]


def _discover_construction_opportunities(
    company_id: int,
    *,
    limit: int,
    max_candidates: int,
    min_score: int = CONSTRUCTION_TENDER_RULES_THRESHOLD,
    metrics: SessionPhaseMetrics | None = None,
) -> dict[str, Any]:
    """Construction Intelligence ranking with phased DB sessions."""
    phase_metrics = metrics or SessionPhaseMetrics()
    started = time.perf_counter()
    tender_rules_threshold = min_score
    tender_stretch_threshold = max(0, min_score - 10)

    read_started = time.perf_counter()
    with session_scope() as session:
        bundle = _load_construction_read_bundle(session, company_id, max_candidates)
        _finalize_read_bundle(session, bundle)
    phase_metrics.read_ms = (time.perf_counter() - read_started) * 1000

    company = bundle.company
    signals = bundle.signals

    rule_started = time.perf_counter()
    rule_candidates = _scan_construction_rule_tenders_from_rows(bundle.tender_rows, signals)
    print(
        f"[OpportunityDiscovery] construction company={company.id} rule_scan "
        f"{time.perf_counter() - rule_started:.2f}s candidates={len(rule_candidates)}"
    )

    hybrid_started = time.perf_counter()
    fresh_cache = dict(bundle.fresh_cache)
    with session_scope() as session:
        hybrid_scoring = _run_hybrid_tender_scoring(
            session,
            company,
            "construction",
            rule_candidates,
            inline_cap=HYBRID_INLINE_SCORE_CAP,
            cached_matches=fresh_cache,
        )
    phase_metrics.hybrid_write_ms = (time.perf_counter() - hybrid_started) * 1000
    print(
        f"[OpportunityDiscovery] construction company={company.id} hybrid_scoring "
        f"{phase_metrics.hybrid_write_ms / 1000:.2f}s "
        f"cache_hits={hybrid_scoring.get('cache_hits', 0)} "
        f"freshly_scored={hybrid_scoring.get('freshly_scored', 0)}"
    )

    items_started = time.perf_counter()
    hybrid_pairs = hybrid_scoring.get("pairs") or {}
    tender_matches, tender_stretch = _rule_tenders_to_opportunity_items(
        None,
        company.id,
        "construction",
        rule_candidates,
        rules_threshold=tender_rules_threshold,
        stretch_threshold=tender_stretch_threshold,
        hybrid_pairs=hybrid_pairs,
        fresh_cache=fresh_cache,
    )
    print(
        f"[OpportunityDiscovery] construction company={company.id} tender_items "
        f"{time.perf_counter() - items_started:.2f}s "
        f"matches={len(tender_matches)} stretch={len(tender_stretch)}"
    )

    permit_started = time.perf_counter()
    permit_matches: list[dict[str, Any]] = []
    permit_stretch: list[dict[str, Any]] = []
    for permit, own in bundle.permit_rows:
        score, reasons = _score_construction_permit(signals, permit, own=own)
        threshold = CONSTRUCTION_PERMIT_OWN_THRESHOLD if own else CONSTRUCTION_PERMIT_MARKET_THRESHOLD
        stretch_threshold = CONSTRUCTION_PERMIT_OWN_THRESHOLD if own else CONSTRUCTION_PERMIT_MARKET_STRETCH
        item = {
            "type": "permit",
            "id": permit.id,
            "score": score,
            "reasons": reasons,
            "source": "rules",
            "context": "own_permit" if own else "market_permit",
            "payload": _permit_payload(permit),
        }
        if score >= threshold:
            permit_matches.append(item)
        elif score >= stretch_threshold:
            permit_stretch.append(item)
    print(
        f"[OpportunityDiscovery] construction company={company.id} permit_scan "
        f"{time.perf_counter() - permit_started:.2f}s "
        f"matches={len(permit_matches)} stretch={len(permit_stretch)}"
    )

    award_started = time.perf_counter()
    award_matches: list[dict[str, Any]] = []
    for award, context in bundle.award_rows:
        score, reasons = _score_contract_award(signals, award, context=context)
        if score < CONSTRUCTION_AWARD_THRESHOLD:
            continue
        award_matches.append(
            {
                "type": "contract_award",
                "id": award.id,
                "score": score,
                "reasons": reasons,
                "source": "rules",
                "context": context,
                "payload": _award_payload(award),
            }
        )
    print(
        f"[OpportunityDiscovery] construction company={company.id} award_scan "
        f"{time.perf_counter() - award_started:.2f}s matches={len(award_matches)}"
    )

    total_candidates = len(tender_matches) + len(tender_stretch) + len(permit_matches) + len(permit_stretch) + len(award_matches)
    top = _assemble_construction_opportunities(
        tender_matches,
        permit_matches,
        award_matches,
        limit=limit,
        tender_stretch=tender_stretch,
        permit_stretch=permit_stretch,
    )

    breakdown_started = time.perf_counter()
    with session_scope() as session:
        breakdown_count = _attach_final_construction_tender_breakdowns(
            session,
            company,
            top,
            hybrid_pairs=hybrid_pairs,
            fresh_cache=fresh_cache,
        )
    phase_metrics.final_db_ms = (time.perf_counter() - breakdown_started) * 1000

    for item in top:
        item.pop("_tender_key", None)

    total_elapsed_ms = (time.perf_counter() - started) * 1000
    cpu_total_ms = total_elapsed_ms - phase_metrics.db_total_ms
    phase_metrics.log(company.id, "construction", cpu_total_ms)
    print(
        f"[OpportunityDiscovery] construction company={company.id} total "
        f"{total_elapsed_ms / 1000:.2f}s candidates_before_reduction={total_candidates} "
        f"final_matches={len(top)} breakdown_items={breakdown_count} "
        f"breakdown_fill={phase_metrics.final_db_ms / 1000:.2f}s"
    )

    return {
        "company_id": company.id,
        "kind": "construction",
        "min_score": tender_rules_threshold,
        "limit": limit,
        "total_candidates": total_candidates,
        "matches": top,
        "ranking_model": "construction_intelligence_v2_hybrid",
        "hybrid_scoring": hybrid_scoring,
        "thresholds": {
            "tender_ai": CONSTRUCTION_TENDER_AI_THRESHOLD,
            "tender_rules": tender_rules_threshold,
            "tender_stretch": tender_stretch_threshold,
            "permit_market": CONSTRUCTION_PERMIT_MARKET_THRESHOLD,
            "permit_own": CONSTRUCTION_PERMIT_OWN_THRESHOLD,
            "award": CONSTRUCTION_AWARD_THRESHOLD,
        },
    }


def _discover_architecture_opportunities(
    company_id: int,
    *,
    limit: int,
    max_candidates: int,
    min_score: int = ARCHITECTURE_DEFAULT_MIN_SCORE,
    metrics: SessionPhaseMetrics | None = None,
) -> dict[str, Any]:
    """Architecture discovery with phased DB sessions."""
    phase_metrics = metrics or SessionPhaseMetrics()
    started = time.perf_counter()
    tender_rules_threshold = min_score
    tender_stretch_threshold = max(20, min_score - 15)
    ai_tender_threshold = ARCHITECTURE_TENDER_AI_THRESHOLD
    ai_tender_stretch_threshold = ARCHITECTURE_TENDER_STRETCH_THRESHOLD

    read_started = time.perf_counter()
    with session_scope() as session:
        bundle = _load_architecture_read_bundle(session, company_id, max_candidates)
        _finalize_read_bundle(session, bundle)
    phase_metrics.read_ms = (time.perf_counter() - read_started) * 1000

    company = bundle.company
    signals = bundle.signals

    rule_started = time.perf_counter()
    rule_candidates = _scan_architecture_rule_tenders_from_rows(bundle.tender_rows, signals)
    print(
        f"[OpportunityDiscovery] arch company={company.id} rule_scan "
        f"{time.perf_counter() - rule_started:.2f}s candidates={len(rule_candidates)}"
    )

    hybrid_started = time.perf_counter()
    fresh_cache = dict(bundle.fresh_cache)
    cached_tender_rows = dict(bundle.cached_tender_rows)
    with session_scope() as session:
        hybrid_scoring = _run_hybrid_tender_scoring(
            session,
            company,
            "architecture",
            rule_candidates,
            inline_cap=HYBRID_INLINE_SCORE_CAP,
            cached_matches=fresh_cache,
        )
        new_keys = [
            key
            for key in (hybrid_scoring.get("pairs") or {})
            if key not in cached_tender_rows
        ]
        if new_keys:
            cached_tender_rows.update(_batch_load_tender_rows(session, new_keys))
    phase_metrics.hybrid_write_ms = (time.perf_counter() - hybrid_started) * 1000
    print(
        f"[OpportunityDiscovery] arch company={company.id} hybrid_scoring "
        f"{phase_metrics.hybrid_write_ms / 1000:.2f}s "
        f"cache_hits={hybrid_scoring.get('cache_hits', 0)} "
        f"freshly_scored={hybrid_scoring.get('freshly_scored', 0)}"
    )

    items_started = time.perf_counter()
    hybrid_pairs = hybrid_scoring.get("pairs") or {}
    tender_matches, tender_stretch = _rule_tenders_to_opportunity_items(
        None,
        company.id,
        "architecture",
        rule_candidates,
        rules_threshold=tender_rules_threshold,
        stretch_threshold=tender_stretch_threshold,
        hybrid_pairs=hybrid_pairs,
        ai_rules_threshold=ai_tender_threshold,
        ai_stretch_threshold=ai_tender_stretch_threshold,
        fresh_cache=fresh_cache,
    )
    seen_tender_keys = {item["_tender_key"] for item in tender_matches + tender_stretch}
    cached_matches, cached_stretch = _cached_ai_tenders_to_opportunity_items(
        None,
        company.id,
        "architecture",
        rules_threshold=tender_rules_threshold,
        stretch_threshold=tender_stretch_threshold,
        seen_keys=seen_tender_keys,
        ai_rules_threshold=ai_tender_threshold,
        ai_stretch_threshold=ai_tender_stretch_threshold,
        fresh_cache=fresh_cache,
        cached_tender_rows=cached_tender_rows,
    )
    tender_matches.extend(cached_matches)
    tender_stretch.extend(cached_stretch)
    print(
        f"[OpportunityDiscovery] arch company={company.id} tender_items "
        f"{time.perf_counter() - items_started:.2f}s "
        f"matches={len(tender_matches)} stretch={len(tender_stretch)}"
    )

    permit_started = time.perf_counter()
    permit_matches: list[dict[str, Any]] = []
    permit_stretch: list[dict[str, Any]] = []
    for permit, own in bundle.permit_rows:
        score, reasons = _score_architecture_permit(signals, permit, own=own)
        threshold = ARCHITECTURE_PERMIT_OWN_THRESHOLD if own else ARCHITECTURE_PERMIT_MARKET_THRESHOLD
        stretch_threshold = ARCHITECTURE_PERMIT_OWN_THRESHOLD if own else ARCHITECTURE_PERMIT_MARKET_STRETCH
        item = {
            "type": "permit",
            "id": permit.id,
            "score": score,
            "reasons": reasons,
            "source": "rules",
            "context": "own_permit" if own else "market_permit",
            "payload": _permit_payload(permit),
        }
        if score >= threshold:
            permit_matches.append(item)
        elif score >= stretch_threshold:
            permit_stretch.append(item)
    print(
        f"[OpportunityDiscovery] arch company={company.id} permit_scan "
        f"{time.perf_counter() - permit_started:.2f}s "
        f"matches={len(permit_matches)} stretch={len(permit_stretch)}"
    )

    matches = tender_matches + permit_matches
    total_candidates = len(matches) + len(tender_stretch) + len(permit_stretch)
    top = _assemble_architecture_opportunities(
        tender_matches,
        permit_matches,
        limit=limit,
        tender_stretch=tender_stretch,
        permit_stretch=permit_stretch,
    )
    if len(top) < limit:
        for pool in (tender_stretch, permit_stretch):
            for item in sorted(pool, key=_match_sort_key, reverse=True):
                if len(top) >= limit:
                    break
                key = (item["type"], item["id"])
                if any((m["type"], m["id"]) == key for m in top):
                    continue
                top.append(item)

    for item in top:
        item.pop("_tender_key", None)

    total_elapsed_ms = (time.perf_counter() - started) * 1000
    cpu_total_ms = total_elapsed_ms - phase_metrics.db_total_ms
    phase_metrics.log(company.id, "architecture", cpu_total_ms)
    print(
        f"[OpportunityDiscovery] arch company={company.id} total "
        f"{total_elapsed_ms / 1000:.2f}s matches={len(top)}"
    )

    return {
        "company_id": company.id,
        "kind": "architecture",
        "min_score": tender_rules_threshold,
        "limit": limit,
        "total_candidates": total_candidates,
        "matches": top,
        "ranking_model": "architecture_intelligence_v1_hybrid",
        "hybrid_scoring": hybrid_scoring,
        "thresholds": {
            "tender_ai": ai_tender_threshold,
            "tender_rules": tender_rules_threshold,
            "tender_stretch": tender_stretch_threshold,
            "tender_ai_stretch": ai_tender_stretch_threshold,
            "permit_market": ARCHITECTURE_PERMIT_MARKET_THRESHOLD,
            "permit_own": ARCHITECTURE_PERMIT_OWN_THRESHOLD,
        },
    }


def discover_opportunities(
    *,
    company_id: int,
    kind: Kind = "construction",
    min_score: int | None = None,
    limit: int = 15,
    max_candidates: int = 400,
) -> dict[str, Any]:
    """Rank tenders, permits, and contract awards for a company profile."""
    metrics = SessionPhaseMetrics()
    if kind == "construction":
        return _discover_construction_opportunities(
            company_id,
            limit=limit,
            max_candidates=max_candidates,
            min_score=min_score if min_score is not None else CONSTRUCTION_DEFAULT_MIN_SCORE,
            metrics=metrics,
        )

    return _discover_architecture_opportunities(
        company_id,
        limit=limit,
        max_candidates=max_candidates,
        min_score=min_score if min_score is not None else ARCHITECTURE_DEFAULT_MIN_SCORE,
        metrics=metrics,
    )
