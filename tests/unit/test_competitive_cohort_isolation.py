"""Unit tests for cohort type isolation (Feature 007)."""

from __future__ import annotations

from tests.unit.competitive_fixtures import make_cip, make_company

from pipeline.competitive_intel.cohort_isolation import (
    apply_cohort_type_isolation,
    is_allowed_gc_cohort_member,
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


def test_architrix_design_studio_excluded():
    architrix = make_company(
        id=213,
        name="Khang Nguyen DBA: Architrix Design Studio",
        company_type="Architect",
        primary_trade="architecture",
        project_types=["Design"],
        total_projects=324,
    )
    assert is_allowed_gc_cohort_member(architrix) is False


def test_lmdg_excluded_by_company_type():
    lmdg = make_company(
        id=1921,
        name="David Steer DBA: LMDG Building Code Consultants Ltd.",
        company_type="Building Code Consultant",
        primary_trade="consulting",
        total_projects=194,
    )
    assert is_allowed_gc_cohort_member(lmdg) is False


def test_office_environments_excluded():
    aura = make_company(
        id=100,
        name="Ron Boram DBA: Aura Office Environments",
        company_type="Unknown",
        primary_trade="",
        project_types=["Office Interiors"],
        total_projects=20,
    )
    assert is_allowed_gc_cohort_member(aura) is False


def test_lung_designs_excluded_despite_gc_company_type():
    lung = make_company(
        id=22,
        name="Danny Lung & Sharon Chen DBA: Lung Designs Group Ltd.",
        company_type="General Contractor",
        primary_trade="demolition",
        project_types=["New Building"],
        total_projects=50,
    )
    assert is_allowed_gc_cohort_member(lung) is False


def test_gc_peer_allowed_by_trade_or_name():
    fusion = make_company(
        id=670,
        name="Jason Ludwig DBA: Fusion Projects",
        company_type="General Contractor",
        primary_trade="general_building",
        total_projects=173,
    )
    heatherbrae = make_company(
        id=6999,
        name="Carl Massey DBA: Heatherbrae Builders Co Ltd",
        company_type="General Contractor",
        total_projects=5,
    )
    reotech = make_company(
        id=165,
        name="Reotech Construction Ltd.",
        company_type="General Contractor",
        total_projects=187,
    )
    assert is_allowed_gc_cohort_member(fusion) is True
    assert is_allowed_gc_cohort_member(heatherbrae) is True
    assert is_allowed_gc_cohort_member(reotech) is True


def test_apply_allowlist_before_scoring_for_gc_subject():
    subject = _gc_subject()
    cip = make_cip(company_id=int(subject.id))
    members = [
        make_company(
            id=213,
            name="Khang Nguyen DBA: Architrix Design Studio",
            company_type="Architect",
            primary_trade="architecture",
            project_types=[],
            total_projects=324,
        ),
        make_company(
            id=1921,
            name="David Steer DBA: LMDG Building Code Consultants Ltd.",
            company_type="Building Code Consultant",
            primary_trade="consulting",
            total_projects=194,
        ),
        make_company(
            id=888,
            name="Danny Lung & Sharon Chen DBA: Lung Designs Group Ltd.",
            company_type="Unknown",
            primary_trade="",
            project_types=["Interior Design"],
            total_projects=40,
        ),
        make_company(
            id=100,
            name="Ron Boram DBA: Aura Office Environments",
            company_type="Unknown",
            primary_trade="",
            project_types=["Office Interiors"],
            total_projects=20,
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
    assert "Khang Nguyen DBA: Architrix Design Studio" not in names
    assert "David Steer DBA: LMDG Building Code Consultants Ltd." not in names
    assert "Danny Lung & Sharon Chen DBA: Lung Designs Group Ltd." not in names
    assert "Ron Boram DBA: Aura Office Environments" not in names
    assert "Jason Ludwig DBA: Fusion Projects" in names


def test_isolation_skipped_for_non_gc_subject():
    subject = make_company(
        company_type="Building Code Consultant",
        primary_trade="consulting",
        name="David Steer DBA: LMDG Building Code Consultants Ltd.",
    )
    arch_peer = make_company(
        id=213,
        name="Khang Nguyen DBA: Architrix Design Studio",
        company_type="Architect",
        total_projects=10,
    )
    assert is_gc_builder_profile(subject) is False
    filtered = apply_cohort_type_isolation(members=[arch_peer], subject=subject, kind="construction")
    assert filtered == [arch_peer]
