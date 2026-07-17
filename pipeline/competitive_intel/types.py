"""Runtime types for competitive intelligence (no DB tables)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from db.company_canonical_constants import (
    company_canonical_name as company_display_name,
)
from db.models import ArchCompany, Company
from pipeline.cip_schema import CompanyIntelligenceProfile
from pipeline.scoring.explain import BreakdownFactor

Kind = Literal["construction", "architecture"]
CompanyRow = Company | ArchCompany


@dataclass
class MarketCohort:
    members: list[CompanyRow]
    definition: str
    definition_key: str
    cohort_size: int
    data_scope: str = "vancouver_permits_and_public_awards"


@dataclass
class ThreatScoreResult:
    score: int
    breakdown: list[BreakdownFactor]
    reasons: list[str]
    confidence: str
    # Required (no default) -- set by
    # pipeline.competitive_intel.threat_score.compute_threat_score() to its
    # THREAT_SCORE_ALGORITHM_VERSION. Kept as a plain field (not an import
    # from threat_score.py) to avoid a circular import, since threat_score.py
    # itself imports ThreatScoreResult from this module. Placed before the
    # default-valued raw_components field, as dataclasses require.
    algorithm_version: str
    raw_components: dict[str, float] = field(default_factory=dict)

    def to_explanation_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "score_label": "Competitive Threat Score",
            "rank_key": float(self.score),
            "breakdown": [b.to_dict() for b in self.breakdown],
            "reasons": self.reasons,
            "confidence": self.confidence,
            "algorithm_version": self.algorithm_version,
        }


@dataclass
class PeerCandidate:
    company_id: int
    name: str
    row: CompanyRow
    cip: CompanyIntelligenceProfile
    similarity: float
    category_overlap_raw: float
    geographic_overlap_raw: float
    value_overlap_raw: float


@dataclass
class TopCompetitor:
    company_id: int
    name: str
    company_kind: Kind
    threat_score: int
    threat_breakdown: dict[str, Any]
    similarity: float
    total_projects: int
    total_value: float
    award_count: int | None


@dataclass
class ActivityStats:
    """Precomputed cohort activity normalization."""

    award_90d_p90: float
    permit_90d_p90: float
    award_90d_by_company: dict[int, int] = field(default_factory=dict)
    permit_90d_by_company: dict[int, int] = field(default_factory=dict)
