"""Unit + local-Postgres tests for the Company classification conflict
evidence-review planner (PR-MARKET-2C, Class A --
pipeline.company_classification_evidence_review) and its CLI runner
(scripts/run_company_classification_evidence_review.py).

Sections:
  1. Exact predicate reuse -- proves zero duplication of PR-MARKET-2B's
     conflict-determination logic.
  2. build_evidence_review -- mock-session unit tests: aggregate/
     candidate split, cross-validation against audit_company_classification,
     review_digest stability/sensitivity, no raw leakage into the
     aggregate result, zero mutation.
  3. CLI runner safety gates -- --show-candidates + --artifact-path
     mutual exclusion, attended-TTY requirement.
  4. RJC (Jones Christoffersen) regression fixture -- local Postgres.
  5. Deterministic ordering + zero writes -- local Postgres.
"""

from __future__ import annotations

import json
import sys
import uuid
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session

from db.company_canonical_constants import ENTITY_ROLE_STANDALONE
from db.models import Company
from pipeline import company_classification_audit as audit_module
from pipeline.company_classification_audit import (
    REVIEW_CONFIRMED_CONFLICT,
    SIGNAL_NAME_PATTERN,
    SIGNAL_TRADE_TAG,
    audit_company_classification,
)
from pipeline.company_classification_evidence_review import (
    ConflictCandidate,
    EvidenceReviewError,
    build_evidence_review,
    compute_review_digest,
)
from tests.db_test_safety import require_local_test_database

# ===================================================================
# 1. Exact predicate reuse -- zero duplication
# ===================================================================


def test_exact_predicate_functions_are_reused_not_duplicated():
    """The evidence-review module must import and call the audit's own
    predicate functions -- never a re-implementation. Identity checks
    (is) prove these are literally the same function objects."""
    import pipeline.company_classification_evidence_review as review_module

    assert review_module._name_pattern_conflict is audit_module._name_pattern_conflict
    assert review_module._trade_tag_conflict is audit_module._trade_tag_conflict


# ===================================================================
# 2. build_evidence_review -- mock-session unit tests
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


def test_matches_audit_aggregate_counts():
    """Cross-validation regression guard: build_evidence_review's own
    review_candidate_count must always equal
    audit_company_classification's confirmed_conflict count on the exact
    same input, and every other aggregate field must be unchanged."""
    rows = [
        _row(1, "Clean GC Ltd", confidence_score=0.9),
        _row(
            2,
            "Read Jones Christoffersen Ltd",
            primary_trade="engineering",
            confidence_score=0.9,
        ),
        _row(3, "D'Arcy Jones Architects", confidence_score=0.9),
        _row(4, "Low Confidence Co", confidence_score=0.2),
    ]
    audit_result = audit_company_classification(_session_with_rows(rows))
    aggregate, candidates = build_evidence_review(_session_with_rows(rows))

    assert (
        aggregate["review_candidate_count"]
        == audit_result["counts"][REVIEW_CONFIRMED_CONFLICT]
    )
    assert len(candidates) == audit_result["counts"][REVIEW_CONFIRMED_CONFLICT]
    for key in audit_result:
        assert aggregate[key] == audit_result[key]


def test_candidates_only_contain_confirmed_conflict_rows():
    rows = [
        _row(1, "Clean GC Ltd", confidence_score=0.9),
        _row(2, "Some Firm Ltd", primary_trade="engineering", confidence_score=0.9),
        _row(3, "Low Confidence Co", confidence_score=0.2),
    ]
    _, candidates = build_evidence_review(_session_with_rows(rows))

    assert len(candidates) == 1
    assert candidates[0].company_id == 2
    assert candidates[0].signals == (SIGNAL_TRADE_TAG,)
    assert candidates[0].proposed_category == "Engineering"
    assert len(candidates[0].provenance) == 1
    assert "trade_tag_conflict" in candidates[0].provenance[0]


def test_name_pattern_candidate_has_correct_provenance():
    rows = [_row(1, "D'Arcy Jones Architects", confidence_score=0.9)]
    _, candidates = build_evidence_review(_session_with_rows(rows))

    assert len(candidates) == 1
    assert candidates[0].signals == (SIGNAL_NAME_PATTERN,)
    assert "name_pattern_conflict" in candidates[0].provenance[0]
    assert "KNOWN_FIRMS" in candidates[0].provenance[0]


def test_no_raw_leakage_in_aggregate_result():
    """The aggregate half of build_evidence_review's return value must
    never contain a company name -- only the candidate list (never
    returned to any artifact/file/log by this module) may."""
    rows = [
        _row(
            1,
            "Read Jones Christoffersen Ltd",
            primary_trade="engineering",
            confidence_score=0.9,
        ),
        _row(2, "Totally Unique Identifying Name Sixty Four LLC", confidence_score=0.9),
    ]
    aggregate, _ = build_evidence_review(_session_with_rows(rows))

    serialized = json.dumps(aggregate)
    for leaked_text in (
        "Read Jones Christoffersen",
        "Christoffersen",
        "Totally Unique Identifying Name",
    ):
        assert leaked_text not in serialized

    expected_keys = {
        "counts",
        "candidates_with_conflicting_signals",
        "signal_histogram",
        "candidates_by_current_type",
        "candidates_by_review_category",
        "examined_count",
        "digest",
        "review_candidate_count",
        "review_digest",
    }
    assert set(aggregate.keys()) == expected_keys


def test_review_digest_stable_across_repeated_runs():
    rows = [
        _row(1, "Clean GC Ltd", confidence_score=0.9),
        _row(
            2,
            "Engineering-Tagged GC",
            primary_trade="engineering",
            confidence_score=0.9,
        ),
    ]
    aggregate_a, _ = build_evidence_review(_session_with_rows(rows))
    aggregate_b, _ = build_evidence_review(_session_with_rows(rows))

    assert aggregate_a["review_digest"] == aggregate_b["review_digest"]


def test_review_digest_sensitive_to_identity_type_trade_and_signals():
    base_rows = [
        _row(2, "Some Firm Ltd", primary_trade="engineering", confidence_score=0.9)
    ]
    different_id_rows = [
        _row(3, "Some Firm Ltd", primary_trade="engineering", confidence_score=0.9)
    ]
    different_trade_rows = [
        _row(2, "Some Firm Ltd", primary_trade="architecture", confidence_score=0.9)
    ]
    different_type_rows = [
        _row(
            2,
            "Some Firm Ltd",
            company_type="Trade Contractor",
            primary_trade="engineering",
            confidence_score=0.9,
        )
    ]

    base_digest = build_evidence_review(_session_with_rows(base_rows))[0][
        "review_digest"
    ]
    id_digest = build_evidence_review(_session_with_rows(different_id_rows))[0][
        "review_digest"
    ]
    trade_digest = build_evidence_review(_session_with_rows(different_trade_rows))[0][
        "review_digest"
    ]
    type_digest = build_evidence_review(_session_with_rows(different_type_rows))[0][
        "review_digest"
    ]

    assert len({base_digest, id_digest, trade_digest, type_digest}) == 4


def test_review_digest_is_full_length_sha256_hex():
    candidate = ConflictCandidate(
        company_id=1,
        company_type="General Contractor",
        primary_trade="engineering",
        signals=(SIGNAL_TRADE_TAG,),
        proposed_category="Engineering",
        provenance=("trade_tag_conflict: ...",),
    )
    digest = compute_review_digest([candidate])
    assert len(digest) == 64
    int(digest, 16)


def test_empty_candidate_list_digest_is_deterministic():
    assert compute_review_digest([]) == compute_review_digest([])


def test_sample_size_rejects_bool_and_negative():
    session = _session_with_rows([])
    with pytest.raises(EvidenceReviewError):
        build_evidence_review(session, sample_size=True)
    with pytest.raises(EvidenceReviewError):
        build_evidence_review(session, sample_size=-1)


def test_zero_session_mutation_calls():
    session = _session_with_rows([_row(1, "Some Co Ltd")])
    build_evidence_review(session)

    session.add.assert_not_called()
    session.commit.assert_not_called()
    session.delete.assert_not_called()
    session.flush.assert_not_called()


def test_query_orders_by_company_id_ascending():
    """No DB needed -- captures the compiled query text to confirm the
    canonical ordering (matching audit_company_classification's own
    query) is used for the candidate scan, not just the aggregate scan."""
    session = MagicMock()
    captured: list[object] = []

    def execute_side_effect(query):
        captured.append(query)
        result = MagicMock()
        result.all.return_value = []
        return result

    session.execute.side_effect = execute_side_effect
    build_evidence_review(session)

    # Second captured query is the candidate scan (the first is inside
    # audit_company_classification's own call).
    assert len(captured) == 2
    compiled = str(captured[1].compile(compile_kwargs={"literal_binds": True}))
    assert "ORDER BY companies.id" in compiled


# ===================================================================
# 3. CLI runner safety gates
# ===================================================================


def _load_cli_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "run_company_classification_evidence_review_under_test",
        "scripts/run_company_classification_evidence_review.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_show_candidates_and_artifact_path_are_mutually_exclusive(monkeypatch, capsys):
    cli = _load_cli_module()
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_company_classification_evidence_review.py",
            "--show-candidates",
            "--artifact-path",
            "out.json",
        ],
    )
    with pytest.raises(SystemExit) as exc_info:
        cli.main()
    assert exc_info.value.code == 2
    captured = capsys.readouterr()
    assert "cannot be combined" in captured.err


def test_show_candidates_refused_without_attended_terminal(monkeypatch, capsys):
    cli = _load_cli_module()
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_company_classification_evidence_review.py", "--show-candidates"],
    )
    monkeypatch.setattr(cli, "_is_attended_terminal", lambda: False)
    exit_code = cli.main()
    assert exit_code == 1
    captured = capsys.readouterr()
    assert "attended terminal" in captured.err


def test_is_attended_terminal_false_in_this_test_environment():
    """This test process itself is not an attended interactive terminal
    (pytest captures stdio) -- confirms the real, unmocked check reflects
    that rather than defaulting to True."""
    cli = _load_cli_module()
    assert cli._is_attended_terminal() is False


# ===================================================================
# 4. RJC (Jones Christoffersen) regression fixture -- local Postgres
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
        name=f"MARKET2C Test Co {unique}",
        entity_role=ENTITY_ROLE_STANDALONE,
        company_type="General Contractor",
    )
    defaults.update(overrides)
    company = Company(**defaults)
    session.add(company)
    session.flush()
    return company


def test_rjc_like_row_appears_only_in_attended_candidates_via_real_db(db_session):
    """The regression fixture: RJC's real reported profile appears with
    its current type/trade/conflict reason in the attended candidate
    list, but its name/id never appear in the artifact-safe aggregate."""
    rjc = _make_company(
        db_session,
        name="Read Jones Christoffersen Ltd",
        company_type="General Contractor",
        primary_trade="engineering",
        confidence_score=0.85,
    )

    aggregate, candidates = build_evidence_review(db_session)

    matching = [c for c in candidates if c.company_id == rjc.id]
    assert len(matching) == 1
    candidate = matching[0]
    assert candidate.company_type == "General Contractor"
    assert candidate.primary_trade == "engineering"
    assert SIGNAL_TRADE_TAG in candidate.signals
    assert candidate.proposed_category == "Engineering"
    assert candidate.provenance

    serialized_aggregate = json.dumps(aggregate)
    assert "Read Jones Christoffersen" not in serialized_aggregate
    assert "Christoffersen" not in serialized_aggregate
    expected_keys = {
        "counts",
        "candidates_with_conflicting_signals",
        "signal_histogram",
        "candidates_by_current_type",
        "candidates_by_review_category",
        "examined_count",
        "digest",
        "review_candidate_count",
        "review_digest",
    }
    assert set(aggregate.keys()) == expected_keys

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
        build_evidence_review(db_session)
    finally:
        event.remove(connection, "commit", on_commit)

    assert commits == []
    assert not db_session.new
    assert not db_session.dirty
    assert not db_session.deleted

    db_session.expire(rjc)
    reloaded = db_session.get(Company, rjc.id)
    assert reloaded.company_type == "General Contractor"


# ===================================================================
# 5. Deterministic ordering + zero writes -- local Postgres
# ===================================================================


def test_candidates_deterministically_ordered_by_company_id_via_real_db(db_session):
    """Insert conflicting rows in a scrambled creation order and confirm
    the candidate list still comes back sorted by Company.id ascending."""
    third = _make_company(
        db_session,
        name="Third Firm Ltd",
        primary_trade="engineering",
        confidence_score=0.9,
    )
    first = _make_company(
        db_session,
        name="First Firm Ltd",
        primary_trade="architecture",
        confidence_score=0.9,
    )
    second = _make_company(
        db_session,
        name="Second Firm Ltd",
        primary_trade="consulting",
        confidence_score=0.9,
    )
    ordered_ids = sorted([first.id, second.id, third.id])

    _, candidates = build_evidence_review(db_session)
    matching_ids = [c.company_id for c in candidates if c.company_id in ordered_ids]

    assert matching_ids == ordered_ids
