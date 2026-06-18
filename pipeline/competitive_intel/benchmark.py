"""Benchmark strip — You vs market median vs top-rival median."""

from __future__ import annotations

import statistics
from typing import Any

from pipeline.competitive_intel.awards import top_award_rival_counts
from pipeline.competitive_intel.types import CompanyRow, Kind, MarketCohort, TopCompetitor

BENCHMARK_METRICS: list[tuple[str, str, str]] = [
    ("total_projects", "Total Projects", "count"),
    ("total_value", "Total Project Value", "currency"),
    ("avg_project_value", "Average Project Value", "currency"),
    ("award_count", "Awards", "count"),
    ("ai_reliability_score", "Reliability Score", "score"),
]


def _metric_value(
    company: CompanyRow,
    key: str,
    *,
    award_counts: dict[int, int] | None = None,
) -> float | int | None:
    if key == "award_count" and award_counts is not None:
        return int(award_counts.get(int(company.id), 0))

    value = getattr(company, key, None)
    if value is None:
        return None
    if key == "ai_reliability_score":
        return int(value) if value is not None else None
    if key in {"total_projects", "award_count"}:
        return int(value or 0)
    return float(value or 0)


def _median(values: list[float | int]) -> float | int | None:
    if not values:
        return None
    return statistics.median(values)


def compute_benchmark_strip(
    subject: CompanyRow,
    cohort: MarketCohort,
    peers: list[TopCompetitor],
    *,
    kind: Kind,
    award_counts: dict[int, int] | None = None,
    award_market_members: list[CompanyRow] | None = None,
) -> dict[str, Any]:
    metrics_out: list[dict[str, Any]] = []
    award_median_members = award_market_members if award_market_members is not None else cohort.members

    for key, label, unit in BENCHMARK_METRICS:
        not_applicable = kind == "architecture" and key == "award_count"

        company_val = None if not_applicable else _metric_value(
            subject, key, award_counts=award_counts if key == "award_count" else None
        )

        cohort_vals: list[float | int] = []
        median_members = award_median_members if key == "award_count" else cohort.members
        for member in median_members:
            if key == "ai_reliability_score":
                rel = getattr(member, "ai_reliability_score", None)
                if rel is not None:
                    cohort_vals.append(int(rel))
            elif key == "award_count" and kind == "architecture":
                continue
            else:
                val = _metric_value(
                    member, key, award_counts=award_counts if key == "award_count" else None
                )
                if val is not None:
                    cohort_vals.append(val)

        market_median = None if not_applicable else _median(cohort_vals)

        peer_vals: list[float | int] = []
        if key == "award_count" and award_counts is not None:
            for peer in peers:
                peer_vals.append(int(award_counts.get(peer.company_id, 0)))
            if peer_vals and all(value == 0 for value in peer_vals) and award_market_members:
                peer_vals = top_award_rival_counts(award_market_members, award_counts)
        else:
            peer_ids = {p.company_id for p in peers}
            peer_rows = [m for m in cohort.members if m.id in peer_ids]
            for row in peer_rows:
                if key == "ai_reliability_score":
                    rel = getattr(row, "ai_reliability_score", None)
                    if rel is not None:
                        peer_vals.append(int(rel))
                else:
                    val = _metric_value(row, key)
                    if val is not None:
                        peer_vals.append(val)

        top_rival_median = None if not_applicable else _median(peer_vals)

        metrics_out.append(
            {
                "key": key,
                "label": label,
                "company": company_val,
                "market_median": market_median,
                "top_competitor_median": top_rival_median,
                "unit": unit,
                "not_applicable": not_applicable,
            }
        )

    return {"metrics": metrics_out}
