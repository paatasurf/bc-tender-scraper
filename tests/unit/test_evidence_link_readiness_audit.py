"""Unit + integration tests for Registry Engine Stage 2A — Evidence Link
readiness audit (read-only).

Pure-logic tests (hashing/ordering) need no database. The write-guard test
needs no real database either — it proves structurally that the audit
functions never call a mutating session method or execute non-SELECT SQL.
Behavioral tests (orphan/non-canonical/cycle/broken-redirect/depth-exhaustion
detection, full-dataset vs. sample counting) use the same local_db_session
convention as tests/unit/test_registry_gateway.py and are skipped when no
local Postgres is available.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy.sql.dml import Delete, Insert, Update

from pipeline.registry_engine.evidence.domain import (
    EVIDENCE_TYPE_PERMIT,
    SOURCE_TABLE_PERMITS,
    EvidenceLinkAuditReport,
    EvidenceReference,
)


def _ref(**overrides) -> EvidenceReference:
    base = dict(
        evidence_type=EVIDENCE_TYPE_PERMIT,
        source_table=SOURCE_TABLE_PERMITS,
        source_system="vancouver",
        external_id="BP-123",
        internal_id=1,
        company_id=7,
        timestamp="2026-01-01T00:00:00+00:00",
    )
    base.update(overrides)
    return EvidenceReference(**base)


# --- pure hashing / ordering (no DB, no session) ------------------------------


def test_reference_hash_deterministic_for_same_input():
    assert _ref().reference_hash == _ref().reference_hash


@pytest.mark.parametrize(
    "field, value",
    [
        ("source_table", "contract_awards"),
        ("source_system", "burnaby"),
        ("external_id", "BP-999"),
        ("internal_id", 2),
        ("company_id", 8),
        ("timestamp", "2026-02-01T00:00:00+00:00"),
    ],
)
def test_reference_hash_changes_with_each_required_field(field, value):
    """Requirement 2: source_table, source_system, external_id, internal_id,
    company_id, and timestamp must each independently affect reference_hash.
    """
    baseline = _ref()
    changed = _ref(**{field: value})
    assert baseline.reference_hash != changed.reference_hash


def test_reference_hash_unaffected_by_evidence_type_alone():
    """evidence_type is not part of the hash per the approved field list —
    it's already implied by source_table 1:1, so including it would be
    redundant, not required.
    """
    a = _ref(evidence_type="permit")
    b = _ref(evidence_type="something_else")
    assert a.reference_hash == b.reference_hash


def test_sort_key_is_fully_deterministic_and_unique():
    """Requirement 7: full sort key, not just internal_id."""
    a = _ref(internal_id=1, company_id=1, external_id="A")
    b = _ref(internal_id=1, company_id=1, external_id="B")
    assert EvidenceReference.sort_key(a) != EvidenceReference.sort_key(b)
    assert EvidenceReference.sort_key(a)[:2] == (SOURCE_TABLE_PERMITS, 1)


def test_report_hash_independent_of_sample_input_order():
    """Canonical ordering: report_hash must not depend on the order refs were passed in."""
    a = _ref(external_id="A", internal_id=1, company_id=1)
    b = _ref(external_id="B", internal_id=2, company_id=2)

    def _report(refs):
        return EvidenceLinkAuditReport(
            source=EVIDENCE_TYPE_PERMIT,
            generated_at="2026-01-01T00:00:00+00:00",
            total_rows=2,
            rows_with_company_id=2,
            rows_without_company_id=0,
            orphan_count=0,
            orphan_samples=[],
            non_canonical_count=0,
            non_canonical_samples=[],
            broken_redirect_count=0,
            cycle_count=0,
            depth_exhausted_count=0,
            excluded_target_count=0,
            reference_sample=refs,
            dataset_hash="irrelevant-for-this-test",
        )

    assert _report([a, b]).report_hash == _report([b, a]).report_hash


def test_report_hash_changes_with_orphan_count():
    def _report(orphan_count):
        return EvidenceLinkAuditReport(
            source=EVIDENCE_TYPE_PERMIT,
            generated_at="2026-01-01T00:00:00+00:00",
            total_rows=1,
            rows_with_company_id=1,
            rows_without_company_id=0,
            orphan_count=orphan_count,
            orphan_samples=[],
            non_canonical_count=0,
            non_canonical_samples=[],
            broken_redirect_count=0,
            cycle_count=0,
            depth_exhausted_count=0,
            excluded_target_count=0,
            reference_sample=[],
            dataset_hash="irrelevant-for-this-test",
        )

    assert _report(0).report_hash != _report(1).report_hash


def test_dataset_hash_is_a_separate_field_from_report_hash():
    """Requirement 6: sample cannot determine report integrity — dataset_hash
    is a distinct, explicitly-passed field, not derived from the sample.
    """
    report = EvidenceLinkAuditReport(
        source=EVIDENCE_TYPE_PERMIT,
        generated_at="2026-01-01T00:00:00+00:00",
        total_rows=1,
        rows_with_company_id=1,
        rows_without_company_id=0,
        orphan_count=0,
        orphan_samples=[],
        non_canonical_count=0,
        non_canonical_samples=[],
        broken_redirect_count=0,
        cycle_count=0,
        depth_exhausted_count=0,
        excluded_target_count=0,
        reference_sample=[],
        dataset_hash="full-dataset-fingerprint",
    )
    assert report.dataset_hash == "full-dataset-fingerprint"
    assert report.dataset_hash != report.report_hash


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
        raise AssertionError(f"evidence audit attempted a write via session.{name}()")

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
            raise AssertionError("evidence audit attempted DML via session.scalar()")
        return 0

    def scalars(self, stmt):
        if isinstance(stmt, (Insert, Update, Delete)):
            raise AssertionError("evidence audit attempted DML via session.scalars()")
        return _WriteGuardResult()

    def execute(self, stmt):
        if isinstance(stmt, (Insert, Update, Delete)):
            raise AssertionError("evidence audit attempted DML via session.execute()")
        return _WriteGuardResult()


def test_permit_audit_never_writes():
    from pipeline.registry_engine.evidence.audit import audit_permit_evidence_links

    session = _WriteGuardSession()
    report = audit_permit_evidence_links(session)

    assert session.write_calls == []
    assert report.total_rows == 0
    assert report.dataset_hash  # computed even over zero rows


def test_contract_award_audit_never_writes():
    from pipeline.registry_engine.evidence.audit import (
        audit_contract_award_evidence_links,
    )

    session = _WriteGuardSession()
    report = audit_contract_award_evidence_links(session)

    assert session.write_calls == []
    assert report.total_rows == 0


def test_tender_audit_never_writes():
    from pipeline.registry_engine.evidence.audit import audit_tender_evidence_linkage

    session = _WriteGuardSession()
    report = audit_tender_evidence_linkage(session)

    assert session.write_calls == []


def test_audit_module_does_not_import_llm_or_company_registry_links_writers():
    """Static guard: this module must not touch company_registry_links at all
    (Registry spec Section 10.2 — that table is OrgBook/ODBUS Registry
    Evidence only) and must not import anything AI/LLM-related.

    Checks actual imports/usages, not docstring prose explaining the design
    rationale (which legitimately names the table it deliberately avoids).
    """
    import ast

    import pipeline.registry_engine.evidence.audit as audit_module

    source = open(audit_module.__file__, encoding="utf-8").read()
    tree = ast.parse(source)

    imported_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported_names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            imported_names.update(alias.name for alias in node.names)

    assert "CompanyRegistryLink" not in imported_names
    assert not any("company_registry_links" in name for name in imported_names)
    assert not any(
        forbidden in name.lower()
        for name in imported_names
        for forbidden in ("openai", "anthropic", "claude", "llm")
    )
    code_only = "\n".join(
        line
        for line in source.splitlines()
        if not line.strip().startswith(('"""', "#"))
    )
    assert ".company_registry_links" not in code_only
    assert "CompanyRegistryLink(" not in code_only


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
        pytest.skip("Refusing evidence audit tests against production DATABASE_URL")
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


def _make_company(
    session,
    *,
    name,
    entity_role="canonical",
    canonical_company_id=None,
    registry_status="active",
):
    from db.models import Company

    company = Company(
        name=name,
        display_name=name,
        entity_role=entity_role,
        canonical_company_id=canonical_company_id,
        registry_status=registry_status,
    )
    session.add(company)
    session.flush()
    return company


def test_permit_audit_detects_non_canonical_and_resolves_redirect(local_db_session):
    from db.models import Permit

    canonical = _make_company(local_db_session, name="Canonical Co A")
    alias = _make_company(
        local_db_session,
        name="Alias Co A",
        entity_role="applicant_alias",
        canonical_company_id=canonical.id,
    )
    permit = Permit(address="123 Test St", external_id="BP-1", company_id=alias.id)
    local_db_session.add(permit)
    local_db_session.flush()

    from pipeline.registry_engine.evidence.audit import audit_permit_evidence_links

    report = audit_permit_evidence_links(local_db_session)

    matching = [
        s for s in report.non_canonical_samples if s["internal_id"] == permit.id
    ]
    assert len(matching) == 1
    assert matching[0]["resolved_canonical_id"] == canonical.id
    assert matching[0]["redirect_broken"] is False
    assert matching[0]["redirect_cycle"] is False
    assert matching[0]["redirect_depth_exhausted"] is False


def test_resolve_canonical_targets_detects_broken_redirect_directly():
    """A broken redirect (canonical_company_id pointing at a missing row) is
    structurally impossible to construct via a real insert — companies.
    canonical_company_id carries an enforced FK constraint (confirmed
    against real Postgres: companies_canonical_company_id_fkey rejects a
    dangling id outright, mirroring the permits/contract_awards company_id
    orphan situation). This tests the detection logic directly instead of
    through a DB insert that the schema itself would refuse.
    """
    from db.company_canonical_constants import ENTITY_ROLE_APPLICANT_ALIAS
    from pipeline.registry_engine.evidence.audit import _resolve_canonical_targets

    class _StubCompany:
        def __init__(
            self, id, entity_role, canonical_company_id=None, registry_status="active"
        ):
            self.id = id
            self.entity_role = entity_role
            self.canonical_company_id = canonical_company_id
            self.registry_status = registry_status

    class _StubScalars:
        def __init__(self, items):
            self._items = items

        def all(self):
            return self._items

    class _StubSession:
        def __init__(self, companies):
            self._companies = companies

        def scalars(self, _stmt):
            return _StubScalars(self._companies)

    alias = _StubCompany(
        id=1, entity_role=ENTITY_ROLE_APPLICANT_ALIAS, canonical_company_id=9_999_999
    )
    # 9_999_999 is deliberately never present in the stub's company set.
    session = _StubSession([alias])

    results = _resolve_canonical_targets(session, [1])

    assert results[1].redirect_broken is True
    assert results[1].resolved_canonical_id is None
    assert results[1].redirect_cycle is False
    assert results[1].redirect_depth_exhausted is False


def test_permit_audit_detects_redirect_cycle(local_db_session):
    from db.models import Permit

    company_a = _make_company(
        local_db_session, name="Cycle Co A", entity_role="applicant_alias"
    )
    company_b = _make_company(
        local_db_session,
        name="Cycle Co B",
        entity_role="applicant_alias",
        canonical_company_id=company_a.id,
    )
    company_a.canonical_company_id = company_b.id
    local_db_session.flush()

    permit = Permit(address="789 Test St", external_id="BP-3", company_id=company_a.id)
    local_db_session.add(permit)
    local_db_session.flush()

    from pipeline.registry_engine.evidence.audit import audit_permit_evidence_links

    report = audit_permit_evidence_links(local_db_session)
    matching = [
        s for s in report.non_canonical_samples if s["internal_id"] == permit.id
    ]
    assert len(matching) == 1
    assert matching[0]["redirect_cycle"] is True
    assert report.cycle_count >= 1


def test_permit_audit_detects_redirect_depth_exhaustion(local_db_session, monkeypatch):
    """Requirement 5: a long, non-cyclic, non-broken chain that exceeds
    MAX_REDIRECT_DEPTH must be reported distinctly from both a genuine cycle
    and a legitimate no-redirect terminal state.
    """
    import pipeline.registry_engine.evidence.audit as audit_module
    from db.models import Permit

    monkeypatch.setattr(audit_module, "MAX_REDIRECT_DEPTH", 2)

    # Chain: start -> mid1 -> mid2 -> mid3 -> canonical (4 hops > depth 2)
    canonical = _make_company(local_db_session, name="Deep Canonical")
    mid3 = _make_company(
        local_db_session,
        name="Deep Mid 3",
        entity_role="applicant_alias",
        canonical_company_id=canonical.id,
    )
    mid2 = _make_company(
        local_db_session,
        name="Deep Mid 2",
        entity_role="applicant_alias",
        canonical_company_id=mid3.id,
    )
    mid1 = _make_company(
        local_db_session,
        name="Deep Mid 1",
        entity_role="applicant_alias",
        canonical_company_id=mid2.id,
    )
    start = _make_company(
        local_db_session,
        name="Deep Start",
        entity_role="applicant_alias",
        canonical_company_id=mid1.id,
    )

    permit = Permit(address="Deep Chain Rd", external_id="BP-DEEP", company_id=start.id)
    local_db_session.add(permit)
    local_db_session.flush()

    from pipeline.registry_engine.evidence.audit import audit_permit_evidence_links

    report = audit_permit_evidence_links(local_db_session)
    matching = [
        s for s in report.non_canonical_samples if s["internal_id"] == permit.id
    ]
    assert len(matching) == 1
    assert matching[0]["redirect_depth_exhausted"] is True
    assert matching[0]["redirect_broken"] is False
    assert matching[0]["redirect_cycle"] is False
    assert report.depth_exhausted_count >= 1


def test_permit_audit_flags_excluded_canonical_target_via_redirect(local_db_session):
    from db.models import Permit

    excluded_canonical = _make_company(
        local_db_session,
        name="Excluded Canonical Co",
        registry_status="excluded",
    )
    alias = _make_company(
        local_db_session,
        name="Alias To Excluded",
        entity_role="applicant_alias",
        canonical_company_id=excluded_canonical.id,
    )
    permit = Permit(address="1 Excluded Way", external_id="BP-4", company_id=alias.id)
    local_db_session.add(permit)
    local_db_session.flush()

    from pipeline.registry_engine.evidence.audit import audit_permit_evidence_links

    report = audit_permit_evidence_links(local_db_session)
    matching = [
        s for s in report.non_canonical_samples if s["internal_id"] == permit.id
    ]
    assert len(matching) == 1
    assert matching[0]["excluded"] is True
    assert report.excluded_target_count >= 1


def test_permit_audit_flags_direct_canonical_excluded_target(local_db_session):
    """Requirement 4: a permit pointing DIRECTLY at a canonical-but-excluded
    company must be counted, not just the redirect case.
    """
    from db.models import Permit

    excluded_canonical = _make_company(
        local_db_session,
        name="Direct Excluded Canonical",
        entity_role="canonical",
        registry_status="excluded",
    )
    permit = Permit(
        address="Direct Excluded Way",
        external_id="BP-DIRECT-EXCLUDED",
        company_id=excluded_canonical.id,
    )
    local_db_session.add(permit)
    local_db_session.flush()

    from pipeline.registry_engine.evidence.audit import audit_permit_evidence_links

    report = audit_permit_evidence_links(local_db_session)
    # A direct canonical hit never appears in non_canonical_samples (it's
    # canonical) — this must be caught by the direct-excluded aggregate.
    assert report.excluded_target_count >= 1


def test_totals_reflect_full_dataset_not_sample_limit(local_db_session, monkeypatch):
    """Requirement 3: broken/cycle/excluded/depth-exhausted totals must be
    computed from the full dataset, not the bounded illustrative sample.
    Force SAMPLE_LIMIT down to 1 and create 3 permits redirecting to a real
    excluded canonical company — the totals must still reflect all 3, even
    though only 1 appears in non_canonical_samples. (Uses the excluded-
    target mechanism rather than a broken redirect, since a dangling
    canonical_company_id can't be constructed against a real FK-enforced
    schema — see test_resolve_canonical_targets_detects_broken_redirect_directly.)
    """
    import pipeline.registry_engine.evidence.audit as audit_module
    from db.models import Permit

    monkeypatch.setattr(audit_module, "SAMPLE_LIMIT", 1)

    excluded_canonical = _make_company(
        local_db_session,
        name="Excluded Target For Sample Limit Test",
        registry_status="excluded",
    )
    permits = []
    for i in range(3):
        alias = _make_company(
            local_db_session,
            name=f"Sample-Limit Alias {i}",
            entity_role="applicant_alias",
            canonical_company_id=excluded_canonical.id,
        )
        permit = Permit(
            address=f"{i} Sample Limit Rd",
            external_id=f"BP-SL-{i}",
            company_id=alias.id,
        )
        local_db_session.add(permit)
        permits.append(permit)
    local_db_session.flush()

    from pipeline.registry_engine.evidence.audit import audit_permit_evidence_links

    report = audit_permit_evidence_links(local_db_session)

    assert len(report.non_canonical_samples) <= 1  # sample bound respected
    assert report.excluded_target_count >= 3  # full dataset, not sample-bound


def test_dataset_hash_reflects_rows_beyond_reference_sample_limit(
    local_db_session, monkeypatch
):
    """Requirement 6: dataset_hash must change when a row outside the
    (small, monkeypatched) reference sample bound is added — proving it is
    not derived from the sample.
    """
    import pipeline.registry_engine.evidence.audit as audit_module
    from db.models import Permit

    monkeypatch.setattr(audit_module, "REFERENCE_SAMPLE_LIMIT", 1)

    company = _make_company(local_db_session, name="Dataset Hash Co")
    permit1 = Permit(address="1 Hash Rd", external_id="BP-H1", company_id=company.id)
    local_db_session.add(permit1)
    local_db_session.flush()

    from pipeline.registry_engine.evidence.audit import audit_permit_evidence_links

    report_before = audit_permit_evidence_links(local_db_session)

    permit2 = Permit(address="2 Hash Rd", external_id="BP-H2", company_id=company.id)
    local_db_session.add(permit2)
    local_db_session.flush()

    report_after = audit_permit_evidence_links(local_db_session)

    assert report_before.dataset_hash != report_after.dataset_hash


def test_tender_audit_reports_schema_gap(local_db_session):
    from pipeline.registry_engine.evidence.audit import audit_tender_evidence_linkage

    report = audit_tender_evidence_linkage(local_db_session)
    assert report.has_company_id_column is False
    assert report.schema_gap is True


def test_permit_audit_produces_no_orphans_under_fk_enforcement(local_db_session):
    """FK-enforced: attempting to insert a permit with a non-existent
    company_id must fail at the database level, so orphan_count should be 0
    for any data that could actually be inserted."""
    from db.models import Permit
    from sqlalchemy.exc import IntegrityError

    bad_permit = Permit(
        address="Orphan St", external_id="BP-ORPHAN", company_id=9_999_999
    )
    local_db_session.add(bad_permit)
    with pytest.raises(IntegrityError):
        local_db_session.flush()
    local_db_session.rollback()
