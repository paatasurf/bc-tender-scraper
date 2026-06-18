"""Unit tests for competitive cohort quality gates."""

from __future__ import annotations

from tests.unit.competitive_fixtures import make_company

from pipeline.competitive_intel.cohort import _passes_cohort_quality_gate


def test_excludes_award_only_peer_without_category_overlap():
    subject = make_company(
        id=1921,
        total_projects=194,
        project_types=["Building Code Consulting", "Commercial"],
        dominant_sector="commercial",
        primary_trade="consulting",
    )
    simex = make_company(
        id=134635,
        name="Simex Defence Inc.",
        total_projects=0,
        total_value=0.0,
        award_count=139,
        award_categories=["Defence", "Military"],
        dominant_sector="commercial",
        primary_trade="defence",
    )
    assert _passes_cohort_quality_gate(subject, simex, kind="construction") is False


def test_includes_award_only_peer_with_strong_category_overlap():
    subject = make_company(
        total_projects=194,
        project_types=["Construction", "Renovation"],
    )
    peer = make_company(
        id=99,
        total_projects=0,
        award_count=50,
        award_categories=["Construction", "Renovation"],
        dominant_sector="institutional",
        primary_trade="general_building",
    )
    assert _passes_cohort_quality_gate(subject, peer, kind="construction") is True


def test_subject_with_many_projects_requires_peer_min_two_projects():
    subject = make_company(total_projects=40, project_types=["Building"])
    peer_one = make_company(id=2, total_projects=1, award_count=3, project_types=["Building"])
    peer_two = make_company(id=3, total_projects=5, project_types=["Building"])
    assert _passes_cohort_quality_gate(subject, peer_one, kind="construction") is False
    assert _passes_cohort_quality_gate(subject, peer_two, kind="construction") is True


def test_requires_sector_or_trade_match():
    subject = make_company(dominant_sector="commercial", primary_trade="consulting")
    peer = make_company(
        id=2,
        total_projects=10,
        dominant_sector="institutional",
        primary_trade="electrical",
    )
    assert _passes_cohort_quality_gate(subject, peer, kind="construction") is False
