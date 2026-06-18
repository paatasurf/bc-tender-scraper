"""Unit tests for cohort type isolation (Feature 007)."""

from __future__ import annotations

from tests.unit.competitive_fixtures import make_cip, make_company

from pipeline.competitive_intel.cohort_isolation import (
    apply_cohort_type_isolation,
    is_excluded_non_construction_member,
    is_gc_builder_profile,
)


def _gc_subject(**overrides):
    return make_company(
        name="Mo Maani DBA: Mo Maani Construction",
        company_type="General Contractor",
        primary_trade="general_building",
        dominant_sector="residential",
        total_projects=30,
        **overrides,
    )


def test_lmdg_excluded_from_gc_cohort():
    lmdg = make_company(
        id=1921,
        name="David Steer DBA: LMDG Building Code Consultants Ltd.",
        company_type="Building Code Consultant",
        primary_trade="consulting",
        total_projects=194,
    )
    assert is_excluded_non_construction_member(lmdg) is True


def test_lung_designs_excluded_from_gc_cohort():
    lung = make_company(
        id=999,
        name="Danny Lung & Sharon Chen DBA: Lung Designs Group Ltd.",
        company_type="Unknown",
        project_types=["Interior Design"],
        total_projects=50,
    )
    assert is_excluded_non_construction_member(lung) is True


def test_gc_peer_not_excluded():
    peer = make_company(
        id=670,
        name="Jason Ludwig DBA: Fusion Projects",
        company_type="General Contractor",
        primary_trade="general_building",
        total_projects=173,
    )
    assert is_excluded_non_construction_member(peer) is False


def test_apply_isolation_before_scoring_for_gc_subject():
    subject = _gc_subject()
    cip = make_cip(company_id=int(subject.id))
    members = [
        make_company(
            id=1921,
            name="David Steer DBA: LMDG Building Code Consultants Ltd.",
            total_projects=194,
        ),
        make_company(
            id=888,
            name="Danny Lung & Sharon Chen DBA: Lung Designs Group Ltd.",
            total_projects=40,
        ),
        make_company(
            id=670,
            name="Jason Ludwig DBA: Fusion Projects",
            company_type="General Contractor",
            primary_trade="general_building",
            total_projects=173,
        ),
    ]
    filtered = apply_cohort_type_isolation(
        members, subject, kind="construction", subject_cip=cip
    )
    names = {m.name for m in filtered}
    assert "David Steer DBA: LMDG Building Code Consultants Ltd." not in names
    assert "Danny Lung & Sharon Chen DBA: Lung Designs Group Ltd." not in names
    assert "Jason Ludwig DBA: Fusion Projects" in names


def test_isolation_skipped_for_non_gc_subject():
    subject = make_company(
        company_type="Building Code Consultant",
        primary_trade="consulting",
        name="David Steer DBA: LMDG Building Code Consultants Ltd.",
    )
    lmdg_peer = make_company(
        id=2,
        name="Another Code Consultant Ltd.",
        company_type="Consultant",
        total_projects=10,
    )
    assert is_gc_builder_profile(subject) is False
    filtered = apply_cohort_type_isolation(members=[lmdg_peer], subject=subject, kind="construction")
    assert filtered == [lmdg_peer]
