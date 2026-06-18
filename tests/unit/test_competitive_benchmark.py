"""Unit tests for benchmark strip."""

from __future__ import annotations

from pipeline.competitive_intel.benchmark import compute_benchmark_strip
from pipeline.competitive_intel.types import MarketCohort, TopCompetitor
from tests.unit.competitive_fixtures import make_arch_company, make_company


def _cohort(*companies):
    return MarketCohort(
        members=list(companies),
        definition="test",
        definition_key="sector_and_city",
        cohort_size=len(companies),
    )


def test_benchmark_five_metrics_construction():
    subject = make_company(id=1)
    peers_rows = [make_company(id=2, total_projects=30), make_company(id=3, total_projects=50)]
    cohort = _cohort(subject, *peers_rows)
    peers = [
        TopCompetitor(
            company_id=2,
            name="Peer A",
            company_kind="construction",
            threat_score=70,
            threat_breakdown={},
            similarity=0.8,
            total_projects=30,
            total_value=5_000_000,
            award_count=3,
        ),
        TopCompetitor(
            company_id=3,
            name="Peer B",
            company_kind="construction",
            threat_score=65,
            threat_breakdown={},
            similarity=0.75,
            total_projects=50,
            total_value=8_000_000,
            award_count=6,
        ),
    ]
    result = compute_benchmark_strip(subject, cohort, peers, kind="construction")
    keys = {m["key"] for m in result["metrics"]}
    assert keys == {
        "total_projects",
        "total_value",
        "avg_project_value",
        "award_count",
        "ai_reliability_score",
    }
    projects = next(m for m in result["metrics"] if m["key"] == "total_projects")
    assert projects["company"] == 40
    assert projects["market_median"] == 40


def test_benchmark_arch_awards_na():
    subject = make_arch_company(id=10)
    cohort = _cohort(subject, make_arch_company(id=11))
    result = compute_benchmark_strip(subject, cohort, [], kind="architecture")
    awards = next(m for m in result["metrics"] if m["key"] == "award_count")
    assert awards["not_applicable"] is True
    assert awards["company"] is None
