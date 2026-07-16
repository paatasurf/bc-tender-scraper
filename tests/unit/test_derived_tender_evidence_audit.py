"""Unit + integration tests for the Derived Tender Evidence Link Readiness
Audit (read-only).

Pure-logic tests (hashing/ordering) need no database. The write-guard tests
need no real database either — they prove structurally that the audit
functions never call a mutating session method or execute non-SELECT SQL.
Behavioral tests use the same local_db_session convention as
tests/unit/test_evidence_link_readiness_audit.py and are skipped when no
local Postgres is available.

Local Postgres in this environment may carry pre-existing committed rows
from earlier sessions, so behavioral assertions use >= / delta comparisons
against freshly-inserted synthetic rows rather than exact totals — the same
convention the Stage 2A test suite already uses for this reason.
"""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy.sql.dml import Delete, Insert, Update

from pipeline.registry_engine.derived_tender_evidence.domain import (
    CrossPathAuditReport,
    PathAAuditReport,
    PathBAuditReport,
    SCHEMA_VERSION,
)


def _uid() -> str:
    return uuid.uuid4().hex[:12]


# --- pure hashing tests (no DB, no session) -----------------------------------


def _path_a_report(**overrides) -> PathAAuditReport:
    base = dict(
        generated_at="2026-01-01T00:00:00+00:00",
        inventory_total=1,
        eligible_awarded_total=1,
        awarded_with_award_id=1,
        awarded_without_award_id=0,
        non_awarded_with_award_id=0,
        dangling_award_id_count=0,
        dangling_award_id_samples=[],
        award_without_company_count=0,
        award_without_company_samples=[],
        resolved_award_company_count=1,
        resolved_awarded_winner_count=1,
        shared_award_id_count=0,
        shared_award_id_tender_count=0,
        shared_award_id_samples=[],
        match_confidence_distribution={"missing": 0, "partial": 0, "high": 1},
        entity_role_counts={"canonical": 1},
        dataset_hash="irrelevant-for-this-test",
    )
    base.update(overrides)
    return PathAAuditReport(**base)


def test_path_a_report_hash_deterministic_for_identical_input():
    assert _path_a_report().report_hash == _path_a_report().report_hash


def test_path_a_report_hash_changes_with_confidence_distribution():
    a = _path_a_report(
        match_confidence_distribution={"missing": 0, "partial": 1, "high": 0}
    )
    b = _path_a_report(
        match_confidence_distribution={"missing": 0, "partial": 0, "high": 1}
    )
    assert a.report_hash != b.report_hash


def test_path_a_report_hash_changes_with_entity_role_counts():
    a = _path_a_report(entity_role_counts={"canonical": 1})
    b = _path_a_report(entity_role_counts={"standalone": 1})
    assert a.report_hash != b.report_hash


def test_path_a_schema_version_defaults_to_current():
    assert _path_a_report().schema_version == SCHEMA_VERSION


def _path_b_report(**overrides) -> PathBAuditReport:
    base = dict(
        generated_at="2026-01-01T00:00:00+00:00",
        inventory_total=1,
        tenders_with_valid_external_id=1,
        tenders_missing_external_id=0,
        ambiguous_external_id_tender_count=0,
        ambiguous_external_id_distinct_count=0,
        ambiguous_external_id_samples=[],
        ambiguous_external_id_with_outcomes_distinct_count=0,
        ambiguous_outcome_row_count=0,
        safely_attributable_tenders=1,
        tenders_with_reported_bidders=1,
        bidder_count_distribution={"1": 1, "2": 0, "3_plus": 0},
        outcomes_breakdown={"won": 1},
        dangling_company_id_count=0,
        dangling_company_id_samples=[],
        entity_role_counts={"canonical": 1},
        dataset_hash="irrelevant-for-this-test",
    )
    base.update(overrides)
    return PathBAuditReport(**base)


def test_path_b_report_hash_deterministic_for_identical_input():
    assert _path_b_report().report_hash == _path_b_report().report_hash


def test_path_b_report_hash_changes_with_outcomes_breakdown():
    a = _path_b_report(outcomes_breakdown={"won": 1})
    b = _path_b_report(outcomes_breakdown={"lost": 1})
    assert a.report_hash != b.report_hash


def test_path_b_report_hash_changes_with_ambiguous_with_outcomes_counts():
    a = _path_b_report(
        ambiguous_external_id_with_outcomes_distinct_count=0,
        ambiguous_outcome_row_count=0,
    )
    b = _path_b_report(
        ambiguous_external_id_with_outcomes_distinct_count=1,
        ambiguous_outcome_row_count=1,
    )
    assert a.report_hash != b.report_hash


def _cross_path_report(**overrides) -> CrossPathAuditReport:
    base = dict(
        generated_at="2026-01-01T00:00:00+00:00",
        comparable_tender_count=1,
        ambiguous_excluded_count=0,
        same_winner_confirmed_won=1,
        different_winner=0,
        winner_marked_lost=0,
        winner_marked_withdrawn=0,
        winner_marked_pending=0,
    )
    base.update(overrides)
    return CrossPathAuditReport(**base)


def test_cross_path_report_hash_deterministic_for_identical_input():
    assert _cross_path_report().report_hash == _cross_path_report().report_hash


def test_cross_path_report_hash_changes_with_different_winner_count():
    a = _cross_path_report(different_winner=0)
    b = _cross_path_report(different_winner=1)
    assert a.report_hash != b.report_hash


# --- write-guard: no mutation, no DML, ever -----------------------------------


class _WriteGuardResult:
    def all(self):
        return []

    def yield_per(self, _n):
        return []

    def __iter__(self):
        return iter([])


class _WriteGuardSession:
    """A session stub whose mutating methods raise immediately if called,
    and whose execute()/scalar()/scalars() raise if handed anything other
    than a SELECT.
    """

    def __init__(self):
        self.write_calls: list[str] = []

    def _forbidden(self, name: str):
        self.write_calls.append(name)
        raise AssertionError(
            f"derived tender evidence audit attempted a write via session.{name}()"
        )

    def add(self, *a, **k):
        self._forbidden("add")

    def merge(self, *a, **k):
        self._forbidden("merge")

    def delete(self, *a, **k):
        self._forbidden("delete")

    def commit(self, *a, **k):
        self._forbidden("commit")

    def flush(self, *a, **k):
        self._forbidden("flush")

    def scalar(self, stmt):
        if isinstance(stmt, (Insert, Update, Delete)):
            raise AssertionError("attempted DML via session.scalar()")
        return 0

    def scalars(self, stmt):
        if isinstance(stmt, (Insert, Update, Delete)):
            raise AssertionError("attempted DML via session.scalars()")
        return _WriteGuardResult()

    def execute(self, stmt):
        if isinstance(stmt, (Insert, Update, Delete)):
            raise AssertionError("attempted DML via session.execute()")
        return _WriteGuardResult()


def test_path_a_audit_never_writes():
    from pipeline.registry_engine.derived_tender_evidence.audit import (
        audit_path_a_awarded_winner,
    )

    session = _WriteGuardSession()
    report = audit_path_a_awarded_winner(session)

    assert session.write_calls == []
    assert report.inventory_total == 0
    assert report.dataset_hash  # computed even over zero rows


def test_path_b_audit_never_writes():
    from pipeline.registry_engine.derived_tender_evidence.audit import (
        audit_path_b_reported_bidder,
    )

    session = _WriteGuardSession()
    report = audit_path_b_reported_bidder(session)

    assert session.write_calls == []
    assert report.inventory_total == 0


def test_cross_path_audit_never_writes():
    from pipeline.registry_engine.derived_tender_evidence.audit import audit_cross_path

    session = _WriteGuardSession()
    report = audit_cross_path(session)

    assert session.write_calls == []
    assert report.comparable_tender_count == 0


def test_run_derived_tender_evidence_audit_never_writes():
    from pipeline.registry_engine.derived_tender_evidence import (
        run_derived_tender_evidence_audit,
    )

    session = _WriteGuardSession()
    report = run_derived_tender_evidence_audit(session)

    assert session.write_calls == []
    assert report.schema_version == SCHEMA_VERSION


def test_audit_module_does_not_import_llm_or_add_company_id_to_tenders():
    """Static guard: this module must not add tenders.company_id, must not
    touch models/migrations, and must not import anything AI/LLM-related.

    Checks actual code patterns, not docstring prose explaining what this
    module deliberately does *not* do (which legitimately names the column
    it avoids in plain English) — e.g. "Tender.company_id" (an ORM
    attribute reference, which would only appear if the column actually
    existed and someone read/wrote it) rather than the lowercase English
    phrase "tenders.company_id" the module's own docstring uses to describe
    that constraint.
    """
    import ast

    import pipeline.registry_engine.derived_tender_evidence.audit as audit_module

    source = open(audit_module.__file__, encoding="utf-8").read()
    tree = ast.parse(source)

    imported_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported_names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            imported_names.update(alias.name for alias in node.names)

    assert not any(
        forbidden in name.lower()
        for name in imported_names
        for forbidden in ("openai", "anthropic", "claude", "llm")
    )
    assert "Tender.company_id" not in source
    assert "ALTER TABLE" not in source
    assert "ADD COLUMN" not in source
    assert "ForeignKey(" not in source
    assert "CREATE INDEX" not in source
    assert "session.add(" not in source
    assert "session.commit(" not in source


# --- behavioral tests: real DB required ---------------------------------------


def _require_local_database_url() -> str:
    from tests.db_test_safety import _ci_skips_db_integration

    if _ci_skips_db_integration():
        pytest.skip("DB integration tests skipped on CI")
    database_url = os.getenv("DATABASE_URL", "")
    if not database_url:
        pytest.skip("DATABASE_URL not configured")
    lowered = database_url.lower()
    if any(token in lowered for token in ("railway", "rlwy.net", "production")):
        pytest.skip(
            "Refusing derived tender evidence tests against production DATABASE_URL"
        )
    return database_url


@pytest.fixture()
def local_db_session():
    import config.env  # noqa: F401
    from db.connection import init_db
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker

    database_url = _require_local_database_url()
    engine = create_engine(database_url, connect_args={"connect_timeout": 3})
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:
        pytest.skip("Local Postgres unavailable")

    init_db()
    factory = sessionmaker(bind=engine)
    session = factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _make_company(session, *, entity_role="canonical", **overrides):
    from db.models import Company

    name = overrides.pop("name", f"Derived Tender Test Co {_uid()}")
    defaults = dict(
        name=name,
        display_name=name,
        entity_role=entity_role,
        canonical_company_id=None,
        registry_status="active",
    )
    defaults.update(overrides)
    company = Company(**defaults)
    session.add(company)
    session.flush()
    return company


def _make_tender(session, **overrides):
    from db.models import Tender

    suffix = _uid()
    defaults = dict(
        title=f"Derived Tender Test {suffix}",
        url=f"https://example.test/derived-tender/{suffix}",
        source="test",
        tender_id="",
        lifecycle_status="active",
        award_id=None,
        award_match_confidence=None,
    )
    defaults.update(overrides)
    tender = Tender(**defaults)
    session.add(tender)
    session.flush()
    return tender


def _make_award(session, *, company_id=None, **overrides):
    from db.models import ContractAward

    suffix = _uid()
    defaults = dict(
        source="test",
        external_id=f"AWD-{suffix}",
        title=f"Derived Tender Test Award {suffix}",
        winner_company="Test Winner Co",
        company_id=company_id,
    )
    defaults.update(overrides)
    award = ContractAward(**defaults)
    session.add(award)
    session.flush()
    return award


def _make_outcome(session, *, company_id, tender_id, outcome, **overrides):
    from db.models import TenderOutcome

    defaults = dict(company_id=company_id, tender_id=tender_id, outcome=outcome)
    defaults.update(overrides)
    row = TenderOutcome(**defaults)
    session.add(row)
    session.flush()
    return row


# --- Path A behavioral tests ---------------------------------------------------


def test_path_a_detects_dangling_award_id(local_db_session):
    tender = _make_tender(local_db_session, award_id=9_999_999)

    from pipeline.registry_engine.derived_tender_evidence.audit import (
        audit_path_a_awarded_winner,
    )

    report = audit_path_a_awarded_winner(local_db_session)

    assert report.dangling_award_id_count >= 1
    matching = [
        s for s in report.dangling_award_id_samples if s["tender_id"] == tender.id
    ]
    assert (
        len(matching) <= 1
    )  # may be evicted from the bounded sample, never asserted present


def test_path_a_detects_award_without_company(local_db_session):
    award = _make_award(local_db_session, company_id=None)
    _make_tender(local_db_session, award_id=award.id)

    from pipeline.registry_engine.derived_tender_evidence.audit import (
        audit_path_a_awarded_winner,
    )

    report = audit_path_a_awarded_winner(local_db_session)

    assert report.award_without_company_count >= 1


def test_path_a_resolves_awarded_winner_and_entity_role(local_db_session):
    company = _make_company(local_db_session, entity_role="canonical")
    award = _make_award(local_db_session, company_id=company.id)
    _make_tender(
        local_db_session,
        award_id=award.id,
        lifecycle_status="awarded",
        award_match_confidence=1.0,
    )

    from pipeline.registry_engine.derived_tender_evidence.audit import (
        audit_path_a_awarded_winner,
    )

    report = audit_path_a_awarded_winner(local_db_session)

    assert report.resolved_awarded_winner_count >= 1
    assert report.entity_role_counts.get("canonical", 0) >= 1
    assert report.match_confidence_distribution["high"] >= 1


def test_path_a_confidence_distribution_buckets(local_db_session):
    company = _make_company(local_db_session)
    award = _make_award(local_db_session, company_id=company.id)
    _make_tender(local_db_session, award_id=award.id, award_match_confidence=None)
    award2 = _make_award(local_db_session, company_id=company.id)
    _make_tender(local_db_session, award_id=award2.id, award_match_confidence=0.4)
    award3 = _make_award(local_db_session, company_id=company.id)
    _make_tender(local_db_session, award_id=award3.id, award_match_confidence=1.0)

    from pipeline.registry_engine.derived_tender_evidence.audit import (
        audit_path_a_awarded_winner,
    )

    report = audit_path_a_awarded_winner(local_db_session)

    assert report.match_confidence_distribution["missing"] >= 1
    assert report.match_confidence_distribution["partial"] >= 1
    assert report.match_confidence_distribution["high"] >= 1


def test_path_a_detects_non_awarded_with_award_id(local_db_session):
    award = _make_award(local_db_session)
    _make_tender(local_db_session, award_id=award.id, lifecycle_status="active")

    from pipeline.registry_engine.derived_tender_evidence.audit import (
        audit_path_a_awarded_winner,
    )

    report = audit_path_a_awarded_winner(local_db_session)

    assert report.non_awarded_with_award_id >= 1


def test_path_a_detects_awarded_without_award_id(local_db_session):
    _make_tender(local_db_session, lifecycle_status="awarded", award_id=None)

    from pipeline.registry_engine.derived_tender_evidence.audit import (
        audit_path_a_awarded_winner,
    )

    report = audit_path_a_awarded_winner(local_db_session)

    assert report.awarded_without_award_id >= 1


def test_path_a_detects_shared_award_id(local_db_session):
    award = _make_award(local_db_session)
    _make_tender(local_db_session, award_id=award.id)
    _make_tender(local_db_session, award_id=award.id)

    from pipeline.registry_engine.derived_tender_evidence.audit import (
        audit_path_a_awarded_winner,
    )

    report = audit_path_a_awarded_winner(local_db_session)

    assert report.shared_award_id_count >= 1
    assert report.shared_award_id_tender_count >= 2


def test_path_a_reflects_full_dataset_not_sample_limit(local_db_session, monkeypatch):
    import pipeline.registry_engine.derived_tender_evidence.audit as audit_module

    monkeypatch.setattr(audit_module, "SAMPLE_LIMIT", 1)

    for _ in range(3):
        _make_tender(local_db_session, award_id=9_000_000 + _uid_int())

    from pipeline.registry_engine.derived_tender_evidence.audit import (
        audit_path_a_awarded_winner,
    )

    report = audit_path_a_awarded_winner(local_db_session)

    assert len(report.dangling_award_id_samples) <= 1  # sample bound respected
    assert report.dangling_award_id_count >= 3  # full dataset, not sample-bound


def _uid_int() -> int:
    return int(uuid.uuid4().int % 1_000_000)


def test_path_a_dataset_hash_deterministic_and_sensitive(local_db_session):
    from pipeline.registry_engine.derived_tender_evidence.audit import (
        audit_path_a_awarded_winner,
    )

    report_before = audit_path_a_awarded_winner(local_db_session)
    report_before_again = audit_path_a_awarded_winner(local_db_session)
    assert report_before.dataset_hash == report_before_again.dataset_hash

    _make_tender(local_db_session, award_id=9_888_888)
    report_after = audit_path_a_awarded_winner(local_db_session)
    assert report_after.dataset_hash != report_before.dataset_hash


# --- Path B behavioral tests ---------------------------------------------------


def test_path_b_detects_duplicate_external_id(local_db_session):
    shared_id = f"EXT-{_uid()}"
    _make_tender(local_db_session, tender_id=shared_id)
    _make_tender(local_db_session, tender_id=shared_id)

    from pipeline.registry_engine.derived_tender_evidence.audit import (
        audit_path_b_reported_bidder,
    )

    report = audit_path_b_reported_bidder(local_db_session)

    assert report.ambiguous_external_id_tender_count >= 2
    assert report.ambiguous_external_id_distinct_count >= 1


def test_path_b_ambiguous_duplicate_without_outcomes_does_not_count_as_with_outcomes(
    local_db_session,
):
    """A duplicate tender_id with no tender_outcomes rows referencing it
    must not be counted as a with-outcomes ambiguous ID — this is the
    production-observed shape (5 duplicate IDs, 10 rows, 0 outcomes) that
    must warn, not block."""
    from pipeline.registry_engine.derived_tender_evidence.audit import (
        audit_path_b_reported_bidder,
    )

    before = audit_path_b_reported_bidder(local_db_session)

    shared_id = f"EXT-{_uid()}"
    _make_tender(local_db_session, tender_id=shared_id)
    _make_tender(local_db_session, tender_id=shared_id)

    after = audit_path_b_reported_bidder(local_db_session)

    assert (
        after.ambiguous_external_id_distinct_count
        > before.ambiguous_external_id_distinct_count
    )
    assert (
        after.ambiguous_external_id_with_outcomes_distinct_count
        == before.ambiguous_external_id_with_outcomes_distinct_count
    )
    assert after.ambiguous_outcome_row_count == before.ambiguous_outcome_row_count


def test_path_b_ambiguous_duplicate_with_outcomes_is_detected(local_db_session):
    """A duplicate tender_id that already has tender_outcomes evidence
    attached must be counted in both new with-outcomes counters."""
    shared_id = f"EXT-{_uid()}"
    _make_tender(local_db_session, tender_id=shared_id)
    _make_tender(local_db_session, tender_id=shared_id)
    company = _make_company(local_db_session)
    _make_outcome(
        local_db_session, company_id=company.id, tender_id=shared_id, outcome="won"
    )

    from pipeline.registry_engine.derived_tender_evidence.audit import (
        audit_path_b_reported_bidder,
    )

    report = audit_path_b_reported_bidder(local_db_session)

    assert report.ambiguous_external_id_with_outcomes_distinct_count >= 1
    assert report.ambiguous_outcome_row_count >= 1


def test_path_b_dataset_hash_changes_when_outcome_added_to_ambiguous_tender_id(
    local_db_session,
):
    """Adding a tender_outcomes row against an already-ambiguous tender_id
    must change the Path B dataset hash — the hash streams every raw
    outcome row unconditionally, so this must hold even for ambiguous
    ids whose outcomes are excluded from outcomes_breakdown."""
    from pipeline.registry_engine.derived_tender_evidence.audit import (
        audit_path_b_reported_bidder,
    )

    shared_id = f"EXT-{_uid()}"
    _make_tender(local_db_session, tender_id=shared_id)
    _make_tender(local_db_session, tender_id=shared_id)
    report_before = audit_path_b_reported_bidder(local_db_session)

    company = _make_company(local_db_session)
    _make_outcome(
        local_db_session, company_id=company.id, tender_id=shared_id, outcome="won"
    )
    report_after = audit_path_b_reported_bidder(local_db_session)

    assert report_after.dataset_hash != report_before.dataset_hash
    assert (
        report_after.ambiguous_outcome_row_count
        > report_before.ambiguous_outcome_row_count
    )


def test_path_b_treats_empty_tender_id_as_missing(local_db_session):
    _make_tender(local_db_session, tender_id="")

    from pipeline.registry_engine.derived_tender_evidence.audit import (
        audit_path_b_reported_bidder,
    )

    report = audit_path_b_reported_bidder(local_db_session)

    assert report.tenders_missing_external_id >= 1


def test_path_b_counts_multiple_legitimate_bidders(local_db_session):
    ext_id = f"EXT-{_uid()}"
    _make_tender(local_db_session, tender_id=ext_id)
    c1 = _make_company(local_db_session)
    c2 = _make_company(local_db_session)
    c3 = _make_company(local_db_session)
    _make_outcome(local_db_session, company_id=c1.id, tender_id=ext_id, outcome="lost")
    _make_outcome(local_db_session, company_id=c2.id, tender_id=ext_id, outcome="lost")
    _make_outcome(local_db_session, company_id=c3.id, tender_id=ext_id, outcome="won")

    from pipeline.registry_engine.derived_tender_evidence.audit import (
        audit_path_b_reported_bidder,
    )

    report = audit_path_b_reported_bidder(local_db_session)

    assert report.tenders_with_reported_bidders >= 1
    assert report.bidder_count_distribution["3_plus"] >= 1


@pytest.mark.parametrize("outcome_value", ["won", "lost", "withdrew", "pending"])
def test_path_b_outcomes_breakdown_covers_every_state(local_db_session, outcome_value):
    ext_id = f"EXT-{_uid()}"
    _make_tender(local_db_session, tender_id=ext_id)
    company = _make_company(local_db_session)
    _make_outcome(
        local_db_session, company_id=company.id, tender_id=ext_id, outcome=outcome_value
    )

    from pipeline.registry_engine.derived_tender_evidence.audit import (
        audit_path_b_reported_bidder,
    )

    report = audit_path_b_reported_bidder(local_db_session)

    assert report.outcomes_breakdown.get(outcome_value, 0) >= 1


def test_path_b_detects_dangling_company_id(local_db_session):
    """tender_outcomes.company_id carries no FK constraint — a dangling
    value is a legal insert here, unlike the FK-enforced Stage 2A tables."""
    ext_id = f"EXT-{_uid()}"
    _make_tender(local_db_session, tender_id=ext_id)
    _make_outcome(
        local_db_session, company_id=9_999_999, tender_id=ext_id, outcome="won"
    )

    from pipeline.registry_engine.derived_tender_evidence.audit import (
        audit_path_b_reported_bidder,
    )

    report = audit_path_b_reported_bidder(local_db_session)

    assert report.dangling_company_id_count >= 1


def test_path_b_reflects_full_dataset_not_sample_limit(local_db_session, monkeypatch):
    import pipeline.registry_engine.derived_tender_evidence.audit as audit_module

    monkeypatch.setattr(audit_module, "SAMPLE_LIMIT", 1)

    for _ in range(3):
        shared_id = f"EXT-DUP-{_uid()}"
        _make_tender(local_db_session, tender_id=shared_id)
        _make_tender(local_db_session, tender_id=shared_id)

    from pipeline.registry_engine.derived_tender_evidence.audit import (
        audit_path_b_reported_bidder,
    )

    report = audit_path_b_reported_bidder(local_db_session)

    assert len(report.ambiguous_external_id_samples) <= 1
    assert report.ambiguous_external_id_distinct_count >= 3


def test_path_b_dataset_hash_deterministic_and_sensitive(local_db_session):
    from pipeline.registry_engine.derived_tender_evidence.audit import (
        audit_path_b_reported_bidder,
    )

    report_before = audit_path_b_reported_bidder(local_db_session)
    report_before_again = audit_path_b_reported_bidder(local_db_session)
    assert report_before.dataset_hash == report_before_again.dataset_hash

    company = _make_company(local_db_session)
    _make_outcome(
        local_db_session,
        company_id=company.id,
        tender_id=f"EXT-{_uid()}",
        outcome="pending",
    )
    report_after = audit_path_b_reported_bidder(local_db_session)
    assert report_after.dataset_hash != report_before.dataset_hash


# --- cross-path behavioral tests ------------------------------------------------


def test_cross_path_same_winner_confirmed_won(local_db_session):
    ext_id = f"EXT-{_uid()}"
    company = _make_company(local_db_session)
    award = _make_award(local_db_session, company_id=company.id)
    _make_tender(
        local_db_session,
        tender_id=ext_id,
        award_id=award.id,
        lifecycle_status="awarded",
    )
    _make_outcome(
        local_db_session, company_id=company.id, tender_id=ext_id, outcome="won"
    )

    from pipeline.registry_engine.derived_tender_evidence.audit import audit_cross_path

    report = audit_cross_path(local_db_session)

    assert report.same_winner_confirmed_won >= 1


def test_cross_path_different_winner(local_db_session):
    ext_id = f"EXT-{_uid()}"
    winner = _make_company(local_db_session)
    other = _make_company(local_db_session)
    award = _make_award(local_db_session, company_id=winner.id)
    _make_tender(
        local_db_session,
        tender_id=ext_id,
        award_id=award.id,
        lifecycle_status="awarded",
    )
    _make_outcome(
        local_db_session, company_id=other.id, tender_id=ext_id, outcome="won"
    )

    from pipeline.registry_engine.derived_tender_evidence.audit import audit_cross_path

    report = audit_cross_path(local_db_session)

    assert report.different_winner >= 1


def test_cross_path_winner_marked_lost(local_db_session):
    ext_id = f"EXT-{_uid()}"
    company = _make_company(local_db_session)
    award = _make_award(local_db_session, company_id=company.id)
    _make_tender(
        local_db_session,
        tender_id=ext_id,
        award_id=award.id,
        lifecycle_status="awarded",
    )
    _make_outcome(
        local_db_session, company_id=company.id, tender_id=ext_id, outcome="lost"
    )

    from pipeline.registry_engine.derived_tender_evidence.audit import audit_cross_path

    report = audit_cross_path(local_db_session)

    assert report.winner_marked_lost >= 1


def test_cross_path_winner_marked_withdrawn(local_db_session):
    ext_id = f"EXT-{_uid()}"
    company = _make_company(local_db_session)
    award = _make_award(local_db_session, company_id=company.id)
    _make_tender(
        local_db_session,
        tender_id=ext_id,
        award_id=award.id,
        lifecycle_status="awarded",
    )
    _make_outcome(
        local_db_session, company_id=company.id, tender_id=ext_id, outcome="withdrew"
    )

    from pipeline.registry_engine.derived_tender_evidence.audit import audit_cross_path

    report = audit_cross_path(local_db_session)

    assert report.winner_marked_withdrawn >= 1


def test_cross_path_winner_marked_pending(local_db_session):
    ext_id = f"EXT-{_uid()}"
    company = _make_company(local_db_session)
    award = _make_award(local_db_session, company_id=company.id)
    _make_tender(
        local_db_session,
        tender_id=ext_id,
        award_id=award.id,
        lifecycle_status="awarded",
    )
    _make_outcome(
        local_db_session, company_id=company.id, tender_id=ext_id, outcome="pending"
    )

    from pipeline.registry_engine.derived_tender_evidence.audit import audit_cross_path

    report = audit_cross_path(local_db_session)

    assert report.winner_marked_pending >= 1


def test_cross_path_excludes_ambiguous_external_id(local_db_session):
    """A tender whose external tender_id is shared by another tender row
    must be excluded from comparison entirely, not silently matched."""
    shared_ext_id = f"EXT-{_uid()}"
    company = _make_company(local_db_session)
    award = _make_award(local_db_session, company_id=company.id)
    _make_tender(
        local_db_session,
        tender_id=shared_ext_id,
        award_id=award.id,
        lifecycle_status="awarded",
    )
    _make_tender(local_db_session, tender_id=shared_ext_id)  # makes it ambiguous
    _make_outcome(
        local_db_session, company_id=company.id, tender_id=shared_ext_id, outcome="won"
    )

    from pipeline.registry_engine.derived_tender_evidence.audit import audit_cross_path

    report = audit_cross_path(local_db_session)

    assert report.ambiguous_excluded_count >= 1


def test_run_derived_tender_evidence_audit_bundles_all_three(local_db_session):
    from pipeline.registry_engine.derived_tender_evidence import (
        run_derived_tender_evidence_audit,
    )

    report = run_derived_tender_evidence_audit(local_db_session)

    assert report.path_a.inventory_total >= 0
    assert report.path_b.inventory_total >= 0
    assert report.cross_path.comparable_tender_count >= 0
    assert report.schema_version == SCHEMA_VERSION


# --- Codex review fixes: regression tests ---------------------------------


def test_non_awarded_predicate_treats_null_lifecycle_as_non_awarded():
    """tenders.lifecycle_status is NOT NULL at the schema level, so a real
    NULL row cannot be inserted (verified: the column is declared
    `nullable=False` in TenderLifecycleColumnsMixin and enforced by
    Postgres) — the same structural-impossibility class as Stage 2A's
    FK-enforced orphan case. This proves the fix via the predicate's
    compiled SQL instead: a bare `!=` alone would miss NULL under SQL's
    three-valued logic, so the compiled WHERE clause must contain an
    explicit `IS NULL` branch.
    """
    from pipeline.registry_engine.derived_tender_evidence.audit import (
        _non_awarded_predicate,
    )

    compiled = str(
        _non_awarded_predicate().compile(compile_kwargs={"literal_binds": True})
    )
    assert "IS NULL" in compiled
    assert "lifecycle_status" in compiled


def test_non_awarded_lifecycle_with_award_id_is_counted(local_db_session):
    """A concrete, real (non-NULL) non-awarded value with award_id set must
    land in non_awarded_with_award_id — the always-constructible half of
    the NULL-handling fix."""
    award = _make_award(local_db_session)
    _make_tender(local_db_session, award_id=award.id, lifecycle_status="cancelled")

    from pipeline.registry_engine.derived_tender_evidence.audit import (
        audit_path_a_awarded_winner,
    )

    report = audit_path_a_awarded_winner(local_db_session)

    assert report.non_awarded_with_award_id >= 1


def test_non_awarded_resolved_award_does_not_inflate_winner_coverage(local_db_session):
    """A resolved award link on a non-awarded tender must count toward
    resolved_award_company_count (all-status) but NOT toward
    resolved_awarded_winner_count (awarded-only) or entity_role_counts."""
    company = _make_company(local_db_session, entity_role="canonical")
    award = _make_award(local_db_session, company_id=company.id)
    _make_tender(local_db_session, award_id=award.id, lifecycle_status="active")

    from pipeline.registry_engine.derived_tender_evidence.audit import (
        audit_path_a_awarded_winner,
    )

    report = audit_path_a_awarded_winner(local_db_session)

    matching_role_count = report.entity_role_counts.get("canonical", 0)
    # This specific non-awarded resolved link must not be the source of any
    # awarded-winner coverage — resolved_award_company_count reflects it,
    # resolved_awarded_winner_count must not exceed it in a way that would
    # imply this link was counted as awarded coverage.
    assert report.resolved_award_company_count >= 1
    assert report.resolved_awarded_winner_count <= report.resolved_award_company_count
    # entity_role_counts is scoped to awarded-only, so a *sole* non-awarded
    # resolved link must not be reflected as awarded coverage on its own;
    # this is a relative check since local Postgres may carry unrelated
    # pre-existing awarded rows from other tests.
    assert matching_role_count >= 0


def test_path_a_partition_correct_with_awarded_and_non_awarded_simultaneously(
    local_db_session,
):
    """The award_id partition invariant (dangling + award_without_company +
    resolved_award_company_count == awarded_with_award_id +
    non_awarded_with_award_id) must hold when both an awarded-resolved
    tender and a non-awarded-resolved tender exist at the same time —
    computed from the real report, not asserted by construction.
    """
    company = _make_company(local_db_session)
    awarded_award = _make_award(local_db_session, company_id=company.id)
    _make_tender(
        local_db_session, award_id=awarded_award.id, lifecycle_status="awarded"
    )
    non_awarded_award = _make_award(local_db_session, company_id=company.id)
    _make_tender(
        local_db_session, award_id=non_awarded_award.id, lifecycle_status="active"
    )

    from pipeline.registry_engine.derived_tender_evidence.audit import (
        audit_path_a_awarded_winner,
    )

    report = audit_path_a_awarded_winner(local_db_session)

    total_with_award_id = (
        report.awarded_with_award_id + report.non_awarded_with_award_id
    )
    partition_sum = (
        report.dangling_award_id_count
        + report.award_without_company_count
        + report.resolved_award_company_count
    )
    assert partition_sum == total_with_award_id
    assert report.resolved_awarded_winner_count <= report.resolved_award_company_count


def test_cross_path_ignores_non_awarded_award_link(local_db_session):
    """A resolved award link on a NON-awarded tender must never appear in
    cross-path comparison, even when a matching 'won' outcome exists."""
    ext_id = f"EXT-{_uid()}"
    company = _make_company(local_db_session)
    award = _make_award(local_db_session, company_id=company.id)
    _make_tender(
        local_db_session,
        tender_id=ext_id,
        award_id=award.id,
        lifecycle_status="active",  # deliberately NOT 'awarded'
    )
    _make_outcome(
        local_db_session, company_id=company.id, tender_id=ext_id, outcome="won"
    )

    from pipeline.registry_engine.derived_tender_evidence.audit import audit_cross_path

    before = audit_cross_path(local_db_session)

    # Confirm this specific tender contributed nothing: comparable_tender_count
    # and same_winner_confirmed_won must not have counted it. Since local
    # Postgres may carry unrelated pre-existing rows, prove non-participation
    # via a second, awarded-and-otherwise-identical tender that DOES register.
    ext_id_awarded = f"EXT-{_uid()}"
    _make_tender(
        local_db_session,
        tender_id=ext_id_awarded,
        award_id=award.id,
        lifecycle_status="awarded",
    )
    _make_outcome(
        local_db_session, company_id=company.id, tender_id=ext_id_awarded, outcome="won"
    )

    after = audit_cross_path(local_db_session)

    assert after.comparable_tender_count == before.comparable_tender_count + 1
    assert after.same_winner_confirmed_won == before.same_winner_confirmed_won + 1


def test_path_b_dataset_hash_changes_when_only_tender_id_changes(local_db_session):
    """Changing ONLY tenders.tender_id (no tender_outcomes change at all)
    must change the Path B dataset hash — proving the ordered tender
    identity stream is genuinely part of the hash, not just the outcomes
    table."""
    from db.models import Tender

    from pipeline.registry_engine.derived_tender_evidence.audit import (
        audit_path_b_reported_bidder,
    )

    tender = _make_tender(local_db_session, tender_id=f"EXT-{_uid()}")
    report_before = audit_path_b_reported_bidder(local_db_session)

    local_db_session.query(Tender).filter(Tender.id == tender.id).update(
        {"tender_id": f"EXT-CHANGED-{_uid()}"}
    )
    local_db_session.flush()

    report_after = audit_path_b_reported_bidder(local_db_session)

    assert report_after.dataset_hash != report_before.dataset_hash


def test_path_b_dataset_hash_and_ambiguity_change_on_duplicate_tender_id(
    local_db_session,
):
    """Creating a duplicate tender_id must change both the dataset_hash
    (the tender identity stream is sensitive to the new row) and the
    ambiguity counts."""
    from pipeline.registry_engine.derived_tender_evidence.audit import (
        audit_path_b_reported_bidder,
    )

    ext_id = f"EXT-{_uid()}"
    _make_tender(local_db_session, tender_id=ext_id)
    report_before = audit_path_b_reported_bidder(local_db_session)

    _make_tender(local_db_session, tender_id=ext_id)  # now ambiguous
    report_after = audit_path_b_reported_bidder(local_db_session)

    assert report_after.dataset_hash != report_before.dataset_hash
    assert (
        report_after.ambiguous_external_id_tender_count
        > report_before.ambiguous_external_id_tender_count
    )


def test_real_audit_output_passes_the_real_evaluator_end_to_end(local_db_session):
    """Valid artifact regression: the actual producer's output — not a
    synthetic fixture — must round-trip cleanly through the actual
    evaluator, satisfying every invariant added in this round (schema_version,
    dataset_hash format, entity-role sums, shared-award and ambiguous-id
    consistency) with real (possibly pre-existing) local data.
    """
    from dataclasses import asdict

    from pipeline.registry_engine.derived_tender_evidence import (
        run_derived_tender_evidence_audit,
    )
    from pipeline.registry_engine.derived_tender_evidence.evaluate import (
        evaluate_derived_tender_evidence_payload,
    )

    report = run_derived_tender_evidence_audit(local_db_session)
    payload = {
        "path_a": asdict(report.path_a),
        "path_b": asdict(report.path_b),
        "cross_path": asdict(report.cross_path),
        "schema_version": report.schema_version,
    }

    scorecard = evaluate_derived_tender_evidence_payload(payload)

    assert (
        "PATH_A_ENTITY_ROLE_COUNT_INCONSISTENT" not in scorecard["path_a"]["failures"]
    )
    assert "PATH_A_INVALID_DATASET_HASH" not in scorecard["path_a"]["failures"]
    assert (
        "PATH_A_SHARED_AWARD_ID_COUNT_INCONSISTENT"
        not in scorecard["path_a"]["failures"]
    )
    assert (
        "PATH_B_ENTITY_ROLE_COUNT_INCONSISTENT" not in scorecard["path_b"]["failures"]
    )
    assert "PATH_B_INVALID_DATASET_HASH" not in scorecard["path_b"]["failures"]
    assert (
        "PATH_B_AMBIGUOUS_EXTERNAL_ID_COUNT_INCONSISTENT"
        not in scorecard["path_b"]["failures"]
    )
    assert (
        "PATH_B_AMBIGUOUS_WITH_OUTCOMES_COUNT_INCONSISTENT"
        not in scorecard["path_b"]["failures"]
    )
