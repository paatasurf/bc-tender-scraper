"""Cohort entity_role and person-name exclusion for competitive intelligence."""

from __future__ import annotations

from unittest.mock import MagicMock

from db.company_canonical_constants import (
    ENTITY_ROLE_APPLICANT_ALIAS,
    ENTITY_ROLE_PROBABLE_PERSON,
    ENTITY_ROLE_STANDALONE,
)
from tests.unit.competitive_fixtures import make_company

from pipeline.competitive_intel.cohort import (
    _fetch_cohort_rows,
    filter_construction_peer_pool,
)
from pipeline.competitive_intel.tender_activity import get_missed_opportunities
from pipeline.competitive_intel.types import TopCompetitor


def test_filter_construction_peer_pool_drops_person_standalone_names():
    real_gc = make_company(
        id=10, name="Pacific Build Co Ltd", entity_role=ENTITY_ROLE_STANDALONE
    )
    tijana = make_company(
        id=10928,
        name="Tijana Sljivic",
        entity_role=ENTITY_ROLE_STANDALONE,
        company_type="General Contractor",
    )
    shalindro = make_company(
        id=1152,
        name="Shalindro Dosanjh",
        entity_role=ENTITY_ROLE_STANDALONE,
        company_type="General Contractor",
    )
    nickname_person = make_company(
        id=7873,
        name="Yi Chieh (Ashanti) Lee",
        entity_role=ENTITY_ROLE_STANDALONE,
        company_type="General Contractor",
    )
    dba_gc = make_company(
        id=8638,
        name="Jack Hui DBA: Pontem Group",
        entity_role=ENTITY_ROLE_STANDALONE,
    )

    filtered = filter_construction_peer_pool(
        [real_gc, tijana, shalindro, nickname_person, dba_gc]
    )
    names = {row.name for row in filtered}
    assert "Pacific Build Co Ltd" in names
    assert "Jack Hui DBA: Pontem Group" in names
    assert "Tijana Sljivic" not in names
    assert "Shalindro Dosanjh" not in names
    assert "Yi Chieh (Ashanti) Lee" not in names


def test_fetch_cohort_rows_applies_analytics_entity_filter_for_construction():
    subject = make_company(
        id=8638, dominant_sector="residential", primary_trade="general_building"
    )
    session = MagicMock()
    captured: dict[str, object] = {}

    def scalars_side_effect(query):
        captured["query"] = query
        result = MagicMock()
        result.all.return_value = []
        return result

    session.scalars.side_effect = scalars_side_effect

    _fetch_cohort_rows(
        session, subject=subject, kind="construction", use_city=False, city=""
    )

    compiled = str(captured["query"].compile(compile_kwargs={"literal_binds": True}))
    assert ENTITY_ROLE_APPLICANT_ALIAS in compiled
    assert ENTITY_ROLE_PROBABLE_PERSON in compiled
    assert "ORDER BY companies.id ASC" in compiled


def test_fetch_cohort_rows_post_filters_person_standalone():
    subject = make_company(
        id=8638, dominant_sector="residential", primary_trade="general_building"
    )
    peer = make_company(
        id=10, name="Real Builder Inc", entity_role=ENTITY_ROLE_STANDALONE
    )
    person = make_company(
        id=10928,
        name="Tijana Sljivic",
        entity_role=ENTITY_ROLE_STANDALONE,
    )

    session = MagicMock()
    session.scalars.return_value.all.return_value = [peer, person]

    rows = _fetch_cohort_rows(
        session, subject=subject, kind="construction", use_city=False, city=""
    )
    assert [row.id for row in rows] == [10]


def test_missed_opportunities_peer_list_excludes_person_names_when_mocked():
    session = MagicMock()
    with __import__("unittest.mock", fromlist=["patch"]).patch(
        "pipeline.competitive_intel.tender_activity.get_top_competitors_for_company",
        return_value=[
            TopCompetitor(
                company_id=99,
                name="Khaleel Sumar Contracting Ltd",
                company_kind="construction",
                threat_score=70,
                threat_breakdown={"score": 70, "breakdown": [], "confidence": "medium"},
                similarity=0.7,
                total_projects=20,
                total_value=5_000_000,
                award_count=1,
            )
        ],
    ), __import__("unittest.mock", fromlist=["patch"]).patch(
        "pipeline.competitive_intel.tender_activity._subject_match_keys",
        return_value=set(),
    ):
        session.scalars.return_value.all.return_value = []
        result = get_missed_opportunities(session, company_id=8638)

    peer_names = {item.get("competitor_name") for item in result["items"]}
    assert "Tijana Sljivic" not in peer_names
    assert "Shalindro Dosanjh" not in peer_names
