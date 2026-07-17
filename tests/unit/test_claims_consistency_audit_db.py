"""DB-backed tests for db.classification_claims_consistency_audit.

Local Postgres only -- skipped when unavailable or when DATABASE_URL resolves
to production. Proves the fetch layer wires real rows into the pure
evaluator correctly: an empty, freshly-applied schema (mirroring the current
actual production state) is PASS; data written through the real Gateway is
PASS; and a hand-crafted cross-table inconsistency the schema's own FK/CHECK
constraints cannot prevent is correctly caught.
"""

from __future__ import annotations

import os
import threading
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session

from db.classification_claims_consistency_audit import (
    AUDIT_ISOLATION_LEVEL,
    run_claims_consistency_audit,
)
from db.classification_claims_ddl import classification_claims_table_names
from db.classification_claims_migration import apply_classification_claims_migration
from db.models import Company
from pipeline.registry_engine.claims.domain import (
    ActorType,
    ClaimType,
    EventType,
    SourceType,
)
from pipeline.registry_engine.claims.gateway import record_event, submit_claim
from pipeline.registry_engine.claims.gateway_capability import (
    _unwrap_engine,
    acquire_claims_write_capability,
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
        pytest.skip("Refusing consistency audit tests against production DATABASE_URL")
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
            conn.execute(
                text(
                    "DELETE FROM companies WHERE name LIKE '__test_claims_consistency_company%'"
                )
            )

    _drop_all()
    try:
        yield engine
    finally:
        _drop_all()
        engine.dispose()


@pytest.fixture()
def claims_schema(claims_engine):
    apply_classification_claims_migration(claims_engine)
    return claims_engine


@pytest.fixture()
def seeded_company(claims_schema):
    with Session(claims_schema) as session:
        company = Company(name="__test_claims_consistency_company__")
        session.add(company)
        session.commit()
        return company.id


@pytest.fixture()
def seeded_rule_set(claims_schema):
    with claims_schema.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO rule_set_versions (
                    rule_set_version_id, claim_type, description,
                    precedence_definition_json, source_reliability_defaults_json,
                    staleness_policy_json, effective_from
                ) VALUES (
                    'v1-sector-test', 'sector_classification', '',
                    CAST('{"licence_authority": 1}' AS jsonb), '{}'::jsonb, '{}'::jsonb,
                    :effective_from
                )
                """),
            {"effective_from": datetime(2020, 1, 1, tzinfo=timezone.utc)},
        )
    return "v1-sector-test"


def test_empty_schema_passes(claims_schema):
    result = run_claims_consistency_audit(claims_schema)
    assert result["status"] == "PASS"
    assert result["findings"] == []
    assert result["schema_version"] == 1
    assert len(result["dataset_hash"]) == 64


def test_gateway_written_data_is_fully_consistent(
    claims_schema, seeded_company, seeded_rule_set
):
    """End-to-end: everything the real Gateway writes (claim + evidence +
    event) must satisfy every invariant this audit checks."""
    capability = acquire_claims_write_capability(
        "test_claims_consistency_audit", allow_production=False
    )
    try:
        now = datetime.now(timezone.utc)
        result = submit_claim(
            capability,
            company_id=seeded_company,
            claim_type=ClaimType.SECTOR_CLASSIFICATION,
            predicate="dominant_sector",
            value_json={"sector": "roofing"},
            source_type=SourceType.LICENCE_AUTHORITY,
            source_reliability=0.9,
            extraction_confidence=0.8,
            extraction_method="test_extractor_v1",
            rule_set_version_id=seeded_rule_set,
            evidence_source="licence_authority_raw",
            evidence_locator={"url": "https://example.test/permit/123"},
            observed_at=now - timedelta(days=1),
            effective_at=now,
            dry_run=False,
        )
        record_event(
            capability,
            claim_id=result.claim_id,
            event_type=EventType.REJECTED,
            related_claim_id=None,
            actor_type=ActorType.SYSTEM,
            actor_id="test-system",
            rule_set_version_id=seeded_rule_set,
            event_at=datetime.now(timezone.utc),
        )

        audit_result = run_claims_consistency_audit(claims_schema)
        assert audit_result["status"] == "PASS"
        assert audit_result["findings"] == []
        assert audit_result["counts"] == {
            "claims": 1,
            "evidence": 1,
            "events": 1,
            "rule_sets": 1,
        }
    finally:
        _unwrap_engine(capability).dispose()


def test_primary_evidence_hash_mismatch_is_caught(
    claims_schema, seeded_company, seeded_rule_set
):
    """A cross-table inconsistency no FK/CHECK can prevent: a claim's
    primary_evidence_content_hash that does not match any claim_evidence row
    for that claim -- constructed via direct SQL (bypassing the Gateway, as
    a stand-in for a hypothetical future bug or manual operation)."""
    claim_id = "11111111-1111-1111-1111-111111111111"
    now = datetime.now(timezone.utc)
    wrong_hash = "a" * 64
    real_hash = "b" * 64
    with claims_schema.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO classification_claims (
                    claim_id, company_id, claim_type, predicate, value_json,
                    source_type, source_reliability, extraction_confidence,
                    extraction_method, rule_set_version_id,
                    primary_evidence_content_hash, observed_at, effective_at,
                    extracted_at, idempotency_key, created_at
                ) VALUES (
                    :claim_id, :company_id, 'sector_classification', 'dominant_sector',
                    CAST('{"sector": "roofing"}' AS jsonb), 'licence_authority', 0.9, 0.8,
                    'manual', :rule_set_version_id, :wrong_hash, :now, :now, :now,
                    :idempotency_key, :now
                )
                """),
            {
                "claim_id": claim_id,
                "company_id": seeded_company,
                "rule_set_version_id": seeded_rule_set,
                "wrong_hash": wrong_hash,
                "now": now,
                "idempotency_key": "c" * 64,
            },
        )
        conn.execute(
            text("""
                INSERT INTO claim_evidence (
                    claim_evidence_id, claim_id, evidence_source,
                    evidence_locator, content_hash, created_at
                ) VALUES (
                    :evidence_id, :claim_id, 'licence_authority_raw',
                    CAST('{"url": "https://example.test"}' AS jsonb), :real_hash, :now
                )
                """),
            {
                "evidence_id": "22222222-2222-2222-2222-222222222222",
                "claim_id": claim_id,
                "real_hash": real_hash,
                "now": now,
            },
        )

    result = run_claims_consistency_audit(claims_schema)
    assert result["status"] == "FAIL"
    assert any("no claim_evidence row matches" in f for f in result["findings"])


def test_cross_scope_superseded_related_claim_is_caught(
    claims_schema, seeded_company, seeded_rule_set
):
    """The schema has no CHECK tying a superseded event's related_claim_id
    to the same (company_id, claim_type, predicate) as its own claim -- only
    the Gateway enforces this at write time. Direct SQL can still violate
    it, and the audit must catch it."""
    claim_a = "33333333-3333-3333-3333-333333333333"
    claim_b = "44444444-4444-4444-4444-444444444444"
    now = datetime.now(timezone.utc)

    def _insert_claim(claim_id: str, predicate: str) -> None:
        with claims_schema.begin() as conn:
            evidence_hash = ("d" * 63) + claim_id[-1]
            conn.execute(
                text("""
                    INSERT INTO classification_claims (
                        claim_id, company_id, claim_type, predicate, value_json,
                        source_type, source_reliability, extraction_confidence,
                        extraction_method, rule_set_version_id,
                        primary_evidence_content_hash, observed_at, effective_at,
                        extracted_at, idempotency_key, created_at
                    ) VALUES (
                        :claim_id, :company_id, 'sector_classification', :predicate,
                        CAST('{"sector": "roofing"}' AS jsonb), 'licence_authority', 0.9, 0.8,
                        'manual', :rule_set_version_id, :evidence_hash, :now, :now, :now,
                        :idempotency_key, :now
                    )
                    """),
                {
                    "claim_id": claim_id,
                    "company_id": seeded_company,
                    "predicate": predicate,
                    "rule_set_version_id": seeded_rule_set,
                    "evidence_hash": evidence_hash,
                    "now": now,
                    "idempotency_key": ("e" * 63) + claim_id[-1],
                },
            )
            conn.execute(
                text("""
                    INSERT INTO claim_evidence (
                        claim_evidence_id, claim_id, evidence_source,
                        evidence_locator, content_hash, created_at
                    ) VALUES (
                        :evidence_id, :claim_id, 'licence_authority_raw',
                        CAST('{"url": "https://example.test"}' AS jsonb), :evidence_hash, :now
                    )
                    """),
                {
                    "evidence_id": f"{claim_id[:-1]}9",
                    "claim_id": claim_id,
                    "evidence_hash": evidence_hash,
                    "now": now,
                },
            )

    _insert_claim(claim_a, "dominant_sector")
    _insert_claim(claim_b, "primary_trade")  # different predicate -> different scope

    with claims_schema.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO claim_events (
                    event_id, claim_id, event_type, related_claim_id,
                    actor_type, actor_id, rationale, rule_set_version_id,
                    event_at, created_at
                ) VALUES (
                    :event_id, :claim_id, 'superseded', :related_claim_id,
                    'system', 'manual', NULL, :rule_set_version_id, :now, :now
                )
                """),
            {
                "event_id": "55555555-5555-5555-5555-555555555555",
                "claim_id": claim_a,
                "related_claim_id": claim_b,
                "rule_set_version_id": seeded_rule_set,
                "now": now,
            },
        )

    result = run_claims_consistency_audit(claims_schema)
    assert result["status"] == "FAIL"
    assert any(
        "outside the company_id/claim_type/predicate scope" in f
        for f in result["findings"]
    )


# --- isolation contract -----------------------------------------------------------------


def test_audit_owns_repeatable_read_isolation(claims_schema, monkeypatch):
    """Proves run_claims_consistency_audit does not rely on whatever
    isolation level a connection happens to have -- it explicitly requests
    REPEATABLE READ, and that request actually takes effect at the DB
    level (not just a Python-side execution_options dict)."""
    import db.classification_claims_consistency_audit as audit_module

    assert AUDIT_ISOLATION_LEVEL == "REPEATABLE READ"

    captured_levels: list[str] = []
    real_fetch = audit_module.fetch_claims_consistency_dataset

    def spy_fetch(conn):
        captured_levels.append(conn.get_isolation_level())
        return real_fetch(conn)

    monkeypatch.setattr(audit_module, "fetch_claims_consistency_dataset", spy_fetch)
    audit_module.run_claims_consistency_audit(claims_schema)

    assert captured_levels == ["REPEATABLE READ"]


def test_audit_ignores_a_default_isolation_session(claims_schema, monkeypatch):
    """Even if a caller's Session/Engine is left at Postgres's ordinary
    default (READ COMMITTED), run_claims_consistency_audit must still use
    REPEATABLE READ for its own transaction -- it takes only an Engine and
    builds its own connection/transaction rather than accepting a
    caller-configured one."""
    import db.classification_claims_consistency_audit as audit_module

    # A bare engine with no special execution_options -- Postgres's default
    # (READ COMMITTED) applies to any transaction opened on it, UNLESS the
    # audit itself overrides that, which is exactly what this asserts.
    plain_engine = create_engine(
        os.environ["DATABASE_URL"], connect_args={"connect_timeout": 3}
    )
    try:
        captured_levels: list[str] = []
        real_fetch = audit_module.fetch_claims_consistency_dataset

        def spy_fetch(conn):
            captured_levels.append(conn.get_isolation_level())
            return real_fetch(conn)

        monkeypatch.setattr(audit_module, "fetch_claims_consistency_dataset", spy_fetch)
        audit_module.run_claims_consistency_audit(plain_engine)

        assert captured_levels == ["REPEATABLE READ"]
    finally:
        plain_engine.dispose()


# --- concurrency: no torn snapshot -------------------------------------------------------


def test_no_torn_snapshot_under_concurrent_writes(
    claims_schema, seeded_company, seeded_rule_set
):
    """Real Postgres concurrency regression: a new rule_set_versions row is
    committed by a SEPARATE connection while the audit's own transaction is
    paused between its first SELECT (classification_claims) and its later
    ones (claim_evidence / claim_events / rule_set_versions). If the audit
    were running at READ COMMITTED (the bug this hardening fixes), its
    LAST query (rule_set_versions) -- executed, by wall-clock time, AFTER
    the concurrent commit -- would see the new row even though its FIRST
    query's snapshot did not. Under the required REPEATABLE READ isolation,
    every query in the audit's transaction is pinned to the snapshot taken
    at the transaction's first query, so the concurrently-committed row
    must be invisible to ALL four queries, not just the first."""
    database_url = os.environ["DATABASE_URL"]
    pause_before_evidence = threading.Event()
    proceed = threading.Event()
    seen_claims_query = threading.Event()

    def before_cursor_execute(
        conn, cursor, statement, parameters, context, executemany
    ):
        if "FROM classification_claims" in statement:
            seen_claims_query.set()
        elif "FROM claim_evidence" in statement and seen_claims_query.is_set():
            pause_before_evidence.set()
            proceed.wait(timeout=10)

    event.listen(claims_schema, "before_cursor_execute", before_cursor_execute)
    audit_result: dict = {}
    audit_error: list[BaseException] = []

    def _run_audit():
        try:
            audit_result["value"] = run_claims_consistency_audit(claims_schema)
        except BaseException as exc:  # noqa: BLE001
            audit_error.append(exc)

    try:
        audit_thread = threading.Thread(target=_run_audit)
        audit_thread.start()
        assert pause_before_evidence.wait(
            timeout=5
        ), "audit never paused before its claim_evidence query"

        # Concurrently, on a SEPARATE connection/engine, commit a brand new
        # rule_set_versions row -- self-contained, no FK dependencies.
        concurrent_engine = create_engine(
            database_url, connect_args={"connect_timeout": 3}
        )
        try:
            with concurrent_engine.begin() as conn:
                conn.execute(
                    text("""
                        INSERT INTO rule_set_versions (
                            rule_set_version_id, claim_type, description,
                            precedence_definition_json, source_reliability_defaults_json,
                            staleness_policy_json, effective_from
                        ) VALUES (
                            'v1-concurrent-intruder', 'sector_classification', '',
                            CAST('{"licence_authority": 1}' AS jsonb), '{}'::jsonb, '{}'::jsonb,
                            :effective_from
                        )
                        """),
                    # A distinct effective_from -- seeded_rule_set already used
                    # (sector_classification, 2020-01-01), and
                    # uq_rule_set_versions_claim_type_effective_from would
                    # reject a second row with the same pair.
                    {"effective_from": datetime(2021, 6, 1, tzinfo=timezone.utc)},
                )
        finally:
            concurrent_engine.dispose()

        proceed.set()
        audit_thread.join(timeout=10)
    finally:
        event.remove(claims_schema, "before_cursor_execute", before_cursor_execute)
        # Clean up the intruder row outside the audit's snapshot so it
        # doesn't leak into other tests via the claims_engine fixture's
        # DROP TABLE teardown (which would remove it anyway, but do it
        # explicitly for clarity/robustness if this test is ever adapted).
        with claims_schema.begin() as conn:
            conn.execute(
                text(
                    "DELETE FROM rule_set_versions WHERE rule_set_version_id = 'v1-concurrent-intruder'"
                )
            )

    assert not audit_error, f"audit failed: {audit_error}"
    assert "value" in audit_result
    result = audit_result["value"]
    assert result["status"] == "PASS"
    # The decisive assertion: rule_set_versions is queried LAST, well after
    # (by wall-clock time) the concurrent commit above -- if isolation were
    # torn, this would be 2 (the original + the intruder), not 1.
    assert result["counts"]["rule_sets"] == 1
