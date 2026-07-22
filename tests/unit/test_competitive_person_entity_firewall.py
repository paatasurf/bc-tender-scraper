"""PR-MARKET-2A: person/entity firewall for Market competitive output.

Guarantees that a person-like row -- whether it carries entity_role
"standalone", "applicant_alias", "probable_person", or was erroneously
left/marked "canonical" by the merge pipeline -- can never surface as a
Market peer in Top competitors, Potential opportunity gaps, or Competitor
fit-scoring coverage. Defense-in-depth across three independent layers:

1. pipeline.competitive_intel.cohort.filter_construction_peer_pool,
   applied immediately after the SQL fetch in _fetch_cohort_rows (the
   SQL-level company_analytics_entity_filter() only excludes
   applicant_alias/probable_person -- this Python-side filter is what
   catches a row with any other entity_role, including "canonical",
   whose name still reads as an individual).
2. The same filter, re-applied in build_market_cohort after cohort-type
   isolation and the quality gate -- belt-and-suspenders, normally a
   no-op on top of layer 1.
3. pipeline.competitive_intel.peers.select_top_competitors's final guard
   on the assembled TopCompetitor list, independent of entity_role --
   the last line of defense before get_top_competitors_for_company
   returns. get_missed_opportunities and get_competitor_tender_activity
   both source their peer list exclusively from that function, so a
   fix here is inherited by both automatically.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from db.company_canonical_constants import (
    ENTITY_ROLE_APPLICANT_ALIAS,
    ENTITY_ROLE_CANONICAL,
    ENTITY_ROLE_PROBABLE_PERSON,
    ENTITY_ROLE_STANDALONE,
)
from pipeline.company_name_heuristics import is_probable_person_name
from pipeline.competitive_intel.cohort import filter_construction_peer_pool
from pipeline.competitive_intel.peers import select_top_competitors
from pipeline.competitive_intel.service import get_top_competitors_for_company
from pipeline.competitive_intel.tender_activity import (
    get_competitor_tender_activity,
    get_missed_opportunities,
)
from pipeline.competitive_intel.types import (
    MarketCohort,
    ThreatScoreResult,
    TopCompetitor,
)
from tests.unit.competitive_fixtures import make_cip, make_company

_FAKE_THREAT = ThreatScoreResult(
    score=50, breakdown=[], reasons=[], confidence="medium", algorithm_version="test_v1"
)


# ===================================================================
# 1. filter_construction_peer_pool -- the row-level predicate itself
# ===================================================================


def test_standalone_person_like_name_excluded():
    person = make_company(
        id=10928, name="Tijana Sljivic", entity_role=ENTITY_ROLE_STANDALONE
    )
    business = make_company(
        id=10, name="Pacific Build Co Ltd", entity_role=ENTITY_ROLE_STANDALONE
    )
    filtered = filter_construction_peer_pool([person, business])
    ids = {row.id for row in filtered}
    assert 10928 not in ids
    assert 10 in ids


def test_canonical_person_like_name_excluded():
    """The confirmed production leak: a row erroneously left/marked
    "canonical" by the merge pipeline despite its name reading as an
    individual. Neither the SQL-level entity_role filter (which only
    excludes applicant_alias/probable_person) nor the old standalone-only
    post-filter caught this."""
    person = make_company(
        id=20001, name="Khaleel Sumar", entity_role=ENTITY_ROLE_CANONICAL
    )
    business = make_company(
        id=20002, name="Sumar Construction Ltd", entity_role=ENTITY_ROLE_CANONICAL
    )
    filtered = filter_construction_peer_pool([person, business])
    ids = {row.id for row in filtered}
    assert 20001 not in ids
    assert 20002 in ids


def test_alias_and_probable_person_roles_excluded_even_with_business_looking_name():
    """entity_role exclusion is independent of the name heuristic -- a
    business-looking name still gets dropped when its role is
    applicant_alias or probable_person."""
    alias = make_company(
        id=30001, name="ABC Construction Group", entity_role=ENTITY_ROLE_APPLICANT_ALIAS
    )
    probable_person = make_company(
        id=30002, name="XYZ Builders Inc", entity_role=ENTITY_ROLE_PROBABLE_PERSON
    )
    business = make_company(
        id=30003, name="Legit Contracting Ltd", entity_role=ENTITY_ROLE_STANDALONE
    )
    filtered = filter_construction_peer_pool([alias, probable_person, business])
    ids = {row.id for row in filtered}
    assert 30001 not in ids
    assert 30002 not in ids
    assert 30003 in ids


def test_legitimate_business_names_retained_regardless_of_role():
    """Real businesses with business markers (Ltd, Construction,
    Engineering, DBA, etc.) must never be dropped, in any entity_role."""
    rows = [
        make_company(
            id=1, name="Pacific Build Co Ltd", entity_role=ENTITY_ROLE_STANDALONE
        ),
        make_company(
            id=2, name="Sumar Construction Ltd", entity_role=ENTITY_ROLE_CANONICAL
        ),
        make_company(
            id=3, name="Jack Hui DBA: Pontem Group", entity_role=ENTITY_ROLE_STANDALONE
        ),
        make_company(
            id=4, name="Vantage Engineering Group", entity_role=ENTITY_ROLE_CANONICAL
        ),
    ]
    filtered = filter_construction_peer_pool(rows)
    assert {row.id for row in filtered} == {1, 2, 3, 4}


# ===================================================================
# 2. Defense-in-depth layer 3: the final guard on the assembled output,
#    proven by deliberately feeding it a "dirty" cohort/cohort-builder
#    that skipped layers 1-2 -- simulating an earlier layer failing.
# ===================================================================


def test_select_top_competitors_final_output_excludes_person_even_if_cohort_leaked_one():
    subject = make_company(id=1)
    subject_cip = make_cip(company_id=1)
    business = make_company(
        id=2, name="Real Builder Inc", entity_role=ENTITY_ROLE_STANDALONE
    )
    leaked_person = make_company(
        id=3, name="Khaleel Sumar", entity_role=ENTITY_ROLE_CANONICAL
    )
    cohort = MarketCohort(
        members=[business, leaked_person],
        definition="test",
        definition_key="sector_and_city",
        cohort_size=2,
    )
    peer_cips = {2: make_cip(company_id=2), 3: make_cip(company_id=3)}

    with (
        patch(
            "pipeline.competitive_intel.peers.build_activity_stats",
            return_value=MagicMock(),
        ),
        patch(
            "pipeline.competitive_intel.peers.compute_threat_score",
            return_value=_FAKE_THREAT,
        ),
    ):
        session = MagicMock()
        results = select_top_competitors(
            session,
            subject=subject,
            subject_cip=subject_cip,
            cohort=cohort,
            peer_cips=peer_cips,
            kind="construction",
            peer_limit=5,
        )

    names = {r.name for r in results}
    assert "Real Builder Inc" in names
    assert "Khaleel Sumar" not in names


def test_get_top_competitors_for_company_excludes_person_even_if_cohort_builder_leaks_one():
    """End-to-end through the public API: even if build_market_cohort
    itself were to return a dirty cohort (every upstream layer failing at
    once), get_top_competitors_for_company's output still cannot contain
    a person-like peer."""
    subject = make_company(id=1)
    subject_cip = make_cip(company_id=1)
    business = make_company(
        id=2, name="Real Builder Inc", entity_role=ENTITY_ROLE_STANDALONE
    )
    leaked_person = make_company(
        id=3, name="Khaleel Sumar", entity_role=ENTITY_ROLE_CANONICAL
    )
    dirty_cohort = MarketCohort(
        members=[business, leaked_person],
        definition="test",
        definition_key="sector_and_city",
        cohort_size=2,
    )

    session = MagicMock()
    session.get.return_value = subject

    with (
        patch("pipeline.competitive_intel.service.get_cip", return_value=subject_cip),
        patch(
            "pipeline.competitive_intel.service.build_market_cohort",
            return_value=dirty_cohort,
        ),
        patch(
            "pipeline.competitive_intel.peers.build_activity_stats",
            return_value=MagicMock(),
        ),
        patch(
            "pipeline.competitive_intel.peers.compute_threat_score",
            return_value=_FAKE_THREAT,
        ),
    ):
        peers = get_top_competitors_for_company(
            session, company_id=1, kind="construction"
        )

    names = {p.name for p in peers}
    assert "Real Builder Inc" in names
    assert "Khaleel Sumar" not in names


def test_end_to_end_standalone_person_like_row_excluded_via_real_build_market_cohort():
    """Regression test matching the exact confirmed production audit
    profile: a Company row with entity_role="standalone", name ==
    display_name, and is_probable_person_name(name) is True.

    Unlike the two tests above, this does NOT mock build_market_cohort --
    it feeds the raw, unfiltered fetch result a real build_market_cohort
    would receive from the database (via a mocked session.scalars) and
    lets _fetch_cohort_rows, apply_cohort_type_isolation, the quality
    gate, filter_construction_peer_pool, and select_top_competitors all
    run for real, proving the actual current-source runtime path cannot
    surface this row in get_top_competitors_for_company's final output.
    Only build_activity_stats/compute_threat_score -- unrelated to
    cohort/peer-selection -- are stubbed, exactly as in
    test_competitive_service.py's own integration tests."""
    subject = make_company(id=1, company_type="General Contractor")
    subject_cip = make_cip(company_id=1)

    business = make_company(
        id=2,
        name="Real Builder Inc",
        entity_role=ENTITY_ROLE_STANDALONE,
        company_type="General Contractor",
    )
    person = make_company(
        id=3,
        name="Khaleel Sumar",
        display_name="Khaleel Sumar",
        entity_role=ENTITY_ROLE_STANDALONE,
        company_type="General Contractor",
    )
    # Assert the fixture actually matches the reported production profile
    # before relying on it -- this is the exact case the production audit
    # found, not an approximation.
    assert person.entity_role == ENTITY_ROLE_STANDALONE
    assert person.name == person.display_name == "Khaleel Sumar"
    assert is_probable_person_name(person.name) is True

    company_by_id = {1: subject, 2: business, 3: person}

    session = MagicMock()
    # Every _fetch_cohort_rows call (city-gated, and widened if triggered)
    # returns this same raw, unfiltered pair -- exactly what a real SQL
    # fetch would hand back before any Python-side filtering runs.
    session.scalars.return_value.all.return_value = [business, person]
    # apply_cohort_type_isolation's _member_company_category looks up each
    # member's authoritative company_type via session.get when a session is
    # provided -- route it to the real fixture rows above.
    session.get.side_effect = lambda model, pk: company_by_id.get(pk)

    def fake_get_cip(session, *, company_id, kind, refresh=False):
        return make_cip(company_id=company_id)

    with (
        patch("pipeline.competitive_intel.service.get_cip", side_effect=fake_get_cip),
        patch(
            "pipeline.competitive_intel.peers.build_activity_stats",
            return_value=MagicMock(),
        ),
        patch(
            "pipeline.competitive_intel.peers.compute_threat_score",
            return_value=_FAKE_THREAT,
        ),
    ):
        peers = get_top_competitors_for_company(
            session, company_id=1, kind="construction"
        )

    names = {p.name for p in peers}
    assert "Real Builder Inc" in names
    assert "Khaleel Sumar" not in names


# ===================================================================
# 3. Potential opportunity gaps / Competitor fit-scoring coverage both
#    source their peer list exclusively from get_top_competitors_for_company
#    -- once that call is guaranteed clean (tests above), a clean peer set
#    passed through it is what actually reaches both endpoints, with no
#    separate peer-fetch path that could reintroduce a person-like row.
# ===================================================================


def test_competitor_activity_inherits_the_cleaned_peer_set():
    clean_peer = TopCompetitor(
        company_id=2,
        name="Real Builder Inc",
        company_kind="construction",
        threat_score=70,
        threat_breakdown={"score": 70, "breakdown": [], "confidence": "medium"},
        similarity=0.8,
        total_projects=10,
        total_value=1_000_000,
        award_count=2,
    )

    match_row = MagicMock()
    match_row.company_id = 2
    match_row.tender_source = "federal"
    match_row.tender_id = 1
    match_row.score = 90
    match_row.created_at = datetime.now(timezone.utc) - timedelta(days=10)

    tender_row = MagicMock()
    tender_row.id = 1
    tender_row.title = "Test Tender"
    tender_row.estimated_value_numeric = 100_000
    tender_row.ai_budget_estimate = ""
    tender_row.estimated_value = ""

    session = MagicMock()
    session.scalars.return_value.all.side_effect = [[match_row], [tender_row]]

    with patch(
        "pipeline.competitive_intel.tender_activity.get_top_competitors_for_company",
        return_value=[clean_peer],
    ):
        activity = get_competitor_tender_activity(session, company_id=1)

    names = {row["name"] for row in activity["competitors"]}
    assert names == {"Real Builder Inc"}


def test_missed_opportunities_inherits_the_cleaned_peer_set():
    clean_peer = TopCompetitor(
        company_id=2,
        name="Real Builder Inc",
        company_kind="construction",
        threat_score=70,
        threat_breakdown={"score": 70, "breakdown": [], "confidence": "medium"},
        similarity=0.8,
        total_projects=10,
        total_value=1_000_000,
        award_count=2,
    )

    match_row = MagicMock()
    match_row.company_id = 2
    match_row.tender_source = "federal"
    match_row.tender_id = 1
    match_row.score = 90
    match_row.created_at = datetime.now(timezone.utc) - timedelta(days=10)

    tender_row = MagicMock()
    tender_row.id = 1
    tender_row.title = "Test Tender"
    tender_row.is_open = False
    tender_row.closing_at = None
    tender_row.estimated_value_numeric = 100_000
    tender_row.ai_budget_estimate = ""
    tender_row.estimated_value = ""

    session = MagicMock()
    session.scalars.return_value.all.side_effect = [[match_row], [tender_row]]

    with (
        patch(
            "pipeline.competitive_intel.tender_activity.get_top_competitors_for_company",
            return_value=[clean_peer],
        ),
        patch(
            "pipeline.competitive_intel.tender_activity._subject_match_keys",
            return_value=set(),
        ),
    ):
        result = get_missed_opportunities(session, company_id=1)

    names = {item["competitor_name"] for item in result["items"]}
    assert names == {"Real Builder Inc"}
