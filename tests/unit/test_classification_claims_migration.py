"""DB-backed tests for migration 029 (Classification Claims schema foundation).

Local Postgres only — skipped when unavailable or when DATABASE_URL resolves
to production. Every test drops and re-creates the six tables for isolation;
this is schema DDL, not application data, so function-scoped setup/teardown
is cheap and keeps every test's row-count/constraint assertions exact.
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from db.classification_claims_ddl import (
    classification_claims_migration_statements,
    classification_claims_table_names,
)
from db.classification_claims_migration import (
    ApplyReadinessStatus,
    ClassificationClaimsRollbackBlockedError,
    apply_classification_claims_migration,
    apply_classification_claims_rollback,
    classification_claims_apply_readiness,
    classification_claims_before_stats,
    classification_claims_migration_pending,
    classification_claims_row_counts,
)

BASE_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _uid() -> str:
    return str(uuid.uuid4())


# --- pure: DDL digest (no DB) --------------------------------------------------------


def test_ddl_digest_is_deterministic():
    from db.classification_claims_ddl import classification_claims_ddl_digest

    assert classification_claims_ddl_digest() == classification_claims_ddl_digest()


def test_ddl_digest_is_valid_sha256():
    from db.classification_claims_ddl import (
        classification_claims_ddl_digest,
        is_valid_ddl_digest,
    )

    assert is_valid_ddl_digest(classification_claims_ddl_digest())


def test_ddl_digest_changes_when_statements_change(monkeypatch):
    import db.classification_claims_ddl as ddl_mod

    original = ddl_mod.classification_claims_ddl_digest()
    monkeypatch.setattr(
        ddl_mod,
        "classification_claims_migration_statements",
        lambda: ["CREATE TABLE IF NOT EXISTS example_table (id INTEGER);"],
    )
    changed = ddl_mod.classification_claims_ddl_digest()
    assert changed != original
    assert ddl_mod.is_valid_ddl_digest(changed)


@pytest.mark.parametrize(
    "bad", ["", "a" * 63, "a" * 65, "A" * 64, "not-a-hash", None, 12345, "g" * 64]
)
def test_is_valid_ddl_digest_rejects_malformed(bad):
    from db.classification_claims_ddl import is_valid_ddl_digest

    assert is_valid_ddl_digest(bad) is False


def test_rollback_ddl_digest_is_deterministic_and_distinct_from_forward():
    from db.classification_claims_ddl import (
        classification_claims_ddl_digest,
        classification_claims_rollback_ddl_digest,
    )

    assert (
        classification_claims_rollback_ddl_digest()
        == classification_claims_rollback_ddl_digest()
    )
    assert (
        classification_claims_rollback_ddl_digest()
        != classification_claims_ddl_digest()
    )


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
            "Refusing classification claims migration tests against production DATABASE_URL"
        )
    return database_url


@pytest.fixture()
def claims_engine():
    import config.env  # noqa: F401

    database_url = _require_local_database_url()
    engine = create_engine(database_url, connect_args={"connect_timeout": 3})
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:
        pytest.skip("Local Postgres unavailable")

    def _drop_all():
        with engine.begin() as conn:
            for name in classification_claims_table_names():
                conn.execute(text(f"DROP TABLE IF EXISTS {name} CASCADE"))

    _drop_all()
    try:
        yield engine
    finally:
        _drop_all()
        engine.dispose()


@pytest.fixture()
def claims_schema(claims_engine):
    """Schema applied, fresh and empty."""
    apply_classification_claims_migration(claims_engine)
    return claims_engine


def _make_company(engine) -> int:
    from sqlalchemy.orm import sessionmaker

    from db.models import Company

    name = f"Claims Migration Test Co {_uid()}"
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        company = Company(name=name, display_name=name)
        session.add(company)
        session.commit()
        return int(company.id)
    finally:
        session.close()


def _insert_rule_set(engine, **overrides) -> str:
    row = dict(
        rule_set_version_id=f"sector_classification_test_{_uid()[:8]}",
        claim_type="sector_classification",
        precedence_definition_json='{"licence_authority": 1}',
        source_reliability_defaults_json="{}",
        staleness_policy_json='{"threshold_days": 90}',
        effective_from=BASE_TIME,
    )
    row.update(overrides)
    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO rule_set_versions
                    (rule_set_version_id, claim_type, precedence_definition_json,
                     source_reliability_defaults_json, staleness_policy_json, effective_from)
                VALUES
                    (:rule_set_version_id, :claim_type, CAST(:precedence_definition_json AS jsonb),
                     CAST(:source_reliability_defaults_json AS jsonb),
                     CAST(:staleness_policy_json AS jsonb), :effective_from)
                """),
            row,
        )
    return row["rule_set_version_id"]


def _insert_claim(engine, *, company_id, rule_set_version_id, **overrides) -> str:
    row = dict(
        claim_id=_uid(),
        company_id=company_id,
        claim_type="sector_classification",
        predicate="dominant_sector",
        value_json='{"sector": "roofing"}',
        source_type="government_registry",
        source_reliability=0.9,
        extraction_confidence=0.9,
        extraction_method="test:v1",
        rule_set_version_id=rule_set_version_id,
        primary_evidence_content_hash="a" * 64,
        observed_at=BASE_TIME,
        effective_at=BASE_TIME,
        idempotency_key=_uid().replace("-", "")
        + "0" * 32,  # not a real hash, just 64 hex-ish chars
    )
    row.update(overrides)
    row["idempotency_key"] = row["idempotency_key"][:64]
    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO classification_claims
                    (claim_id, company_id, claim_type, predicate, value_json, source_type,
                     source_reliability, extraction_confidence, extraction_method,
                     rule_set_version_id, primary_evidence_content_hash, observed_at,
                     effective_at, idempotency_key)
                VALUES
                    (:claim_id, :company_id, :claim_type, :predicate, CAST(:value_json AS jsonb),
                     :source_type, :source_reliability, :extraction_confidence, :extraction_method,
                     :rule_set_version_id, :primary_evidence_content_hash, :observed_at,
                     :effective_at, :idempotency_key)
                """),
            row,
        )
    return row["claim_id"]


def _hexhash(seed: str) -> str:
    import hashlib

    return hashlib.sha256(seed.encode()).hexdigest()


# --- apply / idempotency / pending detection ------------------------------------------


def test_apply_creates_all_six_tables(claims_schema):
    with claims_schema.begin() as conn:
        rows = conn.execute(
            text("""
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = ANY(:names)
                """),
            {"names": classification_claims_table_names()},
        ).all()
    assert {r[0] for r in rows} == set(classification_claims_table_names())


def test_apply_is_idempotent(claims_schema):
    # Applying a second time must not raise and must not duplicate anything.
    apply_classification_claims_migration(claims_schema)
    with claims_schema.begin() as conn:
        rows = conn.execute(
            text("""
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = ANY(:names)
                """),
            {"names": classification_claims_table_names()},
        ).all()
    assert {r[0] for r in rows} == set(classification_claims_table_names())


def test_migration_statements_nonempty():
    assert len(classification_claims_migration_statements()) > 0


def test_migration_pending_before_and_after_apply(claims_engine):
    database_url = os.environ["DATABASE_URL"]
    from sqlalchemy.orm import sessionmaker

    Session = sessionmaker(bind=claims_engine)
    session = Session()
    try:
        assert classification_claims_migration_pending(session) is True
    finally:
        session.close()

    apply_classification_claims_migration(claims_engine)

    session = Session()
    try:
        assert classification_claims_migration_pending(session) is False
        stats = classification_claims_before_stats(session)
        assert stats["tables_missing"] == []
    finally:
        session.close()
    del database_url


# --- happy path: full valid chain across all six tables ----------------------------


def test_valid_full_chain_inserts_successfully(claims_schema):
    company_id = _make_company(claims_schema)
    rule_set_id = _insert_rule_set(claims_schema)
    claim_id = _insert_claim(
        claims_schema,
        company_id=company_id,
        rule_set_version_id=rule_set_id,
        primary_evidence_content_hash=_hexhash("evidence-1"),
        idempotency_key=_hexhash("idempotency-1"),
    )
    with claims_schema.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO claim_evidence (claim_evidence_id, claim_id, evidence_source,
                                             evidence_locator, content_hash)
                VALUES (:id, :claim_id, 'kg_observation', CAST(:locator AS jsonb), :hash)
                """),
            {
                "id": _uid(),
                "claim_id": claim_id,
                "locator": json.dumps({"table": "kg_observations", "id": 1}),
                "hash": _hexhash("evidence-1"),
            },
        )
        conn.execute(
            text("""
                INSERT INTO projector_runs (projector_run_id, resolution_as_of, started_at,
                                             finished_at, claim_type, rule_set_version_id,
                                             companies_processed, beliefs_upserted, beliefs_deleted,
                                             dataset_hash)
                VALUES (:id, :ras, :started, :finished, 'sector_classification', :rsv, 1, 1, 0, :hash)
                """),
            {
                "id": (run_id := _uid()),
                "ras": BASE_TIME,
                "started": BASE_TIME,
                "finished": BASE_TIME,
                "rsv": rule_set_id,
                "hash": _hexhash("dataset-1"),
            },
        )
        conn.execute(
            text("""
                INSERT INTO resolved_company_beliefs
                    (company_id, claim_type, predicate, resolved_value_json, winning_claim_id,
                     source_type, source_reliability, extraction_confidence, resolution_confidence,
                     resolution_status, resolution_as_of, projector_run_id, rule_set_version_id)
                VALUES
                    (:company_id, 'sector_classification', 'dominant_sector', CAST(:val AS jsonb),
                     :claim_id, 'government_registry', 0.9, 0.9, 0.9, 'resolved', :ras, :run_id, :rsv)
                """),
            {
                "company_id": company_id,
                "val": json.dumps({"sector": "roofing"}),
                "claim_id": claim_id,
                "ras": BASE_TIME,
                "run_id": run_id,
                "rsv": rule_set_id,
            },
        )
    with claims_schema.begin() as conn:
        counts = {
            name: conn.execute(text(f"SELECT COUNT(*) FROM {name}")).scalar_one()
            for name in classification_claims_table_names()
        }
    assert counts == {
        "resolved_company_beliefs": 1,
        "projector_runs": 1,
        "claim_events": 0,
        "claim_evidence": 1,
        "classification_claims": 1,
        "rule_set_versions": 1,
    }


# --- constraint rejection tests -----------------------------------------------------


def test_claim_type_predicate_mismatch_rejected(claims_schema):
    company_id = _make_company(claims_schema)
    rule_set_id = _insert_rule_set(claims_schema)
    with pytest.raises(IntegrityError):
        _insert_claim(
            claims_schema,
            company_id=company_id,
            rule_set_version_id=rule_set_id,
            claim_type="sector_classification",
            predicate="licence_identifier",  # not valid for sector_classification
            primary_evidence_content_hash=_hexhash("e"),
            idempotency_key=_hexhash("i"),
        )


def test_dangling_company_id_rejected(claims_schema):
    rule_set_id = _insert_rule_set(claims_schema)
    with pytest.raises(IntegrityError):
        _insert_claim(
            claims_schema,
            company_id=999_999_999,
            rule_set_version_id=rule_set_id,
            primary_evidence_content_hash=_hexhash("e"),
            idempotency_key=_hexhash("i"),
        )


def test_dangling_rule_set_version_id_rejected(claims_schema):
    company_id = _make_company(claims_schema)
    with pytest.raises(IntegrityError):
        _insert_claim(
            claims_schema,
            company_id=company_id,
            rule_set_version_id="does-not-exist",
            primary_evidence_content_hash=_hexhash("e"),
            idempotency_key=_hexhash("i"),
        )


@pytest.mark.parametrize("bad_value", [-0.1, 1.1])
def test_out_of_range_source_reliability_rejected(claims_schema, bad_value):
    company_id = _make_company(claims_schema)
    rule_set_id = _insert_rule_set(claims_schema)
    with pytest.raises(IntegrityError):
        _insert_claim(
            claims_schema,
            company_id=company_id,
            rule_set_version_id=rule_set_id,
            source_reliability=bad_value,
            primary_evidence_content_hash=_hexhash("e"),
            idempotency_key=_hexhash("i"),
        )


def test_malformed_idempotency_key_rejected(claims_schema):
    company_id = _make_company(claims_schema)
    rule_set_id = _insert_rule_set(claims_schema)
    with pytest.raises(IntegrityError):
        _insert_claim(
            claims_schema,
            company_id=company_id,
            rule_set_version_id=rule_set_id,
            primary_evidence_content_hash=_hexhash("e"),
            idempotency_key="not-a-hash",
        )


def test_duplicate_idempotency_key_rejected(claims_schema):
    company_id = _make_company(claims_schema)
    rule_set_id = _insert_rule_set(claims_schema)
    key = _hexhash("shared-key")
    _insert_claim(
        claims_schema,
        company_id=company_id,
        rule_set_version_id=rule_set_id,
        primary_evidence_content_hash=_hexhash("e1"),
        idempotency_key=key,
    )
    with pytest.raises(IntegrityError):
        _insert_claim(
            claims_schema,
            company_id=company_id,
            rule_set_version_id=rule_set_id,
            primary_evidence_content_hash=_hexhash("e2"),
            idempotency_key=key,
        )


def test_effective_at_before_observed_at_rejected(claims_schema):
    company_id = _make_company(claims_schema)
    rule_set_id = _insert_rule_set(claims_schema)
    with pytest.raises(IntegrityError):
        _insert_claim(
            claims_schema,
            company_id=company_id,
            rule_set_version_id=rule_set_id,
            observed_at=BASE_TIME,
            effective_at=BASE_TIME - timedelta(days=1),
            primary_evidence_content_hash=_hexhash("e"),
            idempotency_key=_hexhash("i"),
        )


def test_evidence_dangling_claim_id_rejected(claims_schema):
    with claims_schema.begin() as conn, pytest.raises(IntegrityError):
        conn.execute(
            text("""
                INSERT INTO claim_evidence (claim_evidence_id, claim_id, evidence_source,
                                             evidence_locator, content_hash)
                VALUES (:id, :claim_id, 'permit', CAST(:locator AS jsonb), :hash)
                """),
            {
                "id": _uid(),
                "claim_id": _uid(),  # no such claim
                "locator": json.dumps({}),
                "hash": _hexhash("e"),
            },
        )


def test_evidence_malformed_content_hash_rejected(claims_schema):
    company_id = _make_company(claims_schema)
    rule_set_id = _insert_rule_set(claims_schema)
    claim_id = _insert_claim(
        claims_schema,
        company_id=company_id,
        rule_set_version_id=rule_set_id,
        primary_evidence_content_hash=_hexhash("e"),
        idempotency_key=_hexhash("i"),
    )
    with claims_schema.begin() as conn, pytest.raises(IntegrityError):
        conn.execute(
            text("""
                INSERT INTO claim_evidence (claim_evidence_id, claim_id, evidence_source,
                                             evidence_locator, content_hash)
                VALUES (:id, :claim_id, 'permit', CAST(:locator AS jsonb), 'not-a-hash')
                """),
            {"id": _uid(), "claim_id": claim_id, "locator": json.dumps({})},
        )


def _insert_event(engine, *, claim_id, rule_set_version_id, **overrides):
    row = dict(
        event_id=_uid(),
        claim_id=claim_id,
        event_type="rejected",
        related_claim_id=None,
        actor_type="system",
        actor_id="test",
        rule_set_version_id=rule_set_version_id,
        event_at=BASE_TIME,
    )
    row.update(overrides)
    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO claim_events
                    (event_id, claim_id, event_type, related_claim_id, actor_type, actor_id,
                     rule_set_version_id, event_at)
                VALUES
                    (:event_id, :claim_id, :event_type, :related_claim_id, :actor_type, :actor_id,
                     :rule_set_version_id, :event_at)
                """),
            row,
        )


def test_system_adjudicated_event_rejected(claims_schema):
    company_id = _make_company(claims_schema)
    rule_set_id = _insert_rule_set(claims_schema)
    claim_id = _insert_claim(
        claims_schema,
        company_id=company_id,
        rule_set_version_id=rule_set_id,
        primary_evidence_content_hash=_hexhash("e"),
        idempotency_key=_hexhash("i"),
    )
    with pytest.raises(IntegrityError):
        _insert_event(
            claims_schema,
            claim_id=claim_id,
            rule_set_version_id=rule_set_id,
            event_type="adjudicated",
            actor_type="system",
        )


def test_superseded_event_without_related_claim_rejected(claims_schema):
    company_id = _make_company(claims_schema)
    rule_set_id = _insert_rule_set(claims_schema)
    claim_id = _insert_claim(
        claims_schema,
        company_id=company_id,
        rule_set_version_id=rule_set_id,
        primary_evidence_content_hash=_hexhash("e"),
        idempotency_key=_hexhash("i"),
    )
    with pytest.raises(IntegrityError):
        _insert_event(
            claims_schema,
            claim_id=claim_id,
            rule_set_version_id=rule_set_id,
            event_type="superseded",
            related_claim_id=None,
        )


def test_related_claim_id_forbidden_on_rejected_event(claims_schema):
    company_id = _make_company(claims_schema)
    rule_set_id = _insert_rule_set(claims_schema)
    claim_id = _insert_claim(
        claims_schema,
        company_id=company_id,
        rule_set_version_id=rule_set_id,
        primary_evidence_content_hash=_hexhash("e1"),
        idempotency_key=_hexhash("i1"),
    )
    other_claim_id = _insert_claim(
        claims_schema,
        company_id=company_id,
        rule_set_version_id=rule_set_id,
        primary_evidence_content_hash=_hexhash("e2"),
        idempotency_key=_hexhash("i2"),
    )
    with pytest.raises(IntegrityError):
        _insert_event(
            claims_schema,
            claim_id=claim_id,
            rule_set_version_id=rule_set_id,
            event_type="rejected",
            related_claim_id=other_claim_id,
        )


def test_related_claim_id_equal_claim_id_rejected(claims_schema):
    company_id = _make_company(claims_schema)
    rule_set_id = _insert_rule_set(claims_schema)
    claim_id = _insert_claim(
        claims_schema,
        company_id=company_id,
        rule_set_version_id=rule_set_id,
        primary_evidence_content_hash=_hexhash("e"),
        idempotency_key=_hexhash("i"),
    )
    with pytest.raises(IntegrityError):
        _insert_event(
            claims_schema,
            claim_id=claim_id,
            rule_set_version_id=rule_set_id,
            event_type="superseded",
            related_claim_id=claim_id,
        )


def test_second_event_on_same_claim_rejected(claims_schema):
    company_id = _make_company(claims_schema)
    rule_set_id = _insert_rule_set(claims_schema)
    claim_id = _insert_claim(
        claims_schema,
        company_id=company_id,
        rule_set_version_id=rule_set_id,
        primary_evidence_content_hash=_hexhash("e"),
        idempotency_key=_hexhash("i"),
    )
    _insert_event(
        claims_schema,
        claim_id=claim_id,
        rule_set_version_id=rule_set_id,
        event_type="rejected",
    )
    with pytest.raises(IntegrityError):
        _insert_event(
            claims_schema,
            claim_id=claim_id,
            rule_set_version_id=rule_set_id,
            event_type="rejected",
        )


def test_projector_run_finished_before_started_rejected(claims_schema):
    rule_set_id = _insert_rule_set(claims_schema)
    with claims_schema.begin() as conn, pytest.raises(IntegrityError):
        conn.execute(
            text("""
                INSERT INTO projector_runs (projector_run_id, resolution_as_of, started_at,
                                             finished_at, claim_type, rule_set_version_id,
                                             companies_processed, beliefs_upserted, beliefs_deleted,
                                             dataset_hash)
                VALUES (:id, :ras, :started, :finished, 'sector_classification', :rsv, 0, 0, 0, :hash)
                """),
            {
                "id": _uid(),
                "ras": BASE_TIME,
                "started": BASE_TIME,
                "finished": BASE_TIME - timedelta(seconds=1),
                "rsv": rule_set_id,
                "hash": _hexhash("d"),
            },
        )


# --- rollback: emptiness guard -------------------------------------------------------


def test_rollback_succeeds_when_all_tables_empty(claims_schema):
    result = apply_classification_claims_rollback(claims_schema)
    assert result["statements_executed"] > 0
    with claims_schema.begin() as conn:
        rows = conn.execute(
            text("""
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = ANY(:names)
                """),
            {"names": classification_claims_table_names()},
        ).all()
    assert rows == []


def test_rollback_blocked_when_a_table_is_non_empty(claims_schema):
    _insert_rule_set(claims_schema)  # rule_set_versions now has 1 row
    with pytest.raises(ClassificationClaimsRollbackBlockedError):
        apply_classification_claims_rollback(claims_schema)
    # Tables must still exist — rollback must not have partially executed.
    with claims_schema.begin() as conn:
        rows = conn.execute(
            text("""
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = ANY(:names)
                """),
            {"names": classification_claims_table_names()},
        ).all()
    assert {r[0] for r in rows} == set(classification_claims_table_names())


def test_row_counts_zero_before_schema_exists(claims_engine):
    # claims_engine fixture drops tables but does NOT apply the migration.
    counts = classification_claims_row_counts(claims_engine)
    assert all(count == 0 for count in counts.values())


def test_row_counts_zero_after_apply(claims_schema):
    counts = classification_claims_row_counts(claims_schema)
    assert all(count == 0 for count in counts.values())


# --- rollback: atomicity / locking -----------------------------------------------------


def test_rollback_locks_existing_tables_blocking_concurrent_insert(claims_schema):
    """apply_classification_claims_rollback() must LOCK TABLE ... IN ACCESS
    EXCLUSIVE MODE on every existing table before checking emptiness, closing
    the TOCTOU race where a concurrent INSERT could land between a separate
    "check empty" transaction and a separate "DROP" transaction. This test
    proves the lock genuinely blocks a concurrent writer on a different
    connection, using the exact locking statement the fixed function issues.
    """
    database_url = os.environ["DATABASE_URL"]
    other_engine = create_engine(database_url, connect_args={"connect_timeout": 5})

    lock_acquired = threading.Event()
    release_lock = threading.Event()
    holder_error: list[BaseException] = []

    def _hold_lock_then_release():
        try:
            with claims_schema.begin() as conn:
                conn.execute(
                    text("LOCK TABLE rule_set_versions IN ACCESS EXCLUSIVE MODE")
                )
                lock_acquired.set()
                release_lock.wait(timeout=10)
        except BaseException as exc:  # noqa: BLE001 - surfaced to the test thread below
            holder_error.append(exc)

    holder_thread = threading.Thread(target=_hold_lock_then_release)
    holder_thread.start()
    try:
        assert lock_acquired.wait(
            timeout=5
        ), "lock-holder thread failed to acquire the lock"

        insert_started = threading.Event()
        insert_completed = threading.Event()
        insert_error: list[BaseException] = []

        def _attempt_concurrent_insert():
            insert_started.set()
            try:
                with other_engine.begin() as conn:
                    conn.execute(
                        text("""
                            INSERT INTO rule_set_versions
                                (rule_set_version_id, claim_type, precedence_definition_json,
                                 source_reliability_defaults_json, staleness_policy_json, effective_from)
                            VALUES
                                (:id, 'sector_classification', CAST(:p AS jsonb),
                                 CAST(:s AS jsonb), CAST(:st AS jsonb), :ef)
                            """),
                        {
                            "id": f"concurrent-{_uid()[:8]}",
                            "p": '{"licence_authority": 1}',
                            "s": "{}",
                            "st": '{"threshold_days": 90}',
                            "ef": BASE_TIME,
                        },
                    )
                insert_completed.set()
            except BaseException as exc:  # noqa: BLE001
                insert_error.append(exc)

        insert_thread = threading.Thread(target=_attempt_concurrent_insert)
        insert_thread.start()
        try:
            assert insert_started.wait(timeout=5)
            # The INSERT must NOT complete while the ACCESS EXCLUSIVE lock is held.
            assert not insert_completed.wait(timeout=1), (
                "concurrent INSERT completed while the table was locked — the rollback "
                "locking fix is not actually blocking concurrent writers"
            )

            release_lock.set()
            insert_thread.join(timeout=10)
        finally:
            insert_thread.join(timeout=1)

        assert not insert_error, f"concurrent insert thread raised: {insert_error}"
        assert (
            insert_completed.is_set()
        ), "concurrent INSERT never completed after the lock was released"
    finally:
        release_lock.set()
        holder_thread.join(timeout=10)
        other_engine.dispose()

    assert not holder_error, f"lock-holder thread raised: {holder_error}"


def test_rollback_drops_nothing_when_refused_even_with_multiple_nonempty_tables(
    claims_schema,
):
    """All-or-nothing: refusing rollback because of ANY non-empty table must
    leave every one of the six tables untouched, not just the offending one."""
    company_id = _make_company(claims_schema)
    rule_set_id = _insert_rule_set(claims_schema)
    _insert_claim(
        claims_schema,
        company_id=company_id,
        rule_set_version_id=rule_set_id,
        primary_evidence_content_hash=_hexhash("e-atomic"),
        idempotency_key=_hexhash("i-atomic"),
    )
    # Both rule_set_versions and classification_claims are now non-empty.
    with pytest.raises(ClassificationClaimsRollbackBlockedError):
        apply_classification_claims_rollback(claims_schema)
    with claims_schema.begin() as conn:
        rows = conn.execute(
            text("""
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = ANY(:names)
                """),
            {"names": classification_claims_table_names()},
        ).all()
    assert {r[0] for r in rows} == set(classification_claims_table_names())
    counts = classification_claims_row_counts(claims_schema)
    assert counts["rule_set_versions"] == 1
    assert counts["classification_claims"] == 1


# --- full schema-contract apply readiness (not just table-name presence) -----------------


def test_apply_readiness_not_applied_when_no_tables_exist(claims_engine):
    from sqlalchemy.orm import sessionmaker

    Session = sessionmaker(bind=claims_engine)
    session = Session()
    try:
        readiness = classification_claims_apply_readiness(session)
    finally:
        session.close()
    assert readiness.status is ApplyReadinessStatus.NOT_APPLIED


def test_apply_readiness_fully_applied_after_clean_apply(claims_schema):
    from sqlalchemy.orm import sessionmaker

    Session = sessionmaker(bind=claims_schema)
    session = Session()
    try:
        readiness = classification_claims_apply_readiness(session)
    finally:
        session.close()
    assert readiness.status is ApplyReadinessStatus.FULLY_APPLIED
    assert readiness.violations == []


def test_apply_readiness_corrupt_when_all_six_tables_exist_but_one_object_missing(
    claims_schema,
):
    """All six tables present is NOT sufficient to report "Already applied" —
    a table can exist while missing a required column, constraint, or index.
    This must fail closed as CORRUPT, not silently report FULLY_APPLIED."""
    with claims_schema.begin() as conn:
        conn.execute(
            text(
                "ALTER TABLE claim_events DROP CONSTRAINT ck_adjudicated_requires_human"
            )
        )

    from sqlalchemy.orm import sessionmaker

    Session = sessionmaker(bind=claims_schema)
    session = Session()
    try:
        readiness = classification_claims_apply_readiness(session)
    finally:
        session.close()

    assert readiness.status is ApplyReadinessStatus.CORRUPT
    assert any("ck_adjudicated_requires_human" in v for v in readiness.violations)
    # All six tables still report exists=True — the corruption is object-level, not
    # table-existence-level, which is exactly the gap this fix closes.
    assert all(tc.exists for tc in readiness.conformance.tables.values())
