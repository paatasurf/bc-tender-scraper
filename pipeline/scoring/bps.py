"""Business Pursuit Score — ranking after fit gates pass."""

from __future__ import annotations

from dataclasses import dataclass, field

from pipeline.cip_schema import CompanyIntelligenceProfile
from pipeline.fit.dimensions import FitDimension
from pipeline.fit.geo_policy import is_trade_specialist, strong_trade_match
from pipeline.market_normalizer import NormalizedOpportunity
from pipeline.scoring.explain import (
    BreakdownFactor,
    ScoredRecommendation,
    build_reasons,
)
from pipeline.scoring.revenue_rank import rank_key

# Stable semantic identifier for this Business Pursuit Score contract
# (section weight tables, pattern-match/trade-specialist bonuses, zero-factor
# penalty, verdict thresholds). Bump only when one of those actually
# changes -- see PR-E1.
BPS_ALGORITHM_VERSION = "bps_v1"


@dataclass
class BPSResult:
    score: int
    rank_key: float
    breakdown: list[BreakdownFactor]
    reasons: list[str]
    pursuit_verdict: str
    fits: dict[str, FitDimension] = field(default_factory=dict)

    @property
    def algorithm_version(self) -> str:
        return BPS_ALGORITHM_VERSION

    def to_explanation_dict(self) -> dict:
        return {
            "score": self.score,
            "score_label": "Business Pursuit Score",
            "rank_key": round(self.rank_key, 2),
            "breakdown": [b.to_dict() for b in self.breakdown],
            "reasons": self.reasons,
            "fit_assessment": {k: v.to_dict() for k, v in self.fits.items()},
            "pursuit_verdict": self.pursuit_verdict,
            "algorithm_version": self.algorithm_version,
        }


def _pattern_match_bonus(
    cip: CompanyIntelligenceProfile, opp: NormalizedOpportunity
) -> int:
    sector = opp.sector
    delivery = opp.delivery_type
    for cl in cip.project_clusters[:3]:
        if cl.sector == sector and cl.delivery == delivery:
            return min(10, int(cl.share * 15))
    return 0


def compute_bps(
    cip: CompanyIntelligenceProfile,
    opp: NormalizedOpportunity,
    fits: dict[str, FitDimension],
    *,
    section: str = "active",
) -> BPSResult:
    weights = {
        "business_fit": 0.25,
        "project_type_fit": 0.20,
        "sector_fit": 0.15,
        "geography_fit": 0.15,
        "value_fit": 0.10,
        "client_fit": 0.10,
    }
    if section == "pipeline":
        weights = {
            "business_fit": 0.10,
            "project_type_fit": 0.25,
            "sector_fit": 0.20,
            "geography_fit": 0.25,
            "value_fit": 0.10,
            "client_fit": 0.10,
        }

    breakdown: list[BreakdownFactor] = []
    total = 0.0
    for key, weight in weights.items():
        dim = fits[key]
        pts = round(dim.score * weight)
        breakdown.append(
            BreakdownFactor(
                factor=key,
                label=key.replace("_", " ").title(),
                points=pts,
                max_points=round(100 * weight),
                detail=dim.reason,
            )
        )
        total += dim.score * weight

    bonus = _pattern_match_bonus(cip, opp)
    if bonus:
        breakdown.append(
            BreakdownFactor(
                factor="historical_pattern",
                label="Historical Pattern Match",
                points=bonus,
                max_points=10,
                detail="Matches a top project cluster",
            )
        )
        total += bonus

    if section == "active" and is_trade_specialist(cip):
        is_strong, trade_score = strong_trade_match(cip, opp)
        if is_strong:
            specialist_bonus = 6
            breakdown.append(
                BreakdownFactor(
                    factor="trade_specialist",
                    label="Trade Specialist Match",
                    points=specialist_bonus,
                    max_points=6,
                    detail=f"Primary trade scope match ({trade_score})",
                )
            )
            total += specialist_bonus

    zero_factors = sum(1 for d in fits.values() if d.score < 20)
    if zero_factors >= 2:
        total = min(total, 55)

    score = min(100, int(round(total)))

    if score >= 80:
        verdict = "Pursue — strong alignment across trade, sector, and geography"
    elif score >= 70:
        verdict = "Review — good fit worth evaluating"
    elif score >= 65:
        verdict = "Consider — acceptable fit with some gaps"
    else:
        verdict = "Low priority — partial fit only"

    rk = rank_key(score, opp.estimated_value) if section == "active" else float(score)
    reasons = build_reasons(breakdown, limit=4)

    return BPSResult(
        score=score,
        rank_key=rk,
        breakdown=breakdown,
        reasons=reasons,
        pursuit_verdict=verdict,
        fits=fits,
    )


def bps_to_scored(bps: BPSResult) -> ScoredRecommendation:
    return ScoredRecommendation(
        score=bps.score,
        score_label="Business Pursuit Score",
        rank_key=bps.rank_key,
        breakdown=bps.breakdown,
        reasons=bps.reasons,
    )
