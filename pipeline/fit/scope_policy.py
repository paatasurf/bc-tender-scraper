"""Specialist scope detection for opportunity filtering (Iteration C)."""

from __future__ import annotations

import re

from pipeline.cip_schema import CompanyIntelligenceProfile
from pipeline.fit.geo_policy import TRADE_SPECIALIST_TRADES
from pipeline.market_normalizer import NormalizedOpportunity

LIGHTING_AIRPORT_RE = re.compile(
    r"\blighting\b|\bcatenary\b|\bODALS\b|\bapproach lighting\b"
    r"|\bairport\b|\brunway\b|\bnavigational aid\b|\baeronautical\b",
    re.I,
)

ENGINEERING_ONLY_RE = re.compile(
    r"\bmechanical engineering\b|\bstructural engineering\b|\belectrical engineering\b"
    r"|\bcivil engineering\b|\bbridge engineering\b|\btransportation engineering\b"
    r"|\bgeotechnical engineering\b|\benvironmental engineering\b",
    re.I,
)

ARCHITECTURAL_DESIGN_RE = re.compile(
    r"\barchitectural\b|\barchitecture\b|\barchitect\b|\bprime consultant\b"
    r"|\bmaster plan\b|\bschematic design\b|\bfeasibility study\b"
    r"|\bproject delivery support\b|\bplanning services\b|\burban design\b"
    r"|\bheritage\b|\binterior design\b",
    re.I,
)

DESIGN_KEYWORDS = re.compile(
    r"\bdesign\b|\barchitectural\b|\bfeasibility\b|\bmaster plan\b|\bschematic\b",
    re.I,
)

FIELD_CONSTRUCTION_RE = re.compile(
    r"\broof remediation\b|\broof replacement\b|\bconstruction management services\b"
    r"|\bgeneral contractor\b|\btenant improvement construction\b",
    re.I,
)

CONSULTING_SCOPE_RE = re.compile(
    r"\bengineering\b|\bconsulting\b|\bconsultant\b|\bprofessional services\b|\bdesign services\b",
    re.I,
)


def requires_electrical_specialist(opp: NormalizedOpportunity) -> bool:
    blob = f"{opp.title} {opp.text_blob}"
    return bool(LIGHTING_AIRPORT_RE.search(blob))


def has_electrical_specialization(cip: CompanyIntelligenceProfile) -> bool:
    if cip.primary_trade == "electrical":
        return True
    if cip.company_type == "Trade Contractor" and cip.primary_trade in {"electrical", "mechanical"}:
        return cip.primary_trade == "electrical"
    if cip.primary_trade in TRADE_SPECIALIST_TRADES:
        return cip.primary_trade == "electrical"
    if cip.primary_trade in {"general_building", "general_contracting"}:
        return False
    return False


def is_genuine_design_opportunity(opp: NormalizedOpportunity) -> bool:
    title = opp.title or ""
    if ENGINEERING_ONLY_RE.search(title) and not ARCHITECTURAL_DESIGN_RE.search(title):
        return False
    if ARCHITECTURAL_DESIGN_RE.search(title) or DESIGN_KEYWORDS.search(title):
        return True
    if opp.delivery_type == "design" or opp.subtype == "design_rfp":
        return bool(DESIGN_KEYWORDS.search(title) or ARCHITECTURAL_DESIGN_RE.search(title))
    return False


def is_pure_engineering_rfp(opp: NormalizedOpportunity) -> bool:
    title = opp.title or ""
    if not ENGINEERING_ONLY_RE.search(title):
        return False
    return not ARCHITECTURAL_DESIGN_RE.search(title)


def is_field_construction_for_consultant(opp: NormalizedOpportunity) -> bool:
    blob = f"{opp.title} {opp.text_blob}"
    if not FIELD_CONSTRUCTION_RE.search(blob):
        return False
    return not CONSULTING_SCOPE_RE.search(blob)


def is_architecture_design_path(cip: CompanyIntelligenceProfile, opp: NormalizedOpportunity) -> bool:
    return cip.entity_class == "designer" and is_genuine_design_opportunity(opp)
