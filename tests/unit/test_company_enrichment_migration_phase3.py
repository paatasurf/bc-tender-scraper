"""Fresh-Postgres migration tests for the Phase 3 (migration 035)
provenance/verification extension to db/company_enrichment_migration.py
(docs/COMPANY_CONTACT_PROVIDER_PHASE3_DESIGN.md S2.6's required test list).

Covers: clean apply, clean rollback, repeated apply/rollback (idempotency),
partial-schema detection, a wrong-shaped-but-same-named CHECK constraint,
every malformed-JSONB case from the design doc's S2.1.1 table, the three
ApplyReadinessStatus states, and the verified-evidence CHECK's protection.

Every test runs against the local, disposable, non-production Postgres
instance only -- require_local_test_database() (tests/db_test_safety.py)
calls is_production_database_url() before returning DATABASE_URL and
pytest.fails loudly if it ever resolves to production. No test in this
file applies migration 035 to any target other than that local database,
and every fixture rolls back everything it created before yielding control
back to pytest and again in its own teardown.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.orm import Session

from db.models import Company
from db.company_enrichment_ddl import (
    company_enrichment_migration_statements,
    company_enrichment_phase3_migration_statements,
    company_enrichment_phase3_rollback_statements,
    company_enrichment_rollback_statements,
)
from db.company_enrichment_migration import (
    ApplyReadinessStatus,
    CompanyEnrichmentPhase3ApplyPostconditionError,
    apply_company_enrichment_phase3_migration,
    company_enrichment_apply_readiness,
    company_enrichment_phase3_apply_readiness,
    company_enrichment_phase3_before_stats,
    company_enrichment_phase3_migration_pending,
)
from tests.db_test_safety import require_local_test_database


def _fresh_engine():
    database_url = require_local_test_database()
    engine = create_engine(database_url, connect_args={"connect_timeout": 3})
    try:
        with engine.connect() as probe:
            probe.execute(text("SELECT 1"))
    except Exception:
        pytest.skip("Local Postgres unavailable")
    return engine


def _reset_to_034_only(engine) -> None:
    """Roll back Phase 3, roll back 034, re-apply 034 -- the baseline every
    Phase 3 test starts from."""
    with engine.begin() as conn:
        for stmt in company_enrichment_phase3_rollback_statements():
            conn.execute(text(stmt))
        for stmt in company_enrichment_rollback_statements():
            conn.execute(text(stmt))
        for stmt in company_enrichment_migration_statements():
            conn.execute(text(stmt))


def _reset_to_nothing(engine) -> None:
    with engine.begin() as conn:
        for stmt in company_enrichment_phase3_rollback_statements():
            conn.execute(text(stmt))
        for stmt in company_enrichment_rollback_statements():
            conn.execute(text(stmt))


@pytest.fixture
def phase3_ready_engine():
    """Migration 034 applied, migration 035 (Phase 3) rolled back -- the
    baseline every Phase 3 test starts from and returns to."""
    engine = _fresh_engine()
    _reset_to_034_only(engine)
    try:
        yield engine
    finally:
        _reset_to_nothing(engine)
        engine.dispose()


@pytest.fixture
def clean_engine():
    """Genuinely fresh: both migrations rolled back, neither applied --
    for the full round-trip lifecycle test only."""
    engine = _fresh_engine()
    _reset_to_nothing(engine)
    try:
        yield engine
    finally:
        _reset_to_nothing(engine)
        engine.dispose()


# ---------------------------------------------------------------------------
# 1 & 7. Clean apply, and the full ordered round trip (design doc S2.6
# items 1 and 7).
# ---------------------------------------------------------------------------


def test_clean_apply_reports_fully_applied_with_all_seven_items_present(
    phase3_ready_engine,
) -> None:
    engine = phase3_ready_engine
    with engine.begin() as conn:
        for stmt in company_enrichment_phase3_migration_statements():
            conn.execute(text(stmt))

    with engine.connect() as conn:
        r = company_enrichment_phase3_apply_readiness(conn)

    assert r.status == ApplyReadinessStatus.FULLY_APPLIED, r.violations
    assert r.violations == ()
    fields_phase3 = {
        "source_url",
        "raw_value",
        "extraction_method",
        "verified_at",
        "verified_by",
        "verification_source_url",
    }
    assert fields_phase3.issubset(set(r.fields_columns))
    assert "field_attempt_log" in r.jobs_columns


def test_fresh_postgres_phase3_full_ordered_lifecycle(clean_engine) -> None:
    """The full round trip required by design doc S2.6 item 7: 034
    NOT_APPLIED -> apply 034 -> FULLY_APPLIED -> Phase 3 NOT_APPLIED ->
    apply Phase 3 -> FULLY_APPLIED -> rollback Phase 3 -> Phase 3
    NOT_APPLIED again (034 still FULLY_APPLIED throughout) -> rollback 034
    -> both NOT_APPLIED. The two migrations' readiness states must never
    interfere with each other in either direction."""
    engine = clean_engine

    with engine.connect() as conn:
        assert (
            company_enrichment_apply_readiness(conn).status
            == ApplyReadinessStatus.NOT_APPLIED
        )

    with engine.begin() as conn:
        for stmt in company_enrichment_migration_statements():
            conn.execute(text(stmt))

    with engine.connect() as conn:
        assert (
            company_enrichment_apply_readiness(conn).status
            == ApplyReadinessStatus.FULLY_APPLIED
        )
        r = company_enrichment_phase3_apply_readiness(conn)
        assert r.status == ApplyReadinessStatus.NOT_APPLIED, r.violations

    with engine.begin() as conn:
        for stmt in company_enrichment_phase3_migration_statements():
            conn.execute(text(stmt))

    with engine.connect() as conn:
        assert (
            company_enrichment_phase3_apply_readiness(conn).status
            == ApplyReadinessStatus.FULLY_APPLIED
        )
        # 034's own readiness must ALSO still report FULLY_APPLIED now
        # that Phase 3's columns exist on top of it (design doc S2.5's own
        # named requirement -- the "unexpected columns" misreport bug).
        assert (
            company_enrichment_apply_readiness(conn).status
            == ApplyReadinessStatus.FULLY_APPLIED
        )

    with engine.begin() as conn:
        for stmt in company_enrichment_phase3_rollback_statements():
            conn.execute(text(stmt))

    with engine.connect() as conn:
        assert (
            company_enrichment_phase3_apply_readiness(conn).status
            == ApplyReadinessStatus.NOT_APPLIED
        )
        assert (
            company_enrichment_apply_readiness(conn).status
            == ApplyReadinessStatus.FULLY_APPLIED
        )

    with engine.begin() as conn:
        for stmt in company_enrichment_rollback_statements():
            conn.execute(text(stmt))

    with engine.connect() as conn:
        assert (
            company_enrichment_apply_readiness(conn).status
            == ApplyReadinessStatus.NOT_APPLIED
        )
        assert (
            company_enrichment_phase3_apply_readiness(conn).status
            == ApplyReadinessStatus.NOT_APPLIED
        )


def test_apply_company_enrichment_phase3_migration_end_to_end(
    phase3_ready_engine,
) -> None:
    """The real apply-and-verify entry point (what the CLI script calls),
    not just the raw statement list -- confirms the transactional
    postcondition check passes and reports the expected summary shape."""
    engine = phase3_ready_engine
    result = apply_company_enrichment_phase3_migration(engine)

    assert result["migration"] == "035_company_enrichment_phase3"
    assert result["conforms"] is True
    assert result["statements_executed"] == len(
        company_enrichment_phase3_migration_statements()
    )
    assert "row_counts" in result

    with engine.connect() as conn:
        assert (
            company_enrichment_phase3_apply_readiness(conn).status
            == ApplyReadinessStatus.FULLY_APPLIED
        )


def test_apply_phase3_refuses_when_034_is_not_fully_applied(clean_engine) -> None:
    """apply_company_enrichment_phase3_migration() must refuse (not
    silently proceed or half-apply) when migration 034 itself has not
    been applied yet -- ALTER TABLE against a nonexistent table would
    otherwise be the first, confusing failure an operator sees."""
    engine = clean_engine  # neither migration applied

    with pytest.raises(
        CompanyEnrichmentPhase3ApplyPostconditionError, match="034 is not"
    ):
        apply_company_enrichment_phase3_migration(engine)

    with engine.connect() as conn:
        assert (
            company_enrichment_apply_readiness(conn).status
            == ApplyReadinessStatus.NOT_APPLIED
        )


# ---------------------------------------------------------------------------
# 2. Clean rollback.
# ---------------------------------------------------------------------------


def test_clean_rollback_removes_everything_and_leaves_034_untouched(
    phase3_ready_engine,
) -> None:
    engine = phase3_ready_engine
    with engine.begin() as conn:
        for stmt in company_enrichment_phase3_migration_statements():
            conn.execute(text(stmt))
    with engine.connect() as conn:
        assert (
            company_enrichment_phase3_apply_readiness(conn).status
            == ApplyReadinessStatus.FULLY_APPLIED
        )

    with engine.begin() as conn:
        for stmt in company_enrichment_phase3_rollback_statements():
            conn.execute(text(stmt))

    with engine.connect() as conn:
        r = company_enrichment_phase3_apply_readiness(conn)
        assert r.status == ApplyReadinessStatus.NOT_APPLIED, r.violations

        fields_cols = set(
            conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'company_enrichment_fields'"
                )
            )
            .scalars()
            .all()
        )
        jobs_cols = set(
            conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'company_enrichment_jobs'"
                )
            )
            .scalars()
            .all()
        )
        assert "source_url" not in fields_cols
        assert "verified_at" not in fields_cols
        assert "field_attempt_log" not in jobs_cols

        func_exists = conn.execute(
            text(
                "SELECT 1 FROM pg_proc WHERE proname = "
                "'company_enrichment_validate_field_attempt_log'"
            )
        ).first()
        assert func_exists is None

        # 034's own tables/columns/readiness are completely unaffected.
        assert (
            company_enrichment_apply_readiness(conn).status
            == ApplyReadinessStatus.FULLY_APPLIED
        )


# ---------------------------------------------------------------------------
# 3. Repeated apply / rollback -- idempotency.
# ---------------------------------------------------------------------------


def test_repeated_apply_is_idempotent(phase3_ready_engine) -> None:
    engine = phase3_ready_engine
    result_1 = apply_company_enrichment_phase3_migration(engine)
    result_2 = apply_company_enrichment_phase3_migration(engine)

    assert result_1["conforms"] is True
    assert result_2["conforms"] is True

    with engine.connect() as conn:
        assert (
            company_enrichment_phase3_apply_readiness(conn).status
            == ApplyReadinessStatus.FULLY_APPLIED
        )


def test_repeated_rollback_is_idempotent(phase3_ready_engine) -> None:
    engine = phase3_ready_engine
    with engine.begin() as conn:
        for stmt in company_enrichment_phase3_migration_statements():
            conn.execute(text(stmt))

    with engine.begin() as conn:
        for stmt in company_enrichment_phase3_rollback_statements():
            conn.execute(text(stmt))
    # Second run against an already-rolled-back schema -- every statement
    # uses IF EXISTS, must be a clean no-op, not an error.
    with engine.begin() as conn:
        for stmt in company_enrichment_phase3_rollback_statements():
            conn.execute(text(stmt))

    with engine.connect() as conn:
        assert (
            company_enrichment_phase3_apply_readiness(conn).status
            == ApplyReadinessStatus.NOT_APPLIED
        )


# ---------------------------------------------------------------------------
# 4. Partial schema -- must report CORRUPT, naming both the missing and
# the present piece (design doc S2.6 item 3).
# ---------------------------------------------------------------------------


def test_partial_schema_fields_only_is_corrupt(phase3_ready_engine) -> None:
    """Only company_enrichment_fields's 6 columns applied, not
    company_enrichment_jobs's field_attempt_log/function/constraint."""
    engine = phase3_ready_engine
    with engine.begin() as conn:
        conn.execute(
            text(
                "ALTER TABLE company_enrichment_fields "
                "ADD COLUMN source_url TEXT, "
                "ADD COLUMN raw_value TEXT, "
                "ADD COLUMN extraction_method VARCHAR(30), "
                "ADD COLUMN verified_at TIMESTAMPTZ, "
                "ADD COLUMN verified_by VARCHAR(100), "
                "ADD COLUMN verification_source_url TEXT"
            )
        )

    with engine.connect() as conn:
        r = company_enrichment_phase3_apply_readiness(conn)

    assert r.status == ApplyReadinessStatus.CORRUPT
    assert any(
        "jobs" in v and "field_attempt_log" in v for v in r.violations
    ), r.violations
    assert any(
        "function" in v.lower() for v in r.violations
    ), r.violations  # the function is missing too


def test_partial_schema_jobs_only_is_corrupt(phase3_ready_engine) -> None:
    """Only company_enrichment_jobs's field_attempt_log (+ function +
    constraint) applied, not company_enrichment_fields's 6 columns."""
    engine = phase3_ready_engine
    with engine.begin() as conn:
        for stmt in company_enrichment_phase3_migration_statements():
            if "company_enrichment_fields" in stmt:
                continue
            conn.execute(text(stmt))

    with engine.connect() as conn:
        r = company_enrichment_phase3_apply_readiness(conn)

    assert r.status == ApplyReadinessStatus.CORRUPT
    assert any("fields" in v and "source_url" in v for v in r.violations), r.violations
    # The jobs-side increment IS fully present and correctly shaped.
    assert not any("jobs" in v and "missing" in v for v in r.violations), r.violations


# ---------------------------------------------------------------------------
# 5. Wrong-shaped-but-same-named constraint -- the DO $$ guard's documented
# blind spot; the readiness check, not the guard, must catch it (design
# doc S2.6 item 4).
# ---------------------------------------------------------------------------


def test_wrong_shaped_verified_evidence_constraint_is_detected_as_corrupt(
    phase3_ready_engine,
) -> None:
    engine = phase3_ready_engine
    with engine.begin() as conn:
        conn.execute(
            text(
                "ALTER TABLE company_enrichment_fields "
                "ADD COLUMN source_url TEXT, "
                "ADD COLUMN raw_value TEXT, "
                "ADD COLUMN extraction_method VARCHAR(30), "
                "ADD COLUMN verified_at TIMESTAMPTZ, "
                "ADD COLUMN verified_by VARCHAR(100), "
                "ADD COLUMN verification_source_url TEXT"
            )
        )
        # Deliberately weaker than the real constraint, same name.
        conn.execute(
            text(
                "ALTER TABLE company_enrichment_fields "
                "ADD CONSTRAINT ck_company_enrichment_fields_verified_evidence "
                "CHECK (verified_by IS NOT NULL OR NOT verified)"
            )
        )

    with engine.connect() as conn:
        r = company_enrichment_phase3_apply_readiness(conn)

    assert r.status == ApplyReadinessStatus.CORRUPT
    assert any(
        "ck_company_enrichment_fields_verified_evidence" in v
        and "does not conform" in v
        for v in r.violations
    ), r.violations


def test_apply_refuses_and_rolls_back_when_a_wrong_shaped_constraint_already_exists(
    phase3_ready_engine,
) -> None:
    """The definitive safety property: apply must never silently succeed
    when a same-named-but-wrong-shaped constraint already exists (the
    DO $$ guard only checks the name) -- must fail EXPLICITLY and roll
    back cleanly, leaving nothing half-committed."""
    engine = phase3_ready_engine
    with engine.begin() as conn:
        conn.execute(
            text(
                "ALTER TABLE company_enrichment_fields "
                "ADD COLUMN source_url TEXT, "
                "ADD COLUMN raw_value TEXT, "
                "ADD COLUMN extraction_method VARCHAR(30), "
                "ADD COLUMN verified_at TIMESTAMPTZ, "
                "ADD COLUMN verified_by VARCHAR(100), "
                "ADD COLUMN verification_source_url TEXT"
            )
        )
        conn.execute(
            text(
                "ALTER TABLE company_enrichment_fields "
                "ADD CONSTRAINT ck_company_enrichment_fields_verified_evidence "
                "CHECK (verified_by IS NOT NULL OR NOT verified)"
            )
        )

    with pytest.raises(
        CompanyEnrichmentPhase3ApplyPostconditionError, match="does not fully conform"
    ):
        apply_company_enrichment_phase3_migration(engine)

    with engine.connect() as conn:
        # Nothing else Phase 3 would have added was left half-committed.
        jobs_has_log = conn.execute(
            text(
                "SELECT 1 FROM information_schema.columns WHERE "
                "table_name = 'company_enrichment_jobs' AND "
                "column_name = 'field_attempt_log'"
            )
        ).first()
        assert jobs_has_log is None

        # The pre-existing wrong constraint is untouched -- fail-explicit,
        # not auto-repaired.
        actual_def = conn.execute(
            text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE "
                "conname = 'ck_company_enrichment_fields_verified_evidence'"
            )
        ).scalar_one()
        assert "verification_source_url" not in actual_def


# ---------------------------------------------------------------------------
# 6. Malformed field_attempt_log JSONB -- every case from design doc
# S2.1.1's table, each its own named test (design doc S2.6 item 6).
# ---------------------------------------------------------------------------


@pytest.fixture
def phase3_applied_engine(phase3_ready_engine):
    """034 + Phase 3 both fully applied -- the baseline for JSONB
    validation and verified-evidence tests."""
    engine = phase3_ready_engine
    with engine.begin() as conn:
        for stmt in company_enrichment_phase3_migration_statements():
            conn.execute(text(stmt))
    return engine


def _create_company(conn, name: str) -> int:
    """Create a company through the ORM (matching Company's Python-side
    column defaults, which a hand-written raw INSERT would bypass and
    trip a NOT NULL violation on) -- bound to the CALLER's own connection
    and transaction, via flush() not commit(), so the caller's own
    rollback() undoes it along with everything else."""
    session = Session(bind=conn)
    company = Company(name=name)
    session.add(company)
    session.flush()
    company_id = company.id
    session.expunge(company)
    return company_id


def _insert_job(conn, *, field_attempt_log) -> None:
    """Insert a minimal, otherwise-valid company_enrichment_jobs row with
    the given field_attempt_log value -- a real company row is needed for
    the FK, created and cleaned up by the caller's own transaction."""
    company_id = _create_company(conn, "Phase3 JSONB Test Co")
    conn.execute(
        text(
            "INSERT INTO company_enrichment_jobs "
            "(run_id, company_id, trigger, status, lease_expires_at, field_attempt_log) "
            "VALUES (:run_id, :company_id, 'manual', 'success', NOW() + INTERVAL '1 hour', "
            ":log)"
        ),
        {
            "run_id": "11111111-1111-1111-1111-111111111111",
            "company_id": company_id,
            "log": (
                field_attempt_log
                if isinstance(field_attempt_log, str)
                else json.dumps(field_attempt_log)
            ),
        },
    )


def _valid_entry(**overrides) -> dict:
    entry = {
        "field": "phone",
        "status": "no_match",
        "reason": None,
        "provider": "website_searxng",
        "attempted_at": "2026-08-31T20:00:00+00:00",
    }
    entry.update(overrides)
    return entry


def test_jsonb_accepts_empty_array(phase3_applied_engine) -> None:
    engine = phase3_applied_engine
    with engine.begin() as conn:
        _insert_job(conn, field_attempt_log=[])
        conn.rollback()


def test_jsonb_accepts_one_well_formed_entry(phase3_applied_engine) -> None:
    engine = phase3_applied_engine
    with engine.begin() as conn:
        _insert_job(conn, field_attempt_log=[_valid_entry()])
        conn.rollback()


def test_jsonb_accepts_a_fetch_error_entry_with_a_reason(phase3_applied_engine) -> None:
    engine = phase3_applied_engine
    with engine.begin() as conn:
        _insert_job(
            conn,
            field_attempt_log=[
                _valid_entry(status="fetch_error", reason="robots_disallowed")
            ],
        )
        conn.rollback()


def test_jsonb_accepts_exactly_twenty_entries_boundary(phase3_applied_engine) -> None:
    engine = phase3_applied_engine
    entries = [_valid_entry(field=f"field_{i}") for i in range(20)]
    with engine.begin() as conn:
        _insert_job(conn, field_attempt_log=entries)
        conn.rollback()


def test_jsonb_rejects_twenty_one_entries(phase3_applied_engine) -> None:
    engine = phase3_applied_engine
    entries = [_valid_entry(field=f"field_{i}") for i in range(21)]
    with engine.begin() as conn:
        with pytest.raises(IntegrityError, match="ck_company_enrichment_jobs"):
            _insert_job(conn, field_attempt_log=entries)
        conn.rollback()


def test_jsonb_rejects_a_bare_object_instead_of_an_array(
    phase3_applied_engine,
) -> None:
    engine = phase3_applied_engine
    with engine.begin() as conn:
        with pytest.raises(IntegrityError, match="ck_company_enrichment_jobs"):
            _insert_job(conn, field_attempt_log=_valid_entry())
        conn.rollback()


def test_jsonb_rejects_an_array_of_scalars_not_objects(
    phase3_applied_engine,
) -> None:
    engine = phase3_applied_engine
    with engine.begin() as conn:
        with pytest.raises(IntegrityError, match="ck_company_enrichment_jobs"):
            _insert_job(conn, field_attempt_log=["not", "an", "object"])
        conn.rollback()


def test_jsonb_rejects_an_entry_missing_attempted_at(phase3_applied_engine) -> None:
    engine = phase3_applied_engine
    bad_entry = _valid_entry()
    del bad_entry["attempted_at"]
    with engine.begin() as conn:
        with pytest.raises(IntegrityError, match="ck_company_enrichment_jobs"):
            _insert_job(conn, field_attempt_log=[bad_entry])
        conn.rollback()


def test_jsonb_rejects_a_status_outside_the_three_value_enum(
    phase3_applied_engine,
) -> None:
    engine = phase3_applied_engine
    with engine.begin() as conn:
        with pytest.raises(IntegrityError, match="ck_company_enrichment_jobs"):
            _insert_job(
                conn, field_attempt_log=[_valid_entry(status="unverified_candidate")]
            )
        conn.rollback()


def test_jsonb_rejects_a_field_name_over_fifty_chars(phase3_applied_engine) -> None:
    engine = phase3_applied_engine
    with engine.begin() as conn:
        with pytest.raises(IntegrityError, match="ck_company_enrichment_jobs"):
            _insert_job(conn, field_attempt_log=[_valid_entry(field="x" * 51)])
        conn.rollback()


def test_jsonb_rejects_a_reason_over_two_hundred_chars(phase3_applied_engine) -> None:
    engine = phase3_applied_engine
    with engine.begin() as conn:
        with pytest.raises(IntegrityError, match="ck_company_enrichment_jobs"):
            _insert_job(
                conn,
                field_attempt_log=[
                    _valid_entry(status="fetch_error", reason="x" * 201)
                ],
            )
        conn.rollback()


def test_jsonb_rejects_syntactically_invalid_json(phase3_applied_engine) -> None:
    """Fails at the jsonb type-parse level, before the CHECK constraint is
    even evaluated -- a different exception shape than the CHECK-violation
    cases above (design doc S2.1.1's own documented distinction)."""
    engine = phase3_applied_engine
    with engine.begin() as conn:
        with pytest.raises(DataError):
            _insert_job(conn, field_attempt_log="{not valid json at all")
        conn.rollback()


def test_jsonb_storage_does_not_truncate_a_long_valid_reason(
    phase3_applied_engine,
) -> None:
    """No-truncation proof (design doc S2.6/S7.2): a reason at exactly the
    200-char boundary survives byte-for-byte, proving JSONB storage itself
    (not just the validation function's own bound) never silently cuts
    off a value -- the defect the VARCHAR(80)[] design this replaces
    actually had."""
    engine = phase3_applied_engine
    long_reason = "x" * 200
    with engine.begin() as conn:
        _insert_job(
            conn,
            field_attempt_log=[_valid_entry(status="fetch_error", reason=long_reason)],
        )
        stored = conn.execute(
            text(
                "SELECT field_attempt_log -> 0 ->> 'reason' FROM "
                "company_enrichment_jobs WHERE run_id = "
                "'11111111-1111-1111-1111-111111111111'"
            )
        ).scalar_one()
        assert stored == long_reason
        assert len(stored) == 200
        conn.rollback()


# ---------------------------------------------------------------------------
# 7. Verified-evidence CHECK protection
# (ck_company_enrichment_fields_verified_evidence).
# ---------------------------------------------------------------------------


def _insert_field(
    conn,
    *,
    verified: bool,
    verified_by=None,
    verified_at=None,
    verification_source_url=None,
) -> None:
    company_id = _create_company(conn, "Phase3 Verified Evidence Test Co")
    conn.execute(
        text(
            "INSERT INTO company_enrichment_fields "
            "(company_id, field_name, value, source, verified, "
            " verified_by, verified_at, verification_source_url) "
            "VALUES (:company_id, 'phone', '6045551234', 'website_searxng', "
            ":verified, :verified_by, :verified_at, :verification_source_url)"
        ),
        {
            "company_id": company_id,
            "verified": verified,
            "verified_by": verified_by,
            "verified_at": verified_at,
            "verification_source_url": verification_source_url,
        },
    )


def test_verified_true_with_no_evidence_is_rejected(phase3_applied_engine) -> None:
    engine = phase3_applied_engine
    with engine.begin() as conn:
        with pytest.raises(
            IntegrityError, match="ck_company_enrichment_fields_verified_evidence"
        ):
            _insert_field(conn, verified=True)
        conn.rollback()


@pytest.mark.parametrize(
    "present_fields",
    [
        {"verified_by": "reviewer@example.com"},
        {"verified_at": "2026-08-31T20:00:00+00:00"},
        {"verification_source_url": "https://example.com/contact"},
        {
            "verified_by": "reviewer@example.com",
            "verified_at": "2026-08-31T20:00:00+00:00",
        },
    ],
)
def test_verified_true_with_partial_evidence_is_rejected(
    phase3_applied_engine, present_fields
) -> None:
    """Every combination missing at least one of the three evidence
    fields must be rejected -- 'mandatory together' means all three, not
    a majority."""
    engine = phase3_applied_engine
    with engine.begin() as conn:
        with pytest.raises(
            IntegrityError, match="ck_company_enrichment_fields_verified_evidence"
        ):
            _insert_field(conn, verified=True, **present_fields)
        conn.rollback()


def test_verified_true_with_all_three_evidence_fields_is_accepted(
    phase3_applied_engine,
) -> None:
    engine = phase3_applied_engine
    with engine.begin() as conn:
        _insert_field(
            conn,
            verified=True,
            verified_by="reviewer@example.com",
            verified_at="2026-08-31T20:00:00+00:00",
            verification_source_url="https://example.com/contact",
        )
        conn.rollback()


def test_verified_false_with_no_evidence_is_accepted_normal_candidate_write(
    phase3_applied_engine,
) -> None:
    """The ordinary unverified_candidate write path -- WebsiteContactProvider
    (unchanged by this migration) never sets any evidence field, and this
    must keep working exactly as it does today."""
    engine = phase3_applied_engine
    with engine.begin() as conn:
        _insert_field(conn, verified=False)
        conn.rollback()
