"""Unit tests for missed opportunities and competitor tender activity.

Sections:
  1. get_missed_opportunities argument validation -- no DB (MagicMock
     session; validation must raise before any session interaction).
  2. get_missed_opportunities behavior -- local Postgres only (real
     Tender/CommercialTender/TenderMatch rows; get_top_competitors_for_company
     and, where noted, _subject_match_keys are mocked -- this PR does not
     touch competitor selection/scoring, only the missed-opportunities
     grouping/filtering/response contract).
  3. get_competitor_tender_activity -- mock-based tests, no DATABASE_URL
     required. Pre-existing tests kept verbatim (scoring/cohort/company
     selection untouched); PR-MARKET-1A adds further mock-based tests
     proving the raw/unique/value/evidence/candidate-limit contract so
     the core guarantees run as real (non-skipped) unit tests even
     without a local Postgres instance.
  4. get_competitor_tender_activity -- the same PR-MARKET-1A contract,
     re-verified with local-Postgres fixtures for exact-value coverage
     (the MagicMock multi-call sequencing in Section 3 is precise but
     fragile -- see the note above _tender_row below).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session

from db.models import CommercialTender, Tender, TenderMatch
from pipeline.competitive_intel.tender_activity import (
    TenderActivityError,
    get_competitor_tender_activity,
    get_missed_opportunities,
)
from pipeline.competitive_intel.types import TopCompetitor
from tests.db_test_safety import require_local_test_database

AS_OF = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
# Deliberately far from the real system clock (this suite runs in 2026) --
# used to prove the lookback window is anchored to the injected as_of, not
# to wall-clock "now".
FIXED_HISTORICAL_AS_OF = datetime(2020, 1, 1, 12, 0, tzinfo=timezone.utc)


def _peer(company_id: int, name: str, threat: int = 70) -> TopCompetitor:
    return TopCompetitor(
        company_id=company_id,
        name=name,
        company_kind="construction",
        threat_score=threat,
        threat_breakdown={"score": threat, "breakdown": [], "confidence": "medium"},
        similarity=0.8,
        total_projects=10,
        total_value=1_000_000,
        award_count=2,
    )


def _match(
    *,
    company_id: int,
    tender_source: str = "federal",
    tender_id: int = 1,
    score: int = 80,
):
    row = MagicMock()
    row.company_id = company_id
    row.tender_source = tender_source
    row.tender_id = tender_id
    row.score = score
    row.created_at = datetime.now(timezone.utc) - timedelta(days=10)
    return row


# ===================================================================
# 1. get_missed_opportunities argument validation -- no DB
# ===================================================================


def test_as_of_naive_raises_before_any_session_interaction():
    session = MagicMock()
    with pytest.raises(TenderActivityError):
        get_missed_opportunities(session, company_id=1, as_of=datetime(2026, 1, 1))
    assert session.mock_calls == []


def test_as_of_wrong_type_raises_before_any_session_interaction():
    session = MagicMock()
    with pytest.raises(TenderActivityError):
        get_missed_opportunities(session, company_id=1, as_of="2026-01-01")
    assert session.mock_calls == []


def test_missed_opportunities_peer_list_excludes_person_names_when_mocked():
    """Pre-existing regression, kept: person-name peer filtering happens
    upstream in get_top_competitors_for_company -- this PR never touches
    that collaborator."""
    session = MagicMock()
    with (
        patch(
            "pipeline.competitive_intel.tender_activity.get_top_competitors_for_company",
            return_value=[
                TopCompetitor(
                    company_id=99,
                    name="Khaleel Sumar Contracting Ltd",
                    company_kind="construction",
                    threat_score=70,
                    threat_breakdown={
                        "score": 70,
                        "breakdown": [],
                        "confidence": "medium",
                    },
                    similarity=0.7,
                    total_projects=20,
                    total_value=5_000_000,
                    award_count=1,
                )
            ],
        ),
        patch(
            "pipeline.competitive_intel.tender_activity._subject_match_keys",
            return_value=set(),
        ),
    ):
        session.scalars.return_value.all.return_value = []
        result = get_missed_opportunities(session, company_id=8638)

    peer_names = {item.get("competitor_name") for item in result["items"]}
    assert "Tijana Sljivic" not in peer_names
    assert "Shalindro Dosanjh" not in peer_names


def test_no_peers_returns_empty_response_with_full_metadata():
    session = MagicMock()
    with patch(
        "pipeline.competitive_intel.tender_activity.get_top_competitors_for_company",
        return_value=[],
    ):
        result = get_missed_opportunities(session, company_id=1, as_of=AS_OF)

    assert result["items"] == []
    assert result["semantics_version"] == "potential_opportunity_gap_v1"
    assert result["unique_tender_count"] == 0
    assert result["raw_peer_match_count"] == 0
    assert result["as_of"] == AS_OF.isoformat()


def test_non_construction_kind_returns_empty_response_with_full_metadata():
    session = MagicMock()
    result = get_missed_opportunities(
        session, company_id=1, kind="architecture", as_of=AS_OF
    )
    assert result["items"] == []
    assert result["semantics_version"] == "potential_opportunity_gap_v1"
    assert session.mock_calls == []


# ===================================================================
# 2. get_missed_opportunities behavior -- local Postgres only
# ===================================================================


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


def _make_tender(session: Session, **overrides) -> Tender:
    defaults: dict = dict(
        title="MO1A Test Tender",
        organization="Test Org",
        url=f"https://example.test/mo1a/{uuid.uuid4().hex}",
        source="test",
        tender_id="",
        is_open=False,
        closing_at=None,
    )
    defaults.update(overrides)
    tender = Tender(**defaults)
    session.add(tender)
    session.flush()
    return tender


def _make_commercial_tender(session: Session, **overrides) -> CommercialTender:
    defaults: dict = dict(
        title="MO1A Test Commercial Tender",
        url=f"https://example.test/mo1a-commercial/{uuid.uuid4().hex}",
        source="test",
        tender_id="",
        is_open=False,
        closing_at=None,
    )
    defaults.update(overrides)
    tender = CommercialTender(**defaults)
    session.add(tender)
    session.flush()
    return tender


def _make_match(
    session: Session,
    *,
    company_id: int,
    tender_source: str,
    tender_id: int,
    score: int,
    created_at: datetime | None = None,
    kind: str = "construction",
) -> TenderMatch:
    match = TenderMatch(
        company_kind=kind,
        company_id=company_id,
        tender_source=tender_source,
        tender_id=tender_id,
        score=score,
        created_at=created_at or (AS_OF - timedelta(days=10)),
    )
    session.add(match)
    session.flush()
    return match


class _PeersAndSubjectKeysPatched:
    """Combined context manager: patches get_top_competitors_for_company
    (always) and, when subject_keys is given, _subject_match_keys too --
    this PR does not touch either collaborator's own behavior."""

    def __init__(self, peers, subject_keys=None):
        self._competitors_patch = patch(
            "pipeline.competitive_intel.tender_activity.get_top_competitors_for_company",
            return_value=peers,
        )
        self._subject_keys_patch = (
            patch(
                "pipeline.competitive_intel.tender_activity._subject_match_keys",
                return_value=subject_keys,
            )
            if subject_keys is not None
            else None
        )

    def __enter__(self):
        self._competitors_patch.start()
        if self._subject_keys_patch is not None:
            self._subject_keys_patch.start()
        return self

    def __exit__(self, *exc_info):
        if self._subject_keys_patch is not None:
            self._subject_keys_patch.stop()
        self._competitors_patch.stop()
        return False


def _patched(peers, subject_keys=None):
    return _PeersAndSubjectKeysPatched(peers, subject_keys=subject_keys)


def test_two_peers_on_same_tender_collapse_to_one_item(db_session):
    tender = _make_tender(db_session, is_open=False)
    peers = [_peer(2, "Alpha", 80), _peer(3, "Beta", 60)]
    _make_match(
        db_session, company_id=2, tender_source="federal", tender_id=tender.id, score=90
    )
    _make_match(
        db_session, company_id=3, tender_source="federal", tender_id=tender.id, score=70
    )

    with _patched(peers, subject_keys=set()):
        result = get_missed_opportunities(db_session, company_id=1, as_of=AS_OF)

    assert len(result["items"]) == 1
    item = result["items"][0]
    assert item["competitor_count"] == 2
    assert len(item["competitors"]) == 2
    assert item["competitor_company_id"] == 2
    assert item["competitor_name"] == "Alpha"
    assert item["competitor_threat_score"] == 80
    assert item["match_score"] == 90
    assert result["unique_tender_count"] == 1
    assert result["raw_peer_match_count"] == 2


def test_deterministic_tie_break_by_company_id(db_session):
    tender = _make_tender(db_session, is_open=False)
    peers = [_peer(5, "Gamma", 80), _peer(2, "Delta", 80)]
    _make_match(
        db_session, company_id=5, tender_source="federal", tender_id=tender.id, score=90
    )
    _make_match(
        db_session, company_id=2, tender_source="federal", tender_id=tender.id, score=90
    )

    with _patched(peers, subject_keys=set()):
        result = get_missed_opportunities(db_session, company_id=1, as_of=AS_OF)

    assert result["items"][0]["competitor_company_id"] == 2


def test_deterministic_tie_break_by_match_score_when_threat_equal(db_session):
    tender = _make_tender(db_session, is_open=False)
    peers = [_peer(5, "Gamma", 80), _peer(2, "Delta", 80)]
    _make_match(
        db_session, company_id=5, tender_source="federal", tender_id=tender.id, score=95
    )
    _make_match(
        db_session, company_id=2, tender_source="federal", tender_id=tender.id, score=90
    )

    with _patched(peers, subject_keys=set()):
        result = get_missed_opportunities(db_session, company_id=1, as_of=AS_OF)

    assert result["items"][0]["competitor_company_id"] == 5  # higher match_score wins


def test_limit_applied_after_grouping_not_before(db_session, monkeypatch):
    monkeypatch.setattr("pipeline.competitive_intel.tender_activity.MISSED_LIMIT", 2)
    shared_tender = _make_tender(db_session, is_open=False)
    tender_b = _make_tender(db_session, is_open=False)
    tender_c = _make_tender(db_session, is_open=False)

    peers = [_peer(i, f"Peer{i}", 50 + i) for i in range(2, 7)]  # 5 peers
    for i in range(2, 7):
        _make_match(
            db_session,
            company_id=i,
            tender_source="federal",
            tender_id=shared_tender.id,
            score=70,
        )
    _make_match(
        db_session,
        company_id=2,
        tender_source="federal",
        tender_id=tender_b.id,
        score=95,
    )
    _make_match(
        db_session,
        company_id=3,
        tender_source="federal",
        tender_id=tender_c.id,
        score=75,
    )

    with _patched(peers, subject_keys=set()):
        result = get_missed_opportunities(db_session, company_id=1, as_of=AS_OF)

    assert result["unique_tender_count"] == 3
    assert len(result["items"]) == 2
    tender_ids_in_items = [item["tender_id"] for item in result["items"]]
    assert len(tender_ids_in_items) == len(set(tender_ids_in_items))


def test_open_tender_excluded(db_session):
    tender = _make_tender(db_session, is_open=True)
    peers = [_peer(2, "Alpha", 80)]
    _make_match(
        db_session, company_id=2, tender_source="federal", tender_id=tender.id, score=90
    )

    with _patched(peers, subject_keys=set()):
        result = get_missed_opportunities(db_session, company_id=1, as_of=AS_OF)

    assert result["items"] == []


def test_closed_flag_but_future_closing_at_excluded(db_session):
    future = AS_OF + timedelta(days=5)
    tender = _make_tender(db_session, is_open=False, closing_at=future)
    peers = [_peer(2, "Alpha", 80)]
    _make_match(
        db_session, company_id=2, tender_source="federal", tender_id=tender.id, score=90
    )

    with _patched(peers, subject_keys=set()):
        result = get_missed_opportunities(db_session, company_id=1, as_of=AS_OF)

    assert result["items"] == []


def test_closed_tender_with_past_closing_at_included(db_session):
    past = AS_OF - timedelta(days=5)
    tender = _make_tender(db_session, is_open=False, closing_at=past)
    peers = [_peer(2, "Alpha", 80)]
    _make_match(
        db_session, company_id=2, tender_source="federal", tender_id=tender.id, score=90
    )

    with _patched(peers, subject_keys=set()):
        result = get_missed_opportunities(db_session, company_id=1, as_of=AS_OF)

    assert len(result["items"]) == 1


def test_closed_tender_with_no_closing_at_included(db_session):
    tender = _make_tender(db_session, is_open=False, closing_at=None)
    peers = [_peer(2, "Alpha", 80)]
    _make_match(
        db_session, company_id=2, tender_source="federal", tender_id=tender.id, score=90
    )

    with _patched(peers, subject_keys=set()):
        result = get_missed_opportunities(db_session, company_id=1, as_of=AS_OF)

    assert len(result["items"]) == 1


def test_missing_tender_row_excluded(db_session):
    peers = [_peer(2, "Alpha", 80)]
    _make_match(
        db_session,
        company_id=2,
        tender_source="federal",
        tender_id=999_999_999,
        score=90,
    )

    with _patched(peers, subject_keys=set()):
        result = get_missed_opportunities(db_session, company_id=1, as_of=AS_OF)

    assert result["items"] == []


def test_subject_recent_match_excludes_tender_end_to_end(db_session):
    """Exercises the real _subject_match_keys (not mocked) to prove
    subject-exclusion still works after the grouping rewrite."""
    tender = _make_tender(db_session, is_open=False)
    peers = [_peer(2, "Alpha", 80)]
    _make_match(
        db_session, company_id=2, tender_source="federal", tender_id=tender.id, score=90
    )
    _make_match(
        db_session, company_id=1, tender_source="federal", tender_id=tender.id, score=95
    )

    with patch(
        "pipeline.competitive_intel.tender_activity.get_top_competitors_for_company",
        return_value=peers,
    ):
        result = get_missed_opportunities(db_session, company_id=1, as_of=AS_OF)

    assert result["items"] == []


def test_as_of_defaults_to_utc_now_when_omitted(db_session):
    with patch(
        "pipeline.competitive_intel.tender_activity.get_top_competitors_for_company",
        return_value=[],
    ):
        before = datetime.now(timezone.utc)
        result = get_missed_opportunities(db_session, company_id=1)
        after = datetime.now(timezone.utc)

    as_of_value = datetime.fromisoformat(result["as_of"])
    assert before <= as_of_value <= after


def test_as_of_fixed_aware_value_is_used_and_injectable(db_session):
    fixed = datetime(2026, 3, 15, 12, 0, tzinfo=timezone.utc)
    with patch(
        "pipeline.competitive_intel.tender_activity.get_top_competitors_for_company",
        return_value=[],
    ):
        result = get_missed_opportunities(db_session, company_id=1, as_of=fixed)

    assert result["as_of"] == fixed.isoformat()


def test_lookback_cutoff_anchored_to_injected_as_of_not_system_clock(db_session):
    """Regression test: the lookback window must be computed from the
    injected as_of, not from wall-clock "now". FIXED_HISTORICAL_AS_OF is
    deliberately far from the real system clock (this suite runs in
    2026), so a bug that re-introduces datetime.now(timezone.utc) as the
    cutoff anchor would push the real cutoff years later than any match
    timestamp used here -- excluding the "inside" match too, and failing
    this test."""
    tender_inside = _make_tender(db_session, is_open=False)
    tender_outside = _make_tender(db_session, is_open=False)
    peers = [_peer(2, "Inside", 80), _peer(3, "Outside", 80)]

    _make_match(
        db_session,
        company_id=2,
        tender_source="federal",
        tender_id=tender_inside.id,
        score=90,
        created_at=FIXED_HISTORICAL_AS_OF - timedelta(days=89),
    )
    _make_match(
        db_session,
        company_id=3,
        tender_source="federal",
        tender_id=tender_outside.id,
        score=90,
        created_at=FIXED_HISTORICAL_AS_OF - timedelta(days=91),
    )

    with _patched(peers, subject_keys=set()):
        result = get_missed_opportunities(
            db_session, company_id=1, as_of=FIXED_HISTORICAL_AS_OF
        )

    tender_ids = {item["tender_id"] for item in result["items"]}
    assert tender_inside.id in tender_ids
    assert tender_outside.id not in tender_ids


def test_items_deterministically_ordered_by_threat_then_value(db_session):
    tender_low = _make_tender(db_session, is_open=False, estimated_value_numeric=100.0)
    tender_high = _make_tender(
        db_session, is_open=False, estimated_value_numeric=999_999.0
    )
    peers = [_peer(2, "LowThreat", 30), _peer(3, "HighThreat", 90)]
    _make_match(
        db_session,
        company_id=2,
        tender_source="federal",
        tender_id=tender_low.id,
        score=90,
    )
    _make_match(
        db_session,
        company_id=3,
        tender_source="federal",
        tender_id=tender_high.id,
        score=90,
    )

    with _patched(peers, subject_keys=set()):
        result = get_missed_opportunities(db_session, company_id=1, as_of=AS_OF)

    assert result["items"][0]["competitor_company_id"] == 3


def test_full_tie_break_by_tender_source_when_threat_and_value_equal(db_session):
    same_value = 500_000.0
    tender_federal = _make_tender(
        db_session, is_open=False, estimated_value_numeric=same_value
    )
    tender_commercial = _make_commercial_tender(
        db_session, is_open=False, estimated_value_numeric=same_value
    )
    peers = [_peer(2, "SameThreat", 70)]
    _make_match(
        db_session,
        company_id=2,
        tender_source="federal",
        tender_id=tender_federal.id,
        score=90,
    )
    _make_match(
        db_session,
        company_id=2,
        tender_source="commercial",
        tender_id=tender_commercial.id,
        score=90,
    )

    with _patched(peers, subject_keys=set()):
        result = get_missed_opportunities(db_session, company_id=1, as_of=AS_OF)

    assert [item["tender_source"] for item in result["items"]] == [
        "commercial",
        "federal",
    ]


def test_full_tie_break_by_tender_id_when_source_and_value_equal(db_session):
    same_value = 250_000.0
    tender_low_id = _make_tender(
        db_session, is_open=False, estimated_value_numeric=same_value
    )
    tender_high_id = _make_tender(
        db_session, is_open=False, estimated_value_numeric=same_value
    )
    assert tender_low_id.id < tender_high_id.id
    peers = [_peer(2, "SameThreat", 70)]
    _make_match(
        db_session,
        company_id=2,
        tender_source="federal",
        tender_id=tender_low_id.id,
        score=90,
    )
    _make_match(
        db_session,
        company_id=2,
        tender_source="federal",
        tender_id=tender_high_id.id,
        score=90,
    )

    with _patched(peers, subject_keys=set()):
        result = get_missed_opportunities(db_session, company_id=1, as_of=AS_OF)

    assert [item["tender_id"] for item in result["items"]] == [
        tender_low_id.id,
        tender_high_id.id,
    ]


def test_raw_peer_match_count_counts_rows_before_subject_and_source_filtering(
    db_session,
):
    """raw_peer_match_count must be the count of raw strong-fit
    TenderMatch rows within the lookback window, before subject-match
    exclusion and non-construction-source filtering -- distinct from
    unique_tender_count, which is post-filter and pre-MISSED_LIMIT."""
    tender = _make_tender(db_session, is_open=False)
    peers = [_peer(2, "Alpha", 80)]

    # Counted in raw_peer_match_count (strong-fit peer match), but
    # excluded from grouping/items because it duplicates the subject's
    # own recent match on the same tender.
    _make_match(
        db_session, company_id=2, tender_source="federal", tender_id=tender.id, score=90
    )
    _make_match(
        db_session, company_id=1, tender_source="federal", tender_id=tender.id, score=95
    )
    # Counted in raw_peer_match_count, but excluded from grouping/items
    # because "internal" is not an eligible construction source.
    _make_match(
        db_session,
        company_id=2,
        tender_source="internal",
        tender_id=999_999_998,
        score=90,
    )

    with patch(
        "pipeline.competitive_intel.tender_activity.get_top_competitors_for_company",
        return_value=peers,
    ):
        result = get_missed_opportunities(db_session, company_id=1, as_of=AS_OF)

    assert result["raw_peer_match_count"] == 2
    assert result["unique_tender_count"] == 0
    assert result["items"] == []


def test_unique_tender_count_reflects_eligible_tenders_before_limit(
    db_session, monkeypatch
):
    monkeypatch.setattr("pipeline.competitive_intel.tender_activity.MISSED_LIMIT", 1)
    tender_a = _make_tender(db_session, is_open=False)
    tender_b = _make_tender(db_session, is_open=False)
    peers = [_peer(2, "Alpha", 80)]
    _make_match(
        db_session,
        company_id=2,
        tender_source="federal",
        tender_id=tender_a.id,
        score=90,
    )
    _make_match(
        db_session,
        company_id=2,
        tender_source="federal",
        tender_id=tender_b.id,
        score=90,
    )

    with _patched(peers, subject_keys=set()):
        result = get_missed_opportunities(db_session, company_id=1, as_of=AS_OF)

    assert result["unique_tender_count"] == 2
    assert len(result["items"]) == 1


def test_metadata_disclaimer_and_evidence_statuses_present(db_session):
    tender = _make_tender(db_session, is_open=False)
    peers = [_peer(2, "Alpha", 80)]
    _make_match(
        db_session, company_id=2, tender_source="federal", tender_id=tender.id, score=90
    )

    with _patched(peers, subject_keys=set()):
        result = get_missed_opportunities(db_session, company_id=1, as_of=AS_OF)

    assert result["semantics_version"] == "potential_opportunity_gap_v1"
    assert "does not prove" in result["evidence_disclaimer"]
    assert result["unique_tender_count"] == 1
    assert result["raw_peer_match_count"] == 1

    item = result["items"][0]
    assert item["opportunity_status"] == "potential_gap"
    assert item["evidence_status"] == "peer_fit_only"
    assert item["participation_status"] == "unknown"
    assert item["subject_fit_status"] == "no_recent_match_evidence"


def test_commercial_source_group_and_closed_filter_also_apply(db_session):
    tender = _make_commercial_tender(db_session, is_open=False)
    peers = [_peer(2, "Alpha", 80)]
    _make_match(
        db_session,
        company_id=2,
        tender_source="commercial",
        tender_id=tender.id,
        score=90,
    )

    with _patched(peers, subject_keys=set()):
        result = get_missed_opportunities(db_session, company_id=1, as_of=AS_OF)

    assert len(result["items"]) == 1
    assert result["items"][0]["tender_source"] == "commercial"


def test_zero_session_mutations_after_call(db_session):
    tender = _make_tender(db_session, is_open=False)
    peers = [_peer(2, "Alpha", 80)]
    _make_match(
        db_session, company_id=2, tender_source="federal", tender_id=tender.id, score=90
    )
    db_session.flush()

    with _patched(peers, subject_keys=set()):
        get_missed_opportunities(db_session, company_id=1, as_of=AS_OF)

    assert not db_session.new
    assert not db_session.dirty
    assert not db_session.deleted


def test_get_missed_opportunities_never_commits(db_session):
    tender = _make_tender(db_session, is_open=False)
    peers = [_peer(2, "Alpha", 80)]
    _make_match(
        db_session, company_id=2, tender_source="federal", tender_id=tender.id, score=90
    )

    commits: list[int] = []

    def on_commit(conn):
        commits.append(1)

    connection = db_session.connection()
    event.listen(connection, "commit", on_commit)
    try:
        with _patched(peers, subject_keys=set()):
            get_missed_opportunities(db_session, company_id=1, as_of=AS_OF)
    finally:
        event.remove(connection, "commit", on_commit)

    assert commits == []


# ===================================================================
# 3. get_competitor_tender_activity -- unchanged by this PR
# ===================================================================


def test_competitor_activity_sorted_by_match_count():
    session = MagicMock()
    peers = [_peer(2, "Alpha", 60), _peer(3, "Beta", 55)]
    alpha_matches = [
        _match(company_id=2, tender_id=1),
        _match(company_id=2, tender_id=2),
    ]
    beta_matches = [_match(company_id=3, tender_id=3)]
    tender_one = MagicMock()
    tender_one.id = 1
    tender_one.title = "T1"
    tender_one.estimated_value_numeric = 100_000
    tender_one.ai_budget_estimate = ""
    tender_one.estimated_value = ""
    tender_three = MagicMock()
    tender_three.id = 3
    tender_three.title = "T3"
    tender_three.estimated_value_numeric = 50_000
    tender_three.ai_budget_estimate = ""
    tender_three.estimated_value = ""

    session.scalars.return_value.all.side_effect = [
        alpha_matches,
        [tender_one],
        beta_matches,
        [tender_three],
    ]

    with patch(
        "pipeline.competitive_intel.tender_activity.get_top_competitors_for_company",
        return_value=peers,
    ):
        result = get_competitor_tender_activity(session, company_id=1)

    competitors = result["competitors"]
    assert len(competitors) == 2
    assert competitors[0]["name"] == "Alpha"
    assert competitors[0]["match_count"] == 2
    assert competitors[1]["name"] == "Beta"
    assert competitors[1]["match_count"] == 1

    # PR-MARKET-1A additive evidence contract: present on every item,
    # match_count is numerically identical to its raw_match_count alias.
    for competitor in competitors:
        assert competitor["match_count"] == competitor["raw_match_count"]
        assert competitor["evidence_status"] == "scored_fit_cache_not_participation"
        assert "never" in competitor["evidence_disclaimer"].lower()
        assert isinstance(competitor["candidate_limit"], int)
        assert isinstance(competitor["candidate_limit_saturated"], bool)
        assert isinstance(competitor["unique_tender_match_count"], int)


def test_competitor_activity_breaks_equal_totals_by_company_id():
    session = MagicMock()
    peers = [_peer(3, "Higher ID"), _peer(2, "Lower ID")]
    first_match = [_match(company_id=3, tender_id=3)]
    second_match = [_match(company_id=2, tender_id=2)]
    first_tender = MagicMock(
        id=3,
        title="T3",
        estimated_value_numeric=100_000,
        ai_budget_estimate="",
        estimated_value="",
    )
    second_tender = MagicMock(
        id=2,
        title="T2",
        estimated_value_numeric=100_000,
        ai_budget_estimate="",
        estimated_value="",
    )
    session.scalars.return_value.all.side_effect = [
        first_match,
        [first_tender],
        second_match,
        [second_tender],
    ]

    with patch(
        "pipeline.competitive_intel.tender_activity.get_top_competitors_for_company",
        return_value=peers,
    ):
        result = get_competitor_tender_activity(session, company_id=1)

    assert [row["company_id"] for row in result["competitors"]] == [2, 3]


def test_competitor_activity_excludes_profile_company():
    session = MagicMock()
    peers = [_peer(1, "Self Co", 80), _peer(2, "Rival", 70)]
    rival_matches = [_match(company_id=2, tender_id=5)]
    tender = MagicMock()
    tender.id = 5
    tender.title = "Bridge Repair"
    tender.estimated_value_numeric = 250_000
    tender.ai_budget_estimate = ""
    tender.estimated_value = ""

    session.scalars.return_value.all.side_effect = [rival_matches, [tender]]

    with patch(
        "pipeline.competitive_intel.tender_activity.get_top_competitors_for_company",
        return_value=peers,
    ):
        result = get_competitor_tender_activity(session, company_id=1)

    competitors = result["competitors"]
    assert len(competitors) == 1
    assert competitors[0]["company_id"] == 2
    assert competitors[0]["name"] == "Rival"


# ===================================================================
# 3b. PR-MARKET-1A: honest evidence contract for
#     get_competitor_tender_activity -- mock-based, no DATABASE_URL
#     required. Mirrors the local-Postgres tests in Section 4 below but
#     runs as a real (non-skipped) unit test everywhere, by driving
#     session.scalars(...).all() with an explicit side_effect sequence
#     matching the exact call order in get_competitor_tender_activity:
#     one call for the TenderMatch cache rows per peer, then one call
#     per *unique* (tender_source, tender_id) the first time it is
#     seen (a repeat key hits the in-memory cache and issues no call).
# ===================================================================


def _tender_row(*, id: int, value: float):
    tender = MagicMock()
    tender.id = id
    tender.title = f"T{id}"
    tender.estimated_value_numeric = value
    tender.ai_budget_estimate = ""
    tender.estimated_value = ""
    return tender


def test_competitor_activity_duplicate_raw_rows_dedupe_value_mock():
    """Two raw TenderMatch rows for the SAME tender: both count toward
    raw_match_count, but the tender's value is only loaded/added once --
    the second row hits seen_keys and never issues a second tender
    lookup, let alone double-counts the value."""
    session = MagicMock()
    peers = [_peer(2, "Alpha", 60)]
    matches = [
        _match(company_id=2, tender_id=1, score=90),
        _match(company_id=2, tender_id=1, score=80),
    ]
    tender_one = _tender_row(id=1, value=100_000.0)
    session.scalars.return_value.all.side_effect = [matches, [tender_one]]

    with patch(
        "pipeline.competitive_intel.tender_activity.get_top_competitors_for_company",
        return_value=peers,
    ):
        result = get_competitor_tender_activity(session, company_id=1)

    competitor = result["competitors"][0]
    assert competitor["raw_match_count"] == 2
    assert competitor["unique_tender_match_count"] == 1
    assert competitor["raw_match_count"] > competitor["unique_tender_match_count"]
    assert competitor["total_tender_value"] == 100_000.0


def test_competitor_activity_match_count_equals_raw_match_count_mock():
    session = MagicMock()
    peers = [_peer(2, "Alpha", 60)]
    matches = [
        _match(company_id=2, tender_id=1, score=90),
        _match(company_id=2, tender_id=2, score=85),
        _match(company_id=2, tender_id=3, score=80),
    ]
    tenders = [_tender_row(id=i, value=10_000.0) for i in (1, 2, 3)]
    session.scalars.return_value.all.side_effect = [matches, *([t] for t in tenders)]

    with patch(
        "pipeline.competitive_intel.tender_activity.get_top_competitors_for_company",
        return_value=peers,
    ):
        result = get_competitor_tender_activity(session, company_id=1)

    competitor = result["competitors"][0]
    assert competitor["match_count"] == competitor["raw_match_count"] == 3


def test_competitor_activity_candidate_limit_uses_imported_constant_mock():
    from pipeline.ai_matching import HYBRID_AI_CANDIDATE_LIMIT

    session = MagicMock()
    peers = [_peer(2, "Alpha", 60)]
    matches = [_match(company_id=2, tender_id=1)]
    tender = _tender_row(id=1, value=10_000.0)
    session.scalars.return_value.all.side_effect = [matches, [tender]]

    with patch(
        "pipeline.competitive_intel.tender_activity.get_top_competitors_for_company",
        return_value=peers,
    ):
        result = get_competitor_tender_activity(session, company_id=1)

    competitor = result["competitors"][0]
    assert competitor["candidate_limit"] == HYBRID_AI_CANDIDATE_LIMIT
    assert competitor["candidate_limit_saturated"] is False


def test_competitor_activity_saturation_flag_true_at_limit_mock(monkeypatch):
    """Saturation is driven by the live imported constant, not a
    hardcoded 20 -- lowering it here via monkeypatch on the module
    attribute proves the module reads the constant, not a copy."""
    monkeypatch.setattr(
        "pipeline.competitive_intel.tender_activity.HYBRID_AI_CANDIDATE_LIMIT", 2
    )
    session = MagicMock()
    peers = [_peer(2, "Alpha", 60)]
    matches = [
        _match(company_id=2, tender_id=1, score=90),
        _match(company_id=2, tender_id=2, score=85),
    ]
    tender_one = _tender_row(id=1, value=10_000.0)
    tender_two = _tender_row(id=2, value=20_000.0)
    session.scalars.return_value.all.side_effect = [
        matches,
        [tender_one],
        [tender_two],
    ]

    with patch(
        "pipeline.competitive_intel.tender_activity.get_top_competitors_for_company",
        return_value=peers,
    ):
        result = get_competitor_tender_activity(session, company_id=1)

    competitor = result["competitors"][0]
    assert competitor["raw_match_count"] == 2
    assert competitor["candidate_limit"] == 2
    assert competitor["candidate_limit_saturated"] is True


def test_competitor_activity_not_saturated_below_limit_mock(monkeypatch):
    monkeypatch.setattr(
        "pipeline.competitive_intel.tender_activity.HYBRID_AI_CANDIDATE_LIMIT", 5
    )
    session = MagicMock()
    peers = [_peer(2, "Alpha", 60)]
    matches = [_match(company_id=2, tender_id=1)]
    tender = _tender_row(id=1, value=10_000.0)
    session.scalars.return_value.all.side_effect = [matches, [tender]]

    with patch(
        "pipeline.competitive_intel.tender_activity.get_top_competitors_for_company",
        return_value=peers,
    ):
        result = get_competitor_tender_activity(session, company_id=1)

    competitor = result["competitors"][0]
    assert competitor["raw_match_count"] == 1
    assert competitor["candidate_limit_saturated"] is False


def test_competitor_activity_evidence_fields_present_mock():
    session = MagicMock()
    peers = [_peer(2, "Alpha", 60)]
    matches = [_match(company_id=2, tender_id=1)]
    tender = _tender_row(id=1, value=50_000.0)
    session.scalars.return_value.all.side_effect = [matches, [tender]]

    with patch(
        "pipeline.competitive_intel.tender_activity.get_top_competitors_for_company",
        return_value=peers,
    ):
        result = get_competitor_tender_activity(session, company_id=1)

    competitor = result["competitors"][0]
    assert competitor["evidence_status"] == "scored_fit_cache_not_participation"
    disclaimer = competitor["evidence_disclaimer"].lower()
    assert "never" in disclaimer
    assert "bid" in disclaimer
    assert "participation" in disclaimer


def test_competitor_activity_sorted_by_unique_not_raw_count_mock():
    """Alpha has more raw cache rows but they're all duplicates of ONE
    tender; Beta has fewer raw rows but on two distinct tenders. Ranking
    must follow unique_tender_match_count, not raw_match_count."""
    session = MagicMock()
    peers = [_peer(2, "Alpha", 60), _peer(3, "Beta", 55)]
    alpha_matches = [
        _match(company_id=2, tender_id=1, score=90),
        _match(company_id=2, tender_id=1, score=85),
        _match(company_id=2, tender_id=1, score=80),
    ]
    beta_matches = [
        _match(company_id=3, tender_id=2, score=90),
        _match(company_id=3, tender_id=3, score=85),
    ]
    tender_one = _tender_row(id=1, value=1.0)
    tender_two = _tender_row(id=2, value=1.0)
    tender_three = _tender_row(id=3, value=1.0)

    session.scalars.return_value.all.side_effect = [
        alpha_matches,
        [tender_one],
        beta_matches,
        [tender_two],
        [tender_three],
    ]

    with patch(
        "pipeline.competitive_intel.tender_activity.get_top_competitors_for_company",
        return_value=peers,
    ):
        result = get_competitor_tender_activity(session, company_id=1)

    by_name = {row["name"]: row for row in result["competitors"]}
    assert by_name["Alpha"]["raw_match_count"] == 3
    assert by_name["Alpha"]["unique_tender_match_count"] == 1
    assert by_name["Beta"]["raw_match_count"] == 2
    assert by_name["Beta"]["unique_tender_match_count"] == 2
    # Beta ranks first: 2 unique tenders > 1 unique tender, even though
    # Alpha has more raw cache rows. A sort keyed on raw_match_count
    # would (wrongly) rank Alpha (3) ahead of Beta (2).
    assert [row["name"] for row in result["competitors"]] == ["Beta", "Alpha"]


# ===================================================================
# 4. PR-MARKET-1A: honest evidence contract for
#    get_competitor_tender_activity -- local Postgres, for reliable
#    exact-count/value assertions (avoids the fragile multi-call
#    MagicMock sequencing the tests above depend on).
# ===================================================================


def _recent_match(db_session, **kwargs):
    """get_competitor_tender_activity has no as_of parameter -- it always
    filters against real wall-clock _cutoff_utc(), unlike
    get_missed_opportunities. _make_match's own default created_at is
    anchored to the fixed AS_OF constant instead, which is only correct
    for the as_of-driven Missed Opportunities tests above. Anchor to real
    wall-clock "now" here so these tests stay correct regardless of when
    they run."""
    kwargs.setdefault("created_at", datetime.now(timezone.utc) - timedelta(days=10))
    return _make_match(db_session, **kwargs)


def test_competitor_activity_duplicate_raw_rows_count_raw_but_dedupe_value(db_session):
    """Two raw TenderMatch rows for the SAME tender must both count
    toward raw_match_count (and its match_count alias), but the tender's
    value must only be added to total_tender_value once --
    unique_tender_match_count reflects the deduped count."""
    tender = _make_tender(db_session, is_open=False, estimated_value_numeric=100_000.0)
    peers = [_peer(2, "Alpha", 60)]
    _recent_match(
        db_session, company_id=2, tender_source="federal", tender_id=tender.id, score=90
    )
    _recent_match(
        db_session, company_id=2, tender_source="federal", tender_id=tender.id, score=80
    )

    with _patched(peers):
        result = get_competitor_tender_activity(db_session, company_id=1)

    competitor = result["competitors"][0]
    assert competitor["raw_match_count"] == 2
    assert competitor["unique_tender_match_count"] == 1
    assert competitor["match_count"] == competitor["raw_match_count"]
    assert competitor["total_tender_value"] == 100_000.0


def test_competitor_activity_match_count_equals_raw_match_count(db_session):
    tender = _make_tender(db_session, is_open=False, estimated_value_numeric=10_000.0)
    peers = [_peer(2, "Alpha", 60)]
    _recent_match(
        db_session, company_id=2, tender_source="federal", tender_id=tender.id, score=90
    )
    _recent_match(
        db_session, company_id=2, tender_source="federal", tender_id=tender.id, score=80
    )

    with _patched(peers):
        result = get_competitor_tender_activity(db_session, company_id=1)

    competitor = result["competitors"][0]
    assert competitor["match_count"] == 2
    assert competitor["match_count"] == competitor["raw_match_count"]


def test_competitor_activity_sorted_by_unique_tender_match_count_not_raw(db_session):
    """Alpha has many raw rows but they're all duplicates of ONE tender;
    Beta has fewer raw rows but they're all on DISTINCT tenders. Ranking
    must reflect genuine distinct-tender activity (unique), not raw
    cache-row volume -- count/value must be mathematically consistent
    with each other and dedup must happen before every final metric,
    including the ranking itself."""
    tender_shared = _make_tender(db_session, is_open=False, estimated_value_numeric=1.0)
    tender_b1 = _make_tender(db_session, is_open=False, estimated_value_numeric=1.0)
    tender_b2 = _make_tender(db_session, is_open=False, estimated_value_numeric=1.0)
    peers = [_peer(2, "Alpha", 60), _peer(3, "Beta", 55)]
    for score in (90, 85, 80):
        _recent_match(
            db_session,
            company_id=2,
            tender_source="federal",
            tender_id=tender_shared.id,
            score=score,
        )
    _recent_match(
        db_session,
        company_id=3,
        tender_source="federal",
        tender_id=tender_b1.id,
        score=90,
    )
    _recent_match(
        db_session,
        company_id=3,
        tender_source="federal",
        tender_id=tender_b2.id,
        score=85,
    )

    with _patched(peers):
        result = get_competitor_tender_activity(db_session, company_id=1)

    by_name = {row["name"]: row for row in result["competitors"]}
    assert by_name["Alpha"]["raw_match_count"] == 3
    assert by_name["Alpha"]["unique_tender_match_count"] == 1
    assert by_name["Beta"]["raw_match_count"] == 2
    assert by_name["Beta"]["unique_tender_match_count"] == 2
    # Beta ranks first: 2 unique tenders > 1 unique tender, even though
    # Alpha has more raw cache rows.
    assert [row["name"] for row in result["competitors"]] == ["Beta", "Alpha"]


def test_competitor_activity_candidate_limit_uses_imported_matching_constant(
    db_session,
):
    from pipeline.ai_matching import HYBRID_AI_CANDIDATE_LIMIT

    tender = _make_tender(db_session, is_open=False, estimated_value_numeric=10_000.0)
    peers = [_peer(2, "Alpha", 60)]
    _recent_match(
        db_session, company_id=2, tender_source="federal", tender_id=tender.id, score=90
    )

    with _patched(peers):
        result = get_competitor_tender_activity(db_session, company_id=1)

    competitor = result["competitors"][0]
    assert competitor["candidate_limit"] == HYBRID_AI_CANDIDATE_LIMIT
    assert competitor["candidate_limit_saturated"] is False


def test_competitor_activity_saturation_flag_true_when_raw_count_reaches_limit(
    db_session, monkeypatch
):
    """Saturation is driven purely by the imported existing matching
    constant, not a hardcoded 20 -- lowering it here via monkeypatch
    proves the module reads the constant live, not a copied value."""
    monkeypatch.setattr(
        "pipeline.competitive_intel.tender_activity.HYBRID_AI_CANDIDATE_LIMIT", 2
    )
    tender_a = _make_tender(db_session, is_open=False, estimated_value_numeric=10_000.0)
    tender_b = _make_tender(db_session, is_open=False, estimated_value_numeric=20_000.0)
    peers = [_peer(2, "Alpha", 60)]
    _recent_match(
        db_session,
        company_id=2,
        tender_source="federal",
        tender_id=tender_a.id,
        score=90,
    )
    _recent_match(
        db_session,
        company_id=2,
        tender_source="federal",
        tender_id=tender_b.id,
        score=85,
    )

    with _patched(peers):
        result = get_competitor_tender_activity(db_session, company_id=1)

    competitor = result["competitors"][0]
    assert competitor["raw_match_count"] == 2
    assert competitor["candidate_limit"] == 2
    assert competitor["candidate_limit_saturated"] is True


def test_competitor_activity_not_saturated_when_below_limit(db_session, monkeypatch):
    monkeypatch.setattr(
        "pipeline.competitive_intel.tender_activity.HYBRID_AI_CANDIDATE_LIMIT", 5
    )
    tender = _make_tender(db_session, is_open=False, estimated_value_numeric=10_000.0)
    peers = [_peer(2, "Alpha", 60)]
    _recent_match(
        db_session, company_id=2, tender_source="federal", tender_id=tender.id, score=90
    )

    with _patched(peers):
        result = get_competitor_tender_activity(db_session, company_id=1)

    competitor = result["competitors"][0]
    assert competitor["raw_match_count"] == 1
    assert competitor["candidate_limit_saturated"] is False


def test_competitor_activity_evidence_fields_present_and_never_claim_participation(
    db_session,
):
    tender = _make_tender(db_session, is_open=False, estimated_value_numeric=50_000.0)
    peers = [_peer(2, "Alpha", 60)]
    _recent_match(
        db_session, company_id=2, tender_source="federal", tender_id=tender.id, score=90
    )

    with _patched(peers):
        result = get_competitor_tender_activity(db_session, company_id=1)

    competitor = result["competitors"][0]
    assert competitor["evidence_status"] == "scored_fit_cache_not_participation"
    disclaimer = competitor["evidence_disclaimer"].lower()
    assert "never" in disclaimer
    assert "bid" in disclaimer
    assert "participation" in disclaimer


def test_competitor_activity_evidence_fields_present_on_every_competitor(db_session):
    """Not just the top result -- every returned competitor must carry
    the full evidence contract."""
    tender_a = _make_tender(db_session, is_open=False, estimated_value_numeric=10_000.0)
    tender_b = _make_tender(db_session, is_open=False, estimated_value_numeric=20_000.0)
    peers = [_peer(2, "Alpha", 60), _peer(3, "Beta", 55)]
    _recent_match(
        db_session,
        company_id=2,
        tender_source="federal",
        tender_id=tender_a.id,
        score=90,
    )
    _recent_match(
        db_session,
        company_id=3,
        tender_source="federal",
        tender_id=tender_b.id,
        score=85,
    )

    with _patched(peers):
        result = get_competitor_tender_activity(db_session, company_id=1)

    assert len(result["competitors"]) == 2
    for competitor in result["competitors"]:
        assert competitor["evidence_status"] == "scored_fit_cache_not_participation"
        assert competitor["evidence_disclaimer"]
        assert "candidate_limit" in competitor
        assert "candidate_limit_saturated" in competitor
        assert "raw_match_count" in competitor
        assert "unique_tender_match_count" in competitor


def test_missed_opportunities_evidence_contract_still_green_with_market1a_changes(
    db_session,
):
    """Explicit end-to-end confirmation that PR-MARKET-1A did not touch
    the existing potential_opportunity_gap_v1 contract at all."""
    tender = _make_tender(db_session, is_open=False)
    peers = [_peer(2, "Alpha", 80)]
    _recent_match(
        db_session, company_id=2, tender_source="federal", tender_id=tender.id, score=90
    )

    with _patched(peers, subject_keys=set()):
        result = get_missed_opportunities(db_session, company_id=1, as_of=AS_OF)

    assert result["semantics_version"] == "potential_opportunity_gap_v1"
    item = result["items"][0]
    assert item["opportunity_status"] == "potential_gap"
    assert item["evidence_status"] == "peer_fit_only"
    assert item["participation_status"] == "unknown"
    assert item["subject_fit_status"] == "no_recent_match_evidence"
