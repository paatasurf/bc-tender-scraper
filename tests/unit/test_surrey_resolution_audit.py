"""Tests for the Surrey permit resolution-readiness audit (PR-EN1C).

Sections:
  1. CompanyIndex matching -- local Postgres only (real Company rows).
  2. audit_surrey_permit_resolution row classification -- local Postgres
     only (real Permit rows).
  3. Digest determinism -- no DB.
  4. run_audit transaction contract (one connection/transaction, READ ONLY
     first, guaranteed rollback/close, artifact absent on failure,
     deterministic sampling, zero mutations, no leaked identifiers) --
     local Postgres only.
  5. CLI-level -- no DB (argparse rejects --apply/--allow-production
     before any connection is attempted; CLASSIFICATION.md documents the
     new script).
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.orm import Session

from db.company_canonical_constants import (
    ENTITY_ROLE_APPLICANT_ALIAS,
    ENTITY_ROLE_CANONICAL,
    ENTITY_ROLE_STANDALONE,
    FORCED_CANONICAL_IDS_BY_KEY,
)
from db.models import Company, Permit
from pipeline.company_canonical_merge import resolve_company_name
from pipeline.surrey_applicant import SurreyApplicantNormalization
from pipeline.surrey_resolution_audit import (
    UNCLASSIFIED_ERROR_TYPE,
    CompanyIndex,
    SurreyResolutionAuditError,
    audit_surrey_permit_resolution,
    compute_examined_digest,
)
from tests.db_test_safety import require_local_test_database

# ===================================================================
# 3. Digest determinism -- no DB
# ===================================================================


def test_examined_digest_is_deterministic_regardless_of_input_order():
    assert compute_examined_digest([3, 1, 2]) == compute_examined_digest([1, 2, 3])
    assert compute_examined_digest([1, 2, 3]) == compute_examined_digest([2, 3, 1, 1])


def test_examined_digest_changes_with_a_different_set():
    assert compute_examined_digest([1, 2, 3]) != compute_examined_digest([1, 2, 4])


def test_examined_digest_is_full_length_sha256_hex():
    digest = compute_examined_digest([42])
    assert len(digest) == 64
    int(digest, 16)  # raises ValueError if not valid hex


# ===================================================================
# Local Postgres fixture + helpers (rollback-only, matches the
# established db_session convention used across this repo's tests)
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
        name=f"MO1C Test Co {unique}",
        entity_role=ENTITY_ROLE_STANDALONE,
    )
    defaults.update(overrides)
    company = Company(**defaults)
    session.add(company)
    session.flush()
    return company


def _make_surrey_permit(session: Session, **overrides) -> Permit:
    unique = uuid.uuid4().hex[:8]
    defaults: dict = dict(
        address=f"{unique} Test Ave",
        source="surrey",
        city="Surrey",
        applicant="",
        external_id="",
    )
    defaults.update(overrides)
    permit = Permit(**defaults)
    session.add(permit)
    session.flush()
    return permit


# ===================================================================
# 1. CompanyIndex matching -- local Postgres only
# ===================================================================


def test_company_index_direct_key_match(db_session):
    _make_company(db_session, name="Tyrrell Projects Inc")

    index = CompanyIndex(db_session)
    outcome, method = index.match("Tyrrell Projects Inc")

    assert outcome == "matched"
    assert method == "direct_key"


def test_company_index_canonical_alias_collapses_to_one_identity(db_session):
    canonical = _make_company(
        db_session, name="Realco Group Ltd", entity_role=ENTITY_ROLE_CANONICAL
    )
    _make_company(
        db_session,
        name="Realco Group Ltd Alias Name",
        display_name="Realco Group Ltd",
        entity_role=ENTITY_ROLE_APPLICANT_ALIAS,
        canonical_company_id=canonical.id,
    )

    index = CompanyIndex(db_session)
    outcome, method = index.match("Realco Group Ltd")

    assert outcome == "matched"
    assert method in {"direct_key", "alias_collapsed"}


def test_company_index_ambiguous_when_two_distinct_companies_share_a_key(db_session):
    _make_company(
        db_session,
        name="Ambigcorp Holdings Ltd A",
        display_name="Ambigcorp Holdings Ltd",
        entity_role=ENTITY_ROLE_STANDALONE,
    )
    _make_company(
        db_session,
        name="Ambigcorp Holdings Ltd B",
        display_name="Ambigcorp Holdings Ltd",
        entity_role=ENTITY_ROLE_STANDALONE,
    )

    index = CompanyIndex(db_session)
    outcome, method = index.match("Ambigcorp Holdings Ltd")

    assert outcome == "ambiguous"
    assert method is None


def test_company_index_unmatched_when_no_existing_company(db_session):
    index = CompanyIndex(db_session)
    outcome, method = index.match("Totally Nonexistent Fictitious Firm Ltd")

    assert outcome == "unmatched"
    assert method is None


def test_company_index_forced_override_used_when_no_direct_key_match(
    db_session, monkeypatch
):
    target = _make_company(db_session, name="Forced Target Company Ltd")
    parsed = resolve_company_name("Forceonly Applicant Name Ltd")
    assert parsed is not None
    monkeypatch.setitem(
        FORCED_CANONICAL_IDS_BY_KEY, parsed.canonical_key, int(target.id)
    )

    index = CompanyIndex(db_session)
    outcome, method = index.match("Forceonly Applicant Name Ltd")

    assert outcome == "matched"
    assert method == "forced_override"


def test_company_index_never_writes(db_session):
    _make_company(db_session, name="No Write Check Ltd")
    db_session.flush()

    CompanyIndex(db_session).match("No Write Check Ltd")

    assert not db_session.new
    assert not db_session.dirty
    assert not db_session.deleted


# ===================================================================
# 2. audit_surrey_permit_resolution row classification -- local Postgres
# ===================================================================


def test_missing_applicant_counted_and_not_resolved(db_session):
    _make_surrey_permit(db_session, applicant="")

    report = audit_surrey_permit_resolution(db_session)

    assert report["counts"]["total"] == 1
    assert report["counts"]["applicant_missing"] == 1
    assert report["counts"]["normalized_safe"] == 0


def test_unresolved_applicant_counted_and_not_resolved(db_session):
    _make_surrey_permit(
        db_session,
        applicant="Rashpal Singh Padda and RRA New Homes Ltd 123 Main St Surrey",
    )

    report = audit_surrey_permit_resolution(db_session)

    assert report["counts"]["normalization_unresolved"] == 1
    assert report["counts"]["normalized_safe"] == 0


def test_safe_normalization_and_matched_existing_company(db_session):
    _make_company(db_session, name="Tyrrell Projects Inc")
    _make_surrey_permit(
        db_session,
        applicant="Tyrrell Projects Inc 19949 56 Ave Surrey, British Columbia",
    )

    report = audit_surrey_permit_resolution(db_session)

    assert report["counts"]["normalized_safe"] == 1
    assert report["counts"]["matched_existing_company"] == 1
    assert report["confidence_tier_histogram"].get("high") == 1


def test_safe_normalization_unmatched_existing_company(db_session):
    _make_surrey_permit(
        db_session,
        applicant="Builden Construction Unit 508 13761 96 Ave Surrey, BC",
    )

    report = audit_surrey_permit_resolution(db_session)

    assert report["counts"]["normalized_safe"] == 1
    assert report["counts"]["unmatched_existing_company"] == 1
    assert report["confidence_tier_histogram"].get("medium") == 1


def test_ambiguous_existing_company_also_counts_as_duplicate_risk(db_session):
    _make_company(
        db_session,
        name="Duplicorp Projects Inc A",
        display_name="Duplicorp Projects Inc",
    )
    _make_company(
        db_session,
        name="Duplicorp Projects Inc B",
        display_name="Duplicorp Projects Inc",
    )
    _make_surrey_permit(
        db_session,
        applicant="Duplicorp Projects Inc 19949 56 Ave Surrey, BC",
    )

    report = audit_surrey_permit_resolution(db_session)

    assert report["counts"]["ambiguous_existing_company"] == 1
    assert report["counts"]["duplicate_risk"] == 1


def test_count_invariants_hold_across_a_mixed_batch(db_session):
    _make_company(db_session, name="Tyrrell Projects Inc")
    _make_surrey_permit(db_session, applicant="")
    _make_surrey_permit(
        db_session,
        applicant="Rashpal Singh Padda and RRA New Homes Ltd 123 Main St Surrey",
    )
    _make_surrey_permit(
        db_session,
        applicant="Tyrrell Projects Inc 19949 56 Ave Surrey, British Columbia",
    )
    _make_surrey_permit(
        db_session,
        applicant="Builden Construction Unit 508 13761 96 Ave Surrey, BC",
    )

    report = audit_surrey_permit_resolution(db_session)
    counts = report["counts"]

    assert counts["total"] == 4
    assert (
        counts["applicant_missing"]
        + counts["normalized_safe"]
        + counts["normalization_unresolved"]
        + counts["errors"]
        == counts["total"]
    )
    assert (
        counts["matched_existing_company"]
        + counts["ambiguous_existing_company"]
        + counts["unmatched_existing_company"]
        == counts["normalized_safe"]
    )


def test_non_surrey_permits_are_excluded(db_session):
    _make_surrey_permit(db_session, applicant="", source="vancouver")

    report = audit_surrey_permit_resolution(db_session)

    assert report["counts"]["total"] == 0


def test_audit_never_mutates_permit_or_company_rows(db_session):
    company = _make_company(db_session, name="Tyrrell Projects Inc")
    permit = _make_surrey_permit(
        db_session,
        applicant="Tyrrell Projects Inc 19949 56 Ave Surrey, British Columbia",
    )
    db_session.flush()

    company_count_before = db_session.execute(select(Company.id)).all()
    audit_surrey_permit_resolution(db_session)
    company_count_after = db_session.execute(select(Company.id)).all()

    assert company_count_before == company_count_after
    assert not db_session.new
    assert not db_session.dirty
    assert not db_session.deleted
    db_session.refresh(permit)
    assert permit.company_id is None
    db_session.refresh(company)


# ===================================================================
# 4. run_audit transaction contract -- local Postgres only
# ===================================================================


def _engine_for(database_url: str):
    return create_engine(database_url, connect_args={"connect_timeout": 3})


@pytest.fixture()
def local_database_url():
    database_url = require_local_test_database()
    try:
        probe_engine = _engine_for(database_url)
        with probe_engine.connect() as probe:
            probe.execute(text("SELECT 1"))
        probe_engine.dispose()
    except Exception:
        pytest.skip("Local Postgres unavailable")
    return database_url


def test_read_only_statement_is_first_and_transaction_is_rolled_back(
    local_database_url, tmp_path
):
    from scripts.run_surrey_resolution_audit import run_audit

    engine = _engine_for(local_database_url)
    statements: list[str] = []
    commits: list[int] = []
    rollbacks: list[int] = []

    @event.listens_for(engine, "before_cursor_execute")
    def _capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement.strip())

    @event.listens_for(engine, "commit")
    def _on_commit(conn):
        commits.append(1)

    @event.listens_for(engine, "rollback")
    def _on_rollback(conn):
        rollbacks.append(1)

    try:
        run_audit(
            engine,
            artifact_path=tmp_path / "artifact.json",
            sample_size=0,
        )
    finally:
        engine.dispose()

    non_begin = [s for s in statements if s.upper() not in {"BEGIN", "BEGIN;"}]
    assert non_begin, "expected at least one statement after BEGIN"
    assert (
        "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
        in non_begin[0].upper()
    )
    assert commits == []
    assert rollbacks == [1]


def test_run_audit_rolls_back_and_closes_on_exception_and_writes_no_artifact(
    local_database_url, tmp_path
):
    from scripts.run_surrey_resolution_audit import run_audit

    engine = _engine_for(local_database_url)
    rollbacks: list[int] = []

    @event.listens_for(engine, "rollback")
    def _on_rollback(conn):
        rollbacks.append(1)

    artifact_path = tmp_path / "should_not_exist.json"

    with patch(
        "scripts.run_surrey_resolution_audit.audit_surrey_permit_resolution",
        side_effect=RuntimeError("boom"),
    ):
        with pytest.raises(RuntimeError):
            run_audit(engine, artifact_path=artifact_path, sample_size=0)

    engine.dispose()

    assert rollbacks == [1]
    assert not artifact_path.exists()


def test_run_audit_writes_artifact_only_after_success(local_database_url, tmp_path):
    from scripts.run_surrey_resolution_audit import run_audit

    engine = _engine_for(local_database_url)
    artifact_path = tmp_path / "artifact.json"
    try:
        artifact = run_audit(engine, artifact_path=artifact_path, sample_size=0)
    finally:
        engine.dispose()

    assert artifact_path.exists()
    on_disk = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert on_disk == artifact


@pytest.fixture()
def committing_session(local_database_url):
    """A real, durably-committing session -- deliberately NOT the
    rollback-only db_session fixture, because these tests need a second,
    independent connection (opened by run_audit itself) to actually see
    the seeded rows. Caller is responsible for its own cleanup; this
    fixture only guarantees the session/engine are closed."""
    engine = _engine_for(local_database_url)
    session = Session(bind=engine)
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def test_sample_size_examination_is_deterministic_across_runs(
    committing_session, local_database_url, tmp_path
):
    _make_company(committing_session, name="Tyrrell Projects Inc")
    for _ in range(3):
        _make_surrey_permit(
            committing_session,
            applicant="Tyrrell Projects Inc 19949 56 Ave Surrey, British Columbia",
        )
    committing_session.commit()

    from scripts.run_surrey_resolution_audit import run_audit

    try:
        engine_a = _engine_for(local_database_url)
        first = run_audit(engine_a, artifact_path=None, sample_size=2)
        engine_a.dispose()

        engine_b = _engine_for(local_database_url)
        second = run_audit(engine_b, artifact_path=None, sample_size=2)
        engine_b.dispose()
    finally:
        committing_session.execute(
            text("DELETE FROM permits WHERE applicant LIKE 'Tyrrell Projects Inc%'")
        )
        committing_session.execute(
            text("DELETE FROM companies WHERE name = 'Tyrrell Projects Inc'")
        )
        committing_session.commit()

    assert first["examined_count"] == 2
    assert first["examined_ids_digest"] == second["examined_ids_digest"]


def test_artifact_contains_no_raw_identifiers_names_addresses_or_error_text(
    committing_session, local_database_url, tmp_path
):
    company = _make_company(committing_session, name="Secretcorp Holdings Ltd")
    permit = _make_surrey_permit(
        committing_session,
        address="99999 Very Unique Street Name Way",
        applicant="Secretcorp Holdings Ltd 19949 56 Ave Surrey, British Columbia",
    )
    committing_session.commit()

    from scripts.run_surrey_resolution_audit import run_audit

    try:
        engine = _engine_for(local_database_url)
        artifact = run_audit(engine, artifact_path=None, sample_size=None)
        engine.dispose()
        assert artifact["examined_count"] >= 1
    finally:
        committing_session.execute(
            text("DELETE FROM permits WHERE id = :id"), {"id": permit.id}
        )
        committing_session.execute(
            text("DELETE FROM companies WHERE id = :id"), {"id": company.id}
        )
        committing_session.commit()

    blob = json.dumps(artifact)
    assert "Secretcorp" not in blob
    assert "99999 Very Unique Street Name Way" not in blob
    assert "applicant" not in artifact
    assert "company_id" not in artifact
    allowed_top_level_keys = {
        "artifact_schema_version",
        "git_commit_sha",
        "source",
        "generated_at",
        "transaction_mode",
        "sample_size",
        "counts",
        "match_method_histogram",
        "confidence_tier_histogram",
        "error_counts",
        "examined_count",
        "examined_ids_digest",
    }
    assert set(artifact.keys()) == allowed_top_level_keys


# ===================================================================
# 5. CLI-level -- no DB
# ===================================================================

_SCRIPT_FILE = Path("scripts/run_surrey_resolution_audit.py")
_CLASSIFICATION_FILE = Path("scripts/CLASSIFICATION.md")


def test_cli_rejects_apply_flag_as_unrecognized():
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, str(_SCRIPT_FILE), "--apply"],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "unrecognized" in result.stderr.lower()


def test_cli_rejects_allow_production_flag_as_unrecognized():
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, str(_SCRIPT_FILE), "--allow-production"],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "unrecognized" in result.stderr.lower()


def test_module_issues_read_only_isolation_statement_literal():
    source = _SCRIPT_FILE.read_text(encoding="utf-8")
    assert "SET TRANSACTION ISOLATION LEVEL" in source
    assert "READ ONLY" in source


def test_classification_md_documents_the_new_script():
    classification = _CLASSIFICATION_FILE.read_text(encoding="utf-8")
    assert "run_surrey_resolution_audit.py" in classification


# ===================================================================
# 6. Fail-closed hardening -- error-stage invariants, dangling
#    forced/alias targets, sample_size validation, stable histograms.
# ===================================================================


def test_forced_resolution_exception_still_yields_a_valid_artifact(db_session):
    """A resolution-stage exception on one row must not crash the whole
    audit, must not leak the raw exception message, and must be counted
    as a resolution_error (not a normalization_error), keeping every
    invariant intact."""
    _make_company(db_session, name="Tyrrell Projects Inc")
    _make_surrey_permit(
        db_session,
        applicant="Tyrrell Projects Inc 19949 56 Ave Surrey, British Columbia",
    )

    secret_message = "leak-check-marker-should-never-appear-in-artifact"
    with patch.object(CompanyIndex, "match", side_effect=RuntimeError(secret_message)):
        report = audit_surrey_permit_resolution(db_session)

    counts = report["counts"]
    assert counts["normalized_safe"] == 1
    assert counts["resolution_errors"] == 1
    assert counts["normalization_errors"] == 0
    assert counts["errors"] == 1
    assert counts["matched_existing_company"] == 0
    assert report["error_counts"] == {"resolution:RuntimeError": 1}
    assert secret_message not in json.dumps(report)


def test_forced_normalization_exception_still_yields_a_valid_artifact(db_session):
    _make_surrey_permit(db_session, applicant="Anything")

    secret_message = "another-leak-check-marker"
    with patch(
        "pipeline.surrey_resolution_audit.normalize_surrey_applicant",
        side_effect=RuntimeError(secret_message),
    ):
        report = audit_surrey_permit_resolution(db_session)

    counts = report["counts"]
    assert counts["normalization_errors"] == 1
    assert counts["resolution_errors"] == 0
    assert counts["errors"] == 1
    assert report["error_counts"] == {"normalization:RuntimeError": 1}
    assert secret_message not in json.dumps(report)


def test_company_index_missing_forced_target_does_not_fabricate_a_match(
    db_session, monkeypatch
):
    """A FORCED_CANONICAL_IDS_BY_KEY entry whose target id was never
    actually loaded (deleted, or never existed) must never be honoured."""
    parsed = resolve_company_name("Missingtarget Applicant Ltd")
    assert parsed is not None
    monkeypatch.setitem(FORCED_CANONICAL_IDS_BY_KEY, parsed.canonical_key, 999_999_999)

    index = CompanyIndex(db_session)
    outcome, method = index.match("Missingtarget Applicant Ltd")

    assert outcome == "unmatched"
    assert method is None


def test_company_index_dangling_alias_target_does_not_fabricate_a_match():
    """An alias Company whose canonical_company_id points at a row that
    is not among the loaded Company rows (a dangling reference -- the
    real ``companies.canonical_company_id`` foreign key means this can
    never be created via a normal insert, but must still be handled
    fail-closed if it were ever produced by a raw-SQL intervention,
    restore, or future schema change) must not collapse into a phantom
    matched identity via that dangling target -- it may still resolve via
    its own identity as a real, loaded row. Uses fabricated in-memory rows
    (no DB) so the dangling state can actually be constructed."""
    alias_row = SimpleNamespace(
        id=501,
        name="Dangling Alias Co",
        display_name="",
        canonical_vendor_name="",
        entity_role=ENTITY_ROLE_APPLICANT_ALIAS,
        canonical_company_id=999_999_999,
    )
    session = MagicMock()
    session.scalars.return_value.all.return_value = [alias_row]

    index = CompanyIndex(session)
    outcome, method = index.match("Dangling Alias Co")

    assert outcome == "matched"
    assert method == "direct_key"


class _SessionSpy:
    """Raises on any attribute access -- proves a caller never touched the
    session before validation completed."""

    def __getattr__(self, name):
        raise AssertionError(f"session.{name} was accessed before validation")


@pytest.mark.parametrize("invalid_sample_size", [True, False, 1.5, "3", -1, -100])
def test_invalid_sample_size_rejected_before_any_session_access(invalid_sample_size):
    with pytest.raises(SurreyResolutionAuditError):
        audit_surrey_permit_resolution(_SessionSpy(), sample_size=invalid_sample_size)


@pytest.mark.parametrize("valid_sample_size", [None, 0, 1, 100])
def test_valid_sample_size_passes_validation(db_session, valid_sample_size):
    # Should not raise SurreyResolutionAuditError for validation reasons;
    # reaching real session access proves validation accepted the value.
    report = audit_surrey_permit_resolution(db_session, sample_size=valid_sample_size)
    assert isinstance(report["counts"]["total"], int)


def test_match_method_and_confidence_tier_histograms_are_zero_initialized(
    db_session,
):
    """Every fixed bucket key is always present, even at zero, so a
    consumer never has to guess whether an absent key means zero or means
    the field doesn't exist in this schema version."""
    report = audit_surrey_permit_resolution(db_session, sample_size=0)

    assert report["match_method_histogram"] == {
        "direct_key": 0,
        "alias_collapsed": 0,
        "forced_override": 0,
    }
    assert report["confidence_tier_histogram"] == {"high": 0, "medium": 0}


def test_unrecognized_normalization_status_is_a_typed_contract_violation(db_session):
    """An unrecognized normalize_surrey_applicant status is a contract
    violation in this module's own assumptions -- it must raise
    SurreyResolutionAuditError and interrupt the whole audit, never be
    silently folded into normalized_safe or counted as an ordinary
    per-row normalization_error."""
    _make_surrey_permit(db_session, applicant="Anything At All")
    fabricated = SurreyApplicantNormalization(
        raw="Anything At All",
        organization="Anything At All",
        status="bogus_unknown_status",
    )

    with patch(
        "pipeline.surrey_resolution_audit.normalize_surrey_applicant",
        return_value=fabricated,
    ):
        with pytest.raises(SurreyResolutionAuditError):
            audit_surrey_permit_resolution(db_session)


def test_non_identifier_error_type_name_is_sanitized_to_unclassified(db_session):
    """A dynamically-constructed exception class whose __name__ is not a
    valid Python identifier (and could carry free text / a secret) must
    never appear verbatim in error_counts or anywhere in the artifact --
    it is replaced with the fixed UNCLASSIFIED_ERROR_TYPE value."""
    _make_company(db_session, name="Tyrrell Projects Inc")
    _make_surrey_permit(
        db_session,
        applicant="Tyrrell Projects Inc 19949 56 Ave Surrey, British Columbia",
    )

    secret_class_name = "Leaked Secret Class Name !! $3cr3t"
    DynamicExc = type(secret_class_name, (RuntimeError,), {})

    with patch.object(CompanyIndex, "match", side_effect=DynamicExc("boom")):
        report = audit_surrey_permit_resolution(db_session)

    assert report["error_counts"] == {f"resolution:{UNCLASSIFIED_ERROR_TYPE}": 1}
    blob = json.dumps(report)
    assert secret_class_name not in blob
    assert "Leaked" not in blob
    assert "$3cr3t" not in blob
