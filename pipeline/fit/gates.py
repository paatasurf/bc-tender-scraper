"""Hard business-fit gates (Iteration C tuned)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pipeline.business_attributes import MAINTENANCE_RE
from pipeline.cip_schema import CompanyIntelligenceProfile
from pipeline.fit.dimensions import FitDimension, compute_all_fits
from pipeline.fit.geo_policy import (
    infer_opportunity_trades,
    is_remote_federal_for_local_company,
    is_trade_specialist,
    is_weak_consultant_federal,
    strong_trade_match,
)
from pipeline.fit.profile_confidence import effective_profile_confidence, meets_active_profile_threshold
from pipeline.fit.scope_policy import (
    has_electrical_specialization,
    is_architecture_design_path,
    is_field_construction_for_consultant,
    is_pure_engineering_rfp,
    requires_electrical_specialist,
)
from pipeline.market_normalizer import NormalizedOpportunity

Section = Literal["active", "pipeline", "intelligence", "growth"]

ACTIVE_BPS_THRESHOLD = 65
PIPELINE_BPS_THRESHOLD = 65
INTEL_BPS_THRESHOLD = 68
GROWTH_BPS_THRESHOLD = 72

GATE_THRESHOLDS: dict[Section, dict[str, int]] = {
    "active": {
        "business_fit": 60,
        "project_type_fit": 40,
        "sector_fit": 50,
        "geography_fit": 55,
        "value_fit": 45,
        "client_fit": 35,
    },
    "pipeline": {
        "business_fit": 45,
        "project_type_fit": 35,
        "sector_fit": 40,
        "geography_fit": 50,
        "value_fit": 0,
        "client_fit": 0,
    },
    "intelligence": {
        "business_fit": 50,
        "project_type_fit": 30,
        "sector_fit": 40,
        "geography_fit": 40,
        "value_fit": 0,
        "client_fit": 40,
    },
    "growth": {
        "business_fit": 55,
        "project_type_fit": 40,
        "sector_fit": 45,
        "geography_fit": 50,
        "value_fit": 45,
        "client_fit": 35,
    },
}


def _effective_thresholds(
    cip: CompanyIntelligenceProfile,
    opp: NormalizedOpportunity,
    section: Section,
) -> dict[str, int]:
    thresholds = dict(GATE_THRESHOLDS[section])
    if section == "active" and is_architecture_design_path(cip, opp):
        thresholds.update(
            {
                "business_fit": 50,
                "project_type_fit": 35,
                "sector_fit": 45,
                "geography_fit": 45,
            }
        )
    elif section == "active" and is_trade_specialist(cip):
        is_strong, _ = strong_trade_match(cip, opp)
        if is_strong:
            thresholds.update({"geography_fit": 45, "sector_fit": 45, "project_type_fit": 35})
    return thresholds


@dataclass
class GateResult:
    passed: bool
    fits: dict[str, FitDimension]
    rejection_code: str = ""
    rejection_detail: str = ""
    failed_dimensions: list[str] | None = None

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "fits": {k: v.to_dict() for k, v in self.fits.items()},
            "rejection_code": self.rejection_code,
            "rejection_detail": self.rejection_detail,
            "failed_dimensions": self.failed_dimensions or [],
        }


def _hard_reject(cip: CompanyIntelligenceProfile, opp: NormalizedOpportunity, section: Section) -> tuple[bool, str, str]:
    title_blob = f"{opp.title} {opp.text_blob}"

    if cip.work_orientation not in {"maintenance", "mixed"} and (
        opp.orientation == "maintenance" or MAINTENANCE_RE.search(title_blob or "")
    ):
        return True, "ORIENTATION_MISMATCH", "Maintenance/SOA procurement vs construction-oriented company"

    if section == "active" and requires_electrical_specialist(opp) and not has_electrical_specialization(cip):
        return (
            True,
            "SPECIALIST_SCOPE_MISMATCH",
            "Lighting/airport scope requires electrical trade specialization",
        )

    if section == "active" and cip.entity_class == "designer" and is_pure_engineering_rfp(opp):
        return (
            True,
            "ENGINEERING_SCOPE_MISMATCH",
            "Pure engineering RFQ — not an architectural design opportunity",
        )

    if section == "active" and cip.entity_class == "consultant" and is_field_construction_for_consultant(opp):
        return (
            True,
            "CONSTRUCTION_SCOPE_MISMATCH",
            "Field construction/remediation scope vs engineering consulting profile",
        )

    if section == "active" and is_remote_federal_for_local_company(cip, opp):
        is_strong, _ = strong_trade_match(cip, opp)
        if not (is_trade_specialist(cip) and is_strong):
            return True, "FEDERAL_GEO_MISMATCH", "Federal opportunity outside company service geography"

    if section == "active" and is_weak_consultant_federal(cip, opp):
        return (
            True,
            "CONSULTANT_REMOTE_MISMATCH",
            "Remote federal opportunity without geographic or sector alignment for consultant",
        )

    eff_conf = effective_profile_confidence(cip)
    opp_trades = infer_opportunity_trades(opp)
    if "unclassified" in opp_trades and eff_conf >= 0.55:
        if section == "active" and cip.entity_class in {"contractor", "consultant"}:
            is_strong, _ = strong_trade_match(cip, opp)
            if not is_strong and not is_architecture_design_path(cip, opp):
                return True, "UNCLASSIFIED_SCOPE", "Opportunity scope too generic to assess business fit"

    if section == "growth":
        growth_ok = any(
            g in opp.sector or g in opp.delivery_type or g.replace("_expansion", "") in opp.sector
            for g in cip.growth_direction
        )
        if not growth_ok and cip.expansion_confidence < 0.3:
            return True, "NO_GROWTH_BASIS", "No observed history supporting this expansion direction"

    if section == "active" and not meets_active_profile_threshold(cip):
        if not is_architecture_design_path(cip, opp):
            return (
                True,
                "LOW_PROFILE_CONFIDENCE",
                f"Company profile too uncertain for active bid recommendations (confidence {eff_conf:.2f})",
            )

    return False, "", ""


def evaluate_gates(
    cip: CompanyIntelligenceProfile,
    opp: NormalizedOpportunity,
    section: Section,
) -> GateResult:
    fits = compute_all_fits(cip, opp)
    thresholds = _effective_thresholds(cip, opp, section)

    hard, code, detail = _hard_reject(cip, opp, section)
    if hard:
        return GateResult(False, fits, code, detail, [code])

    failed_set: set[str] = set()
    for key, threshold in thresholds.items():
        if threshold <= 0:
            continue
        if fits[key].score < threshold:
            failed_set.add(key)

    if section == "active":
        if fits["business_fit"].score < thresholds["business_fit"]:
            failed_set.add("business_fit")
        if fits["sector_fit"].score < thresholds["sector_fit"]:
            failed_set.add("sector_fit")
        passed_count = sum(1 for k, d in fits.items() if d.score >= thresholds.get(k, 100))
        min_dims = 4 if is_architecture_design_path(cip, opp) else 4
        if passed_count < min_dims:
            failed_set.add("insufficient_dimensions")

    failed = sorted(failed_set)
    passed = len(failed) == 0
    rejection_code = ""
    rejection_detail = ""
    if not passed:
        rejection_code = failed[0].upper() if failed else "GATE_FAILED"
        rejection_detail = "; ".join(
            f"{fits[f].name}: {fits[f].score} — {fits[f].reason}" for f in failed if f in fits
        ) or "Failed business fit gates"

    return GateResult(passed, fits, rejection_code, rejection_detail, failed)


def intel_actionability_gate(
    cip: CompanyIntelligenceProfile,
    opp: NormalizedOpportunity,
    *,
    active_items: list[dict],
    related_client: bool = False,
) -> tuple[bool, str]:
    if opp.context == "own_history":
        return True, "Own award history"

    org = (opp.organization or "").lower()
    for client in cip.repeat_clients + cip.award_clients:
        if client and client.lower() in org:
            return True, f"Linked to client: {client}"

    for item in active_items:
        title = str(item.get("payload", {}).get("title", "")).lower()
        client = str(item.get("payload", {}).get("company", "")).lower()
        if org and (org in title or org in client):
            return True, "Linked to active opportunity buyer"

    sector = opp.sector
    if sector == cip.dominant_sector and opp.context == "peer_award":
        return True, f"Competitor award in core sector: {sector}"

    return False, "No link to active bids, clients, or sector competitors"
