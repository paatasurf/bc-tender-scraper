"""Local-Postgres tests for the Market person/entity firewall applied to
``pipeline.opportunity_discovery._load_award_candidates``'s peer-award
candidate query (PR-MO2A).

Before this fix, the ``peer_ids`` sub-query selected ``Company`` rows by
overlapping ``award_categories`` with no entity-role or person-name
filtering at all -- an applicant_alias, probable_person, or a
standalone/canonical row whose name still reads as an individual could
contribute a ``"peer_award"`` context bonus to opportunity-discovery
scoring, entirely outside the Market cohort's established firewall (see
``pipeline.competitive_intel.cohort``).

The fix reuses that firewall verbatim (``construction_company_analytics_clause()``
at the SQL level, ``filter_construction_peer_pool`` as the Python-side
post-filter) -- no new heuristics, no re-implementation.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from db.company_canonical_constants import (
    ENTITY_ROLE_APPLICANT_ALIAS,
    ENTITY_ROLE_CANONICAL,
    ENTITY_ROLE_PROBABLE_PERSON,
    ENTITY_ROLE_STANDALONE,
)
from db.models import Company, ContractAward
from pipeline.opportunity_discovery import _load_award_candidates
from tests.db_test_safety import require_local_test_database


@pytest.fixture()
def db_session():
    database_url = require_local_test_database()
    engine = create_engine(database_url, connect_args={"connect_timeout": 3})
    try:
        with engine.connect() as probe:
            probe.execute(text("SELECT 1"))
    except Exception:
        pytest.skip("Local Postgres unavailable")

    conn = engine.connect()
    outer = conn.begin()
    conn.execute(text("SET LOCAL lock_timeout = '10s'"))
    session = Session(bind=conn)
    try:
        yield session
    finally:
        session.close()
        if outer.is_active:
            outer.rollback()
        conn.close()
        engine.dispose()


def _make_company(session: Session, **overrides) -> Company:
    unique = uuid.uuid4().hex[:8]
    defaults: dict = dict(
        name=f"MO2A Test Co {unique}",
        entity_role=ENTITY_ROLE_STANDALONE,
        award_categories=["Construction"],
    )
    defaults.update(overrides)
    company = Company(**defaults)
    session.add(company)
    session.flush()
    return company


def _make_award(
    session: Session, *, company_id: int | None, **overrides
) -> ContractAward:
    unique = uuid.uuid4().hex[:8]
    defaults: dict = dict(
        source="test",
        external_id=f"mo2a-{unique}",
        title=f"MO2A Test Award {unique}",
        winner_company=f"Winner {unique}",
        company_id=company_id,
        award_date="2026-01-01",
        procurement_category="Construction",
        buyer_organization="",
    )
    defaults.update(overrides)
    award = ContractAward(**defaults)
    session.add(award)
    session.flush()
    return award


def _contexts_by_company(
    results: list[tuple[ContractAward, str]],
) -> dict[int | None, str]:
    return {award.company_id: context for award, context in results}


def test_applicant_alias_excluded_from_peer_award_candidates(db_session):
    subject = _make_company(db_session)
    alias = _make_company(db_session, entity_role=ENTITY_ROLE_APPLICANT_ALIAS)
    _make_award(db_session, company_id=alias.id)

    results = _load_award_candidates(db_session, subject, limit=20)

    assert alias.id not in _contexts_by_company(results)


def test_probable_person_excluded_from_peer_award_candidates(db_session):
    subject = _make_company(db_session)
    probable_person = _make_company(db_session, entity_role=ENTITY_ROLE_PROBABLE_PERSON)
    _make_award(db_session, company_id=probable_person.id)

    results = _load_award_candidates(db_session, subject, limit=20)

    assert probable_person.id not in _contexts_by_company(results)


def test_person_like_name_with_erroneous_standalone_role_excluded(db_session):
    """The confirmed Market-firewall leak pattern (PR-MARKET-2A): a row
    whose entity_role is "standalone" (not applicant_alias/probable_person
    -- so the SQL-level clause alone would miss it) but whose name still
    reads as an individual. filter_construction_peer_pool must still
    catch it here, exactly as it does for the Market cohort."""
    subject = _make_company(db_session)
    person = _make_company(
        db_session, name="Tijana Sljivic", entity_role=ENTITY_ROLE_STANDALONE
    )
    _make_award(db_session, company_id=person.id)

    results = _load_award_candidates(db_session, subject, limit=20)

    assert person.id not in _contexts_by_company(results)


def test_person_like_name_with_erroneous_canonical_role_excluded(db_session):
    subject = _make_company(db_session)
    person = _make_company(
        db_session, name="Khaleel Sumar", entity_role=ENTITY_ROLE_CANONICAL
    )
    _make_award(db_session, company_id=person.id)

    results = _load_award_candidates(db_session, subject, limit=20)

    assert person.id not in _contexts_by_company(results)


def test_legitimate_peer_company_retained_as_peer_award(db_session):
    subject = _make_company(db_session)
    peer = _make_company(db_session, name="Pacific Build Co Ltd")
    _make_award(db_session, company_id=peer.id)

    results = _load_award_candidates(db_session, subject, limit=20)

    assert _contexts_by_company(results).get(peer.id) == "peer_award"


def test_valid_and_invalid_peers_mixed_only_valid_survives(db_session):
    subject = _make_company(db_session)
    valid_peer = _make_company(db_session, name="Genuine Contracting Ltd")
    person_peer = _make_company(db_session, name="Shalindro Dosanjh")
    alias_peer = _make_company(db_session, entity_role=ENTITY_ROLE_APPLICANT_ALIAS)
    _make_award(db_session, company_id=valid_peer.id)
    _make_award(db_session, company_id=person_peer.id)
    _make_award(db_session, company_id=alias_peer.id)

    contexts = _contexts_by_company(
        _load_award_candidates(db_session, subject, limit=20)
    )

    assert contexts.get(valid_peer.id) == "peer_award"
    assert person_peer.id not in contexts
    assert alias_peer.id not in contexts


# ===================================================================
# Neighboring opportunity paths -- unchanged by this fix
# ===================================================================


def test_own_history_context_unaffected_by_firewall(db_session):
    """The subject's own award history is never filtered by entity_role
    -- the firewall only applies to the peer_award candidate query."""
    subject = _make_company(db_session, entity_role=ENTITY_ROLE_APPLICANT_ALIAS)
    own_award = _make_award(db_session, company_id=subject.id)

    contexts = _contexts_by_company(
        _load_award_candidates(db_session, subject, limit=20)
    )

    assert contexts.get(own_award.company_id) == "own_history"


def test_client_history_context_unaffected_by_firewall(db_session):
    """The buyer/client-history path queries ContractAward directly by
    buyer_organization text match -- untouched by this fix, and a
    person-like/alias company's award can still surface there exactly as
    before (this path was not in scope for PR-MO2A)."""
    unique = uuid.uuid4().hex[:8]
    client_name = f"City Of Mo2a Test {unique}"
    subject = _make_company(db_session, award_clients=[client_name])
    person_peer = _make_company(
        db_session, name="Shalindro Dosanjh", award_categories=[]
    )
    client_award = _make_award(
        db_session,
        company_id=person_peer.id,
        buyer_organization=client_name,
        procurement_category="Other",
    )

    contexts = _contexts_by_company(
        _load_award_candidates(db_session, subject, limit=20)
    )

    assert contexts.get(client_award.company_id) == "client_history"
