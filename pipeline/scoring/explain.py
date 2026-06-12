"""Shared explainability structures for BD recommendations."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class BreakdownFactor:
    factor: str
    label: str
    points: int
    max_points: int
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "factor": self.factor,
            "label": self.label,
            "points": self.points,
            "max_points": self.max_points,
            "detail": self.detail,
        }


@dataclass
class ScoredRecommendation:
    score: int
    score_label: str
    rank_key: float
    breakdown: list[BreakdownFactor] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    def to_explanation_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "score_label": self.score_label,
            "rank_key": round(self.rank_key, 2),
            "breakdown": [b.to_dict() for b in self.breakdown],
            "reasons": self.reasons,
        }


def build_reasons(breakdown: list[BreakdownFactor], limit: int = 5) -> list[str]:
    active = [b for b in breakdown if b.points > 0]
    active.sort(key=lambda b: b.points, reverse=True)
    return [b.label for b in active[:limit]]


def weighted_fit(
    factors: list[tuple[str, str, int, int, str]],
) -> tuple[int, list[BreakdownFactor]]:
    """Build score from (factor_id, label, raw_score_0_100, max_points, detail)."""
    breakdown: list[BreakdownFactor] = []
    total = 0
    for factor_id, label, raw, max_pts, detail in factors:
        pts = round((raw / 100) * max_pts) if raw else 0
        breakdown.append(
            BreakdownFactor(
                factor=factor_id,
                label=label,
                points=pts,
                max_points=max_pts,
                detail=detail,
            )
        )
        total += pts
    return min(100, total), breakdown
