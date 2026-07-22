"""Unit + local-Postgres tests for the Company classification lineage
diagnostic (PR-MARKET-2D, Class A --
pipeline.company_classification_lineage_diagnostic) and its CLI runner
(scripts/run_company_classification_lineage_diagnostic.py).

Sections:
  1. resolve_company_by_exact_identity -- exact-only, fail-closed
     resolution (mock session, no DB needed for the argument-validation
     and query-shape checks; local Postgres for real ambiguity/zero-match
     data).
  2. compute_lineage_digest -- pure function, no DB.
  3. CLI runner safety gates -- --show-evidence + --artifact-path mutual
     exclusion, attended-TTY requirement, evidence-printing behavior.
  4. RJC (Jones Christoffersen) regression fixture -- local Postgres:
     end-to-end build_lineage_diagnostic, zero mutation, no raw leakage.
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
from pipeline.company_classification_audit import (
    REVIEW_CONFIRMED_CONFLICT,
    SIGNAL_TRADE_TAG,
)
from pipeline.company_classification_lineage_diagnostic import (
    RESOLUTION_AMBIGUOUS,
    RESOLUTION_NOT_FOUND,
    RESOLUTION_RESOLVED,
    LineageDiagnosticError,
    LineageEvidence,
    ResolutionResult,
    build_lineage_diagnostic,
    compute_lineage_digest,
    resolve_company_by_exact_identity,
)
from tests.db_test_safety import require_local_test_database

# ===================================================================
# 1. resolve_company_by_exact_identity -- exact-only, fail-closed
# ===================================================================


def _session_with_id_rows(ids: list[int]) -> MagicMock:
    session = MagicMock()
    session.execute.return_value.all.return_value = [(i,) for i in ids]
    return session


def test_resolve_single_match_is_resolved():
    session = _session_with_id_rows([42])
    result = resolve_company_by_exact_identity(session, "Read Jones Christoffersen Ltd")
    assert result.status == RESOLUTION_RESOLVED
    assert result.candidate_count == 1
    assert result.company_id == 42


def test_resolve_zero_matches_fails_closed_not_found():
    session = _session_with_id_rows([])
    result = resolve_company_by_exact_identity(session, "Nonexistent Firm Ltd")
    assert result.status == RESOLUTION_NOT_FOUND
    assert result.candidate_count == 0
    assert result.company_id is None


def test_resolve_multiple_matches_fails_closed_ambiguous():
    session = _session_with_id_rows([7, 9])
    result = resolve_company_by_exact_identity(session, "Duplicate Name Ltd")
    assert result.status == RESOLUTION_AMBIGUOUS
    assert result.candidate_count == 2
    assert result.company_id is None


def test_resolve_empty_identity_raises():
    session = _session_with_id_rows([])
    with pytest.raises(LineageDiagnosticError):
        resolve_company_by_exact_identity(session, "")
    with pytest.raises(LineageDiagnosticError):
        resolve_company_by_exact_identity(session, "   ")


def test_resolve_query_uses_exact_equality_not_fuzzy():
    """No DB needed -- captures the compiled query text to confirm this
    is a literal equality comparison, never a LIKE/ILIKE/normalized-key
    lookup."""
    session = MagicMock()
    captured: list[object] = []

    def execute_side_effect(query):
        captured.append(query)
        result = MagicMock()
        result.all.return_value = []
        return result

    session.execute.side_effect = execute_side_effect
    resolve_company_by_exact_identity(session, "Read Jones Christoffersen Ltd")

    compiled = str(captured[0].compile(compile_kwargs={"literal_binds": True}))
    assert "ILIKE" not in compiled.upper()
    assert " LIKE " not in compiled.upper()
    assert "= 'Read Jones Christoffersen Ltd'" in compiled


def test_resolve_never_strips_leading_or_trailing_whitespace_from_lookup_value():
    """The lookup value passed to SQL must be byte-identical to the raw
    identity argument -- .strip() may only ever be used to validate that
    identity is non-empty, never to build the query. A leading/trailing-
    space-only difference from a stored row is a genuine non-match for an
    exact-only resolver, not something to silently paper over."""
    session = MagicMock()
    captured: list[object] = []

    def execute_side_effect(query):
        captured.append(query)
        result = MagicMock()
        result.all.return_value = []
        return result

    session.execute.side_effect = execute_side_effect
    padded_identity = "  Read Jones Christoffersen Ltd  "
    resolve_company_by_exact_identity(session, padded_identity)

    compiled = str(captured[0].compile(compile_kwargs={"literal_binds": True}))
    assert f"= '{padded_identity}'" in compiled
    assert "= 'Read Jones Christoffersen Ltd'" not in compiled


def test_resolve_still_fails_closed_on_whitespace_only_identity():
    """.strip() is still used for the emptiness *check* -- a
    whitespace-only identity must still raise, never reach the query."""
    session = MagicMock()
    with pytest.raises(LineageDiagnosticError):
        resolve_company_by_exact_identity(session, "   ")
    session.execute.assert_not_called()


# ===================================================================
# 2. compute_lineage_digest -- pure function, no DB
# ===================================================================


def _fake_evidence(**overrides) -> LineageEvidence:
    defaults = dict(
        company_id=1,
        company_name="Read Jones Christoffersen Ltd",
        display_name="",
        entity_role=ENTITY_ROLE_STANDALONE,
        company_type="General Contractor",
        confidence_score=0.85,
        primary_trade="engineering",
        dominant_sector="commercial",
        cip_company_type="General Contractor",
        cip_entity_class="contractor",
        cip_primary_trade="engineering",
        classification_method="no_match",
        classification_internal_category="Unknown",
        classification_market_category="Unknown",
        classification_confidence=0.35,
        known_firms_match_category=None,
        matching_rule_categories=(),
        review_category=REVIEW_CONFIRMED_CONFLICT,
        conflict_signals=(SIGNAL_TRADE_TAG,),
        passes_entity_analytics_filter=True,
        passes_person_name_filter=True,
        passes_gc_cohort_isolation_allowlist=True,
        provenance=("some provenance line",),
    )
    defaults.update(overrides)
    return LineageEvidence(**defaults)


def test_digest_not_found_vs_ambiguous_differ():
    not_found = ResolutionResult(
        status=RESOLUTION_NOT_FOUND, candidate_count=0, company_id=None
    )
    ambiguous = ResolutionResult(
        status=RESOLUTION_AMBIGUOUS, candidate_count=2, company_id=None
    )
    assert compute_lineage_digest(not_found, None) != compute_lineage_digest(
        ambiguous, None
    )


def test_digest_stable_across_repeated_calls():
    resolution = ResolutionResult(
        status=RESOLUTION_RESOLVED, candidate_count=1, company_id=1
    )
    evidence = _fake_evidence()
    assert compute_lineage_digest(resolution, evidence) == compute_lineage_digest(
        resolution, evidence
    )


def test_digest_sensitive_to_evidence_field_changes():
    resolution = ResolutionResult(
        status=RESOLUTION_RESOLVED, candidate_count=1, company_id=1
    )
    base = compute_lineage_digest(resolution, _fake_evidence())
    different_trade = compute_lineage_digest(
        resolution, _fake_evidence(primary_trade="architecture")
    )
    different_review = compute_lineage_digest(
        resolution,
        _fake_evidence(review_category="not_actionable", conflict_signals=()),
    )
    different_id = compute_lineage_digest(resolution, _fake_evidence(company_id=2))
    assert len({base, different_trade, different_review, different_id}) == 4


def test_digest_is_full_length_sha256_hex():
    resolution = ResolutionResult(
        status=RESOLUTION_RESOLVED, candidate_count=1, company_id=1
    )
    digest = compute_lineage_digest(resolution, _fake_evidence())
    assert len(digest) == 64
    int(digest, 16)


# ===================================================================
# 3. CLI runner safety gates
# ===================================================================


def _load_cli_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "run_company_classification_lineage_diagnostic_under_test",
        "scripts/run_company_classification_lineage_diagnostic.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_show_evidence_and_artifact_path_are_mutually_exclusive(monkeypatch, capsys):
    cli = _load_cli_module()
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_company_classification_lineage_diagnostic.py",
            "--identity",
            "Read Jones Christoffersen Ltd",
            "--show-evidence",
            "--artifact-path",
            "out.json",
        ],
    )
    with pytest.raises(SystemExit) as exc_info:
        cli.main()
    assert exc_info.value.code == 2
    captured = capsys.readouterr()
    assert "cannot be combined" in captured.err


def test_show_evidence_refused_without_attended_terminal(monkeypatch, capsys):
    cli = _load_cli_module()
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_company_classification_lineage_diagnostic.py",
            "--identity",
            "Read Jones Christoffersen Ltd",
            "--show-evidence",
        ],
    )
    monkeypatch.setattr(cli, "_is_attended_terminal", lambda: False)
    exit_code = cli.main()
    assert exit_code == 1
    captured = capsys.readouterr()
    assert "attended terminal" in captured.err


def test_is_attended_terminal_false_in_this_test_environment():
    cli = _load_cli_module()
    assert cli._is_attended_terminal() is False


def test_identity_argument_is_required(monkeypatch, capsys):
    """Exercises the REAL parser inside main() -- omitting --identity
    must refuse before anything else runs."""
    cli = _load_cli_module()
    monkeypatch.setattr(
        sys, "argv", ["run_company_classification_lineage_diagnostic.py"]
    )
    with pytest.raises(SystemExit) as exc_info:
        cli.main()
    assert exc_info.value.code == 2
    captured = capsys.readouterr()
    assert "--identity" in captured.err


def test_no_company_id_flag_accepted(monkeypatch, capsys):
    """Never accept a UI-supplied numeric id -- the only input is
    --identity. Exercises the REAL parser: an unknown --company-id flag
    must be refused."""
    cli = _load_cli_module()
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_company_classification_lineage_diagnostic.py",
            "--identity",
            "Read Jones Christoffersen Ltd",
            "--company-id",
            "9801",
        ],
    )
    with pytest.raises(SystemExit) as exc_info:
        cli.main()
    assert exc_info.value.code == 2
    captured = capsys.readouterr()
    assert "unrecognized arguments" in captured.err


def test_print_evidence_shows_all_required_fields(capsys):
    cli = _load_cli_module()
    evidence = _fake_evidence()
    cli._print_evidence(evidence)
    out = capsys.readouterr().out

    assert "company_id=1" in out
    assert "company_name='Read Jones Christoffersen Ltd'" in out
    assert "company_type='General Contractor'" in out
    assert "primary_trade='engineering'" in out
    assert "cip_company_type='General Contractor'" in out
    assert "classification_method='no_match'" in out
    assert "review_category='confirmed_conflict'" in out
    assert SIGNAL_TRADE_TAG in out
    assert "passes_entity_analytics_filter=True" in out
    assert "some provenance line" in out


def test_print_evidence_never_invoked_by_default_output_path(
    monkeypatch, capsys, tmp_path
):
    """Without --show-evidence, main() must never call _print_evidence at
    all -- confirms neither stdout nor the written artifact file ever
    contain the resolved company's name."""
    cli = _load_cli_module()
    called = {"count": 0}
    monkeypatch.setattr(
        cli,
        "_print_evidence",
        lambda evidence: called.__setitem__("count", called["count"] + 1),
    )

    fake_aggregate = {
        "resolution_status": RESOLUTION_RESOLVED,
        "candidate_count": 1,
        "review_category": REVIEW_CONFIRMED_CONFLICT,
        "signal_histogram": {"name_pattern_conflict": 0, "trade_tag_conflict": 1},
        "known_firms_match_count": 0,
        "matching_rule_category_count": 0,
        "cohort_checks": {
            "passes_entity_analytics_filter": True,
            "passes_person_name_filter": True,
            "passes_gc_cohort_isolation_allowlist": True,
        },
        "digest": "x",
    }
    fake_evidence = _fake_evidence()
    monkeypatch.setattr(
        cli,
        "run_diagnostic",
        lambda engine, *, identity: (fake_aggregate, fake_evidence),
    )
    monkeypatch.setattr(
        cli, "guard_readonly_db_from_args", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(cli, "get_engine", lambda: MagicMock())
    monkeypatch.setattr(cli, "get_git_commit_sha", lambda: "deadbeef")

    artifact_path = tmp_path / "artifact.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_company_classification_lineage_diagnostic.py",
            "--identity",
            "Read Jones Christoffersen Ltd",
            "--artifact-path",
            str(artifact_path),
        ],
    )

    exit_code = cli.main()

    assert exit_code == 0
    assert called["count"] == 0
    out = capsys.readouterr().out
    assert "Read Jones Christoffersen" not in out
    assert "Read Jones Christoffersen" not in artifact_path.read_text(encoding="utf-8")


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
        name=f"MARKET2D Test Co {unique}",
        entity_role=ENTITY_ROLE_STANDALONE,
        company_type="General Contractor",
    )
    defaults.update(overrides)
    company = Company(**defaults)
    session.add(company)
    session.flush()
    return company


def test_rjc_like_fixture_resolves_and_shows_erroneous_gc_type(db_session):
    """The regression fixture: a structural-engineering firm currently
    mis-tagged company_type="General Contractor" (RJC's reported real
    profile) resolves exactly, is flagged confirmed_conflict via
    trade_tag_conflict, and its name/id never appear in the artifact-safe
    aggregate."""
    unique_name = f"Read Jones Christoffersen Ltd {uuid.uuid4().hex[:8]}"
    rjc = _make_company(
        db_session,
        name=unique_name,
        company_type="General Contractor",
        primary_trade="engineering",
        confidence_score=0.85,
    )

    aggregate, evidence = build_lineage_diagnostic(db_session, identity=unique_name)

    assert aggregate["resolution_status"] == RESOLUTION_RESOLVED
    assert aggregate["candidate_count"] == 1
    assert aggregate["review_category"] == REVIEW_CONFIRMED_CONFLICT
    assert aggregate["signal_histogram"]["trade_tag_conflict"] == 1
    assert aggregate["cohort_checks"]["passes_entity_analytics_filter"] is True

    assert evidence is not None
    assert evidence.company_id == rjc.id
    assert evidence.company_name == unique_name
    assert evidence.company_type == "General Contractor"
    assert evidence.primary_trade == "engineering"
    assert evidence.review_category == REVIEW_CONFIRMED_CONFLICT
    assert SIGNAL_TRADE_TAG in evidence.conflict_signals
    assert evidence.provenance

    serialized_aggregate = json.dumps(aggregate)
    assert unique_name not in serialized_aggregate
    assert "Christoffersen" not in serialized_aggregate
    assert str(rjc.id) not in json.dumps(aggregate["cohort_checks"])

    assert not db_session.dirty
    assert not db_session.new
    assert not db_session.deleted


def test_zero_matches_fixture_via_real_db(db_session):
    aggregate, evidence = build_lineage_diagnostic(
        db_session, identity=f"Nonexistent Firm {uuid.uuid4().hex}"
    )
    assert aggregate["resolution_status"] == RESOLUTION_NOT_FOUND
    assert aggregate["candidate_count"] == 0
    assert evidence is None
    assert aggregate["review_category"] is None


def test_ambiguous_fixture_via_real_db(db_session):
    """Two distinct Company rows sharing the exact same name must fail
    closed as ambiguous, never guessing one of them."""
    shared_name = f"Duplicate Firm Name {uuid.uuid4().hex[:8]}"
    _make_company(db_session, name=shared_name)
    _make_company(db_session, name=shared_name)

    aggregate, evidence = build_lineage_diagnostic(db_session, identity=shared_name)

    assert aggregate["resolution_status"] == RESOLUTION_AMBIGUOUS
    assert aggregate["candidate_count"] == 2
    assert evidence is None
    assert aggregate["review_category"] is None
    assert shared_name not in json.dumps(aggregate)


def test_lineage_diagnostic_makes_no_database_writes(db_session):
    unique_name = f"Zero Write Firm {uuid.uuid4().hex[:8]}"
    rjc = _make_company(
        db_session,
        name=unique_name,
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
        build_lineage_diagnostic(db_session, identity=unique_name)
    finally:
        event.remove(connection, "commit", on_commit)

    assert commits == []
    assert not db_session.new
    assert not db_session.dirty
    assert not db_session.deleted

    db_session.expire(rjc)
    reloaded = db_session.get(Company, rjc.id)
    assert reloaded.company_type == "General Contractor"


def test_digest_deterministic_across_two_real_calls(db_session):
    unique_name = f"Deterministic Digest Firm {uuid.uuid4().hex[:8]}"
    _make_company(
        db_session,
        name=unique_name,
        company_type="General Contractor",
        primary_trade="engineering",
        confidence_score=0.85,
    )

    aggregate_a, _ = build_lineage_diagnostic(db_session, identity=unique_name)
    aggregate_b, _ = build_lineage_diagnostic(db_session, identity=unique_name)

    assert aggregate_a["digest"] == aggregate_b["digest"]
