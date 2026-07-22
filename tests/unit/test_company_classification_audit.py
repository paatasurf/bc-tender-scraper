"""Unit + local-Postgres tests for the Company classification truth audit
(PR-MARKET-2B, Class A -- pipeline.company_classification_audit).

Sections:
  1. compute_examined_digest -- pure function, no DB.
  2. audit_company_classification -- mock-session unit tests: signal
     detection, review-category bucketing, count invariants, digest
     stability, zero mutation, no raw identifying data in the artifact.
  3. RJC (Jones Christoffersen) regression fixture -- local Postgres.
  4. No impact on Market scoring/cohort/Top competitors -- structural
     check that this module is never imported by the competitive_intel
     package.
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session

from db.company_canonical_constants import ENTITY_ROLE_STANDALONE
from db.models import Company
from pipeline.company_classification_audit import (
    REVIEW_CONFIRMED_CONFLICT,
    REVIEW_NEEDS_REVIEW,
    REVIEW_NOT_ACTIONABLE,
    SIGNAL_NAME_PATTERN,
    SIGNAL_TRADE_TAG,
    CompanyClassificationAuditError,
    audit_company_classification,
    compute_examined_digest,
)
from tests.db_test_safety import require_local_test_database

# ===================================================================
# 1. compute_examined_digest -- pure function, no DB
# ===================================================================


def test_examined_digest_is_deterministic_regardless_of_input_order():
    assert compute_examined_digest([3, 1, 2]) == compute_examined_digest([1, 2, 3])


def test_examined_digest_changes_with_a_different_set():
    assert compute_examined_digest([1, 2, 3]) != compute_examined_digest([1, 2, 4])


def test_examined_digest_is_full_length_sha256_hex():
    digest = compute_examined_digest([42])
    assert len(digest) == 64
    int(digest, 16)  # raises ValueError if not valid hex


# ===================================================================
# 2. audit_company_classification -- mock-session unit tests
# ===================================================================


def _row(
    company_id: int,
    name: str,
    *,
    company_type: str = "General Contractor",
    primary_trade: str = "general_building",
    confidence_score: float | None = 0.9,
) -> tuple:
    return (company_id, name, company_type, primary_trade, confidence_score)


def _session_with_rows(rows: list[tuple]) -> MagicMock:
    session = MagicMock()
    session.execute.return_value.all.return_value = rows
    return session


def test_clean_gc_row_is_not_actionable_and_excluded_from_candidates():
    session = _session_with_rows(
        [
            _row(
                1,
                "Pacific Build Co Ltd",
                primary_trade="general_building",
                confidence_score=0.9,
            )
        ]
    )
    result = audit_company_classification(session)

    assert result["counts"]["total_scanned"] == 1
    assert result["counts"][REVIEW_NOT_ACTIONABLE] == 1
    assert result["counts"][REVIEW_CONFIRMED_CONFLICT] == 0
    assert result["counts"][REVIEW_NEEDS_REVIEW] == 0
    assert result["candidates_with_conflicting_signals"] == 0
    assert result["candidates_by_current_type"] == {}
    assert result["candidates_by_review_category"] == {}


def test_trade_tag_conflict_flags_confirmed_conflict():
    """Company.primary_trade (a separately-derived, already-persisted
    field) disagrees with Company.company_type -- the exact mechanism the
    RJC fixture below exercises."""
    session = _session_with_rows(
        [_row(1, "Some Firm Ltd", primary_trade="engineering", confidence_score=0.9)]
    )
    result = audit_company_classification(session)

    assert result["counts"][REVIEW_CONFIRMED_CONFLICT] == 1
    assert result["signal_histogram"][SIGNAL_TRADE_TAG] == 1
    assert result["signal_histogram"][SIGNAL_NAME_PATTERN] == 0
    assert result["candidates_by_review_category"] == {
        "confirmed_conflict:Engineering": 1
    }


def test_name_pattern_conflict_flags_confirmed_conflict():
    """Company.name matches an already-deployed professional-services
    name pattern (KNOWN_FIRMS) from pipeline.company_classification,
    reused verbatim -- while company_type is still General Contractor."""
    session = _session_with_rows(
        [
            _row(
                1,
                "D'Arcy Jones Architects",
                primary_trade="general_building",
                confidence_score=0.9,
            )
        ]
    )
    result = audit_company_classification(session)

    assert result["counts"][REVIEW_CONFIRMED_CONFLICT] == 1
    assert result["signal_histogram"][SIGNAL_NAME_PATTERN] == 1
    assert result["candidates_by_review_category"] == {
        "confirmed_conflict:Architect": 1
    }


def test_known_firms_gc_categorized_entries_do_not_trigger_confirmed_conflict():
    """KNOWN_FIRMS is NOT a professional-services signal wholesale -- only
    entries whose own already-assigned category is Architect, Engineering
    Firm, or Building Code Consultant may contribute to
    name_pattern_conflict. A KNOWN_FIRMS entry already categorized
    General Contractor or Trade Contractor must never land in
    confirmed_conflict merely for being present in that table."""
    from pipeline.company_classification import KNOWN_FIRMS

    gc_trade_contractor_categories = {"General Contractor", "Trade Contractor"}
    # Realistic company names built from each GC/Trade-Contractor-categorized
    # KNOWN_FIRMS entry -- each asserted below to actually match its own
    # KNOWN_FIRMS pattern, so this test fails loudly (not silently) if
    # KNOWN_FIRMS ever changes shape rather than testing nothing.
    candidate_names = [
        "Govan Brown Construction Ltd",
        "Acres Enterprises Inc",
        "Priority Projects Construction",
        "Syncor Solutions Contracting",
        "Jakobsen Associates Builders",
        "Eyco Building Group Ltd",
        "Raffaele & Associates Construction",
        "MCM Construction Ltd",
    ]
    gc_patterns = [
        pattern
        for pattern, category in KNOWN_FIRMS
        if category in gc_trade_contractor_categories
    ]
    assert len(candidate_names) == len(gc_patterns), (
        "candidate_names must cover every GC/Trade-Contractor-categorized "
        "KNOWN_FIRMS entry -- update this list if KNOWN_FIRMS changes"
    )
    for name, pattern in zip(candidate_names, gc_patterns):
        assert pattern.search(
            name
        ), f"{name!r} no longer matches KNOWN_FIRMS pattern {pattern.pattern!r}"

    rows = [
        _row(idx, name, company_type="General Contractor", confidence_score=0.9)
        for idx, name in enumerate(candidate_names, start=1)
    ]
    result = audit_company_classification(_session_with_rows(rows))

    assert result["counts"][REVIEW_CONFIRMED_CONFLICT] == 0
    assert result["signal_histogram"][SIGNAL_NAME_PATTERN] == 0
    assert result["counts"][REVIEW_NOT_ACTIONABLE] == len(candidate_names)


def test_low_confidence_with_no_signal_is_needs_review():
    session = _session_with_rows(
        [
            _row(
                1,
                "Ambiguous Co",
                primary_trade="general_building",
                confidence_score=0.3,
            )
        ]
    )
    result = audit_company_classification(session)

    assert result["counts"][REVIEW_NEEDS_REVIEW] == 1
    assert result["counts"][REVIEW_CONFIRMED_CONFLICT] == 0
    assert result["candidates_by_review_category"] == {"needs_review:unspecified": 1}


def test_high_confidence_with_no_signal_is_not_actionable():
    session = _session_with_rows(
        [
            _row(
                1,
                "Solid GC Ltd",
                primary_trade="general_building",
                confidence_score=0.95,
            )
        ]
    )
    result = audit_company_classification(session)

    assert result["counts"][REVIEW_NOT_ACTIONABLE] == 1
    assert result["candidates_with_conflicting_signals"] == 0


def test_null_confidence_score_with_no_signal_is_not_actionable():
    """A NULL confidence_score must never be treated as "low confidence"
    by accident (float(None) would raise) -- absence of a score is not
    itself a deterministic conflict signal."""
    session = _session_with_rows(
        [
            _row(
                1,
                "Never Classified Ltd",
                primary_trade="general_building",
                confidence_score=None,
            )
        ]
    )
    result = audit_company_classification(session)

    assert result["counts"][REVIEW_NOT_ACTIONABLE] == 1


def test_both_signals_firing_prefers_name_pattern_for_proposed_category():
    session = _session_with_rows(
        [
            _row(
                1,
                "D'Arcy Jones Architects",
                primary_trade="architecture",
                confidence_score=0.9,
            )
        ]
    )
    result = audit_company_classification(session)

    assert result["counts"][REVIEW_CONFIRMED_CONFLICT] == 1
    assert result["signal_histogram"][SIGNAL_NAME_PATTERN] == 1
    assert result["signal_histogram"][SIGNAL_TRADE_TAG] == 1
    assert result["candidates_by_review_category"] == {
        "confirmed_conflict:Architect": 1
    }


def test_review_category_counts_sum_to_total_scanned():
    session = _session_with_rows(
        [
            _row(1, "Clean GC Ltd", confidence_score=0.9),
            _row(
                2,
                "Engineering-Tagged GC",
                primary_trade="engineering",
                confidence_score=0.9,
            ),
            _row(3, "Low Confidence Co", confidence_score=0.2),
        ]
    )
    result = audit_company_classification(session)

    total = sum(
        result["counts"][c]
        for c in (REVIEW_CONFIRMED_CONFLICT, REVIEW_NEEDS_REVIEW, REVIEW_NOT_ACTIONABLE)
    )
    assert total == result["counts"]["total_scanned"] == 3


def test_sample_size_rejects_bool_and_negative():
    session = _session_with_rows([])
    with pytest.raises(CompanyClassificationAuditError):
        audit_company_classification(session, sample_size=True)
    with pytest.raises(CompanyClassificationAuditError):
        audit_company_classification(session, sample_size=-1)


def test_digest_stable_across_repeated_runs_on_identical_input():
    rows = [
        _row(1, "Clean GC Ltd", confidence_score=0.9),
        _row(
            2,
            "Engineering-Tagged GC",
            primary_trade="engineering",
            confidence_score=0.9,
        ),
    ]
    result_a = audit_company_classification(_session_with_rows(rows))
    result_b = audit_company_classification(_session_with_rows(rows))

    assert result_a["digest"] == result_b["digest"]
    assert result_a == result_b


def test_no_raw_identifying_data_in_artifact():
    """The returned artifact must never contain the company's raw name,
    id, or any other identifying text -- only aggregate counts and
    closed-vocabulary category labels."""
    rows = [
        _row(
            1,
            "Read Jones Christoffersen Ltd",
            primary_trade="engineering",
            confidence_score=0.9,
        ),
        _row(2, "Totally Unique Identifying Name Sixty Four LLC", confidence_score=0.9),
    ]
    result = audit_company_classification(_session_with_rows(rows))

    serialized = json.dumps(result)
    for leaked_text in (
        "Read Jones Christoffersen",
        "Christoffersen",
        "Totally Unique Identifying Name",
        "Ltd",
        "LLC",
    ):
        assert leaked_text not in serialized
    # Only the closed MARKET_CATEGORIES/signal-name vocabulary and
    # aggregate counters are allowed as dict keys -- no company id ever
    # becomes a key or value.
    expected_top_level_keys = {
        "counts",
        "candidates_with_conflicting_signals",
        "signal_histogram",
        "candidates_by_current_type",
        "candidates_by_review_category",
        "examined_count",
        "digest",
    }
    assert set(result.keys()) == expected_top_level_keys


def test_zero_session_mutation_calls():
    session = _session_with_rows([_row(1, "Some Co Ltd")])
    audit_company_classification(session)

    session.add.assert_not_called()
    session.commit.assert_not_called()
    session.delete.assert_not_called()
    session.flush.assert_not_called()


# ===================================================================
# 3. RJC (Jones Christoffersen) regression fixture -- local Postgres
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


def _make_company(session: Session, **overrides) -> Company:
    unique = uuid.uuid4().hex[:8]
    defaults: dict = dict(
        name=f"MARKET2B Test Co {unique}",
        entity_role=ENTITY_ROLE_STANDALONE,
        company_type="General Contractor",
    )
    defaults.update(overrides)
    company = Company(**defaults)
    session.add(company)
    session.flush()
    return company


def test_rjc_like_row_flagged_for_review_via_real_db(db_session):
    """Regression fixture matching the reported real-world case: a
    structural-engineering firm (Read Jones Christoffersen / RJC) whose
    Company.company_type is currently "General Contractor" while
    Company.primary_trade -- a separately-derived, already-persisted
    field -- correctly reads "engineering". The audit must flag this
    combination as a candidate requiring review (confirmed_conflict, the
    strongest of the two non-not_actionable buckets) and must NOT change
    company_type itself -- no manual production override is made here."""
    rjc = _make_company(
        db_session,
        name="Read Jones Christoffersen Ltd",
        company_type="General Contractor",
        primary_trade="engineering",
        confidence_score=0.85,
    )

    result = audit_company_classification(db_session)

    assert result["counts"][REVIEW_CONFIRMED_CONFLICT] >= 1
    assert result["signal_histogram"][SIGNAL_TRADE_TAG] >= 1

    # Zero mutation: the row's own company_type is untouched by the audit.
    db_session.expire(rjc)
    reloaded = db_session.get(Company, rjc.id)
    assert reloaded.company_type == "General Contractor"

    assert not db_session.dirty
    assert not db_session.deleted


def test_rjc_like_row_audit_makes_no_database_writes(db_session):
    rjc = _make_company(
        db_session,
        name="Read Jones Christoffersen Ltd",
        company_type="General Contractor",
        primary_trade="engineering",
        confidence_score=0.85,
    )
    db_session.flush()

    commits: list[int] = []

    def on_commit(conn):
        commits.append(1)

    connection = db_session.connection()
    event.listen(connection, "commit", on_commit)
    try:
        audit_company_classification(db_session)
    finally:
        event.remove(connection, "commit", on_commit)

    assert commits == []
    assert not db_session.new
    assert not db_session.dirty
    assert not db_session.deleted
    assert rjc.id is not None  # sanity: row still exists in this transaction


def test_clean_gc_row_via_real_db_is_not_flagged(db_session):
    _make_company(
        db_session,
        name="Genuinely A Construction Company Ltd",
        company_type="General Contractor",
        primary_trade="general_building",
        confidence_score=0.9,
    )

    result = audit_company_classification(db_session)

    assert result["counts"][REVIEW_NOT_ACTIONABLE] >= 1


# ===================================================================
# 4. No impact on Market scoring/cohort/Top competitors -- structural
#    check that this module is never imported by competitive_intel.
# ===================================================================


def test_module_not_imported_by_competitive_intel_package():
    """This audit is a standalone, opt-in read-only tool -- it must never
    be wired into the live Market cohort/scoring/Top-competitors path.
    A source-text check (not just "not currently called") so a future
    accidental import is caught even before any call site is added."""
    import pathlib

    package_dir = pathlib.Path("pipeline/competitive_intel")
    for path in package_dir.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert "company_classification_audit" not in source, (
            f"{path} must not import pipeline.company_classification_audit "
            "-- this audit is read-only/standalone and must never affect "
            "Market scoring, cohort composition, or Top competitors."
        )
