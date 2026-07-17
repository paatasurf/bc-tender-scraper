"""DB-facing layer for the Classification Claims consistency audit.

Fetches a read-only snapshot of the four relevant tables (classification_claims,
claim_evidence, claim_events, rule_set_versions), hands it to the pure
evaluator in ``pipeline.registry_engine.claims.consistency``, and attaches a
deterministic ``dataset_hash`` plus ``schema_version`` to the result.

SELECT-only. No DDL, no writes -- used by
``scripts/run_claims_consistency_audit.py`` (Class A).

``run_claims_consistency_audit`` owns its own transaction, explicitly set to
``REPEATABLE READ`` isolation, so all four SELECTs, the evaluation, and the
``dataset_hash`` observe exactly one consistent Postgres snapshot (fixed at
the transaction's first query) -- never a torn read where, say, a claim
committed concurrently between the ``classification_claims`` and
``claim_evidence`` queries shows up in one but not the other. It takes an
``Engine`` (not a caller-supplied ``Session``/``Connection``) specifically
so it is never at the mercy of whatever isolation level a caller's session
happens to be configured with -- READ COMMITTED (Postgres's and this
codebase's usual default) is not sufficient for a multi-query, point-in-time
audit like this one.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict

from sqlalchemy import text
from sqlalchemy.engine import Engine

from pipeline.registry_engine.claims.consistency import (
    ClaimRow,
    EventRow,
    EvidenceRow,
    RuleSetRow,
    evaluate_claims_consistency,
)

SCHEMA_VERSION = 1

# The isolation level run_claims_consistency_audit explicitly requires --
# exposed as a constant so tests can assert against it without hardcoding
# the string in more than one place.
AUDIT_ISOLATION_LEVEL = "REPEATABLE READ"

__all__ = [
    "AUDIT_ISOLATION_LEVEL",
    "SCHEMA_VERSION",
    "compute_dataset_hash",
    "fetch_claims_consistency_dataset",
    "run_claims_consistency_audit",
]


def fetch_claims_consistency_dataset(conn) -> dict:
    """Read-only. Accepts a SQLAlchemy Connection or Session -- anything
    with an ``.execute()`` compatible with ``text()`` statements. Callers
    wanting the four SELECTs below to observe one consistent snapshot must
    call this from inside an explicit REPEATABLE READ transaction (see
    ``run_claims_consistency_audit``, which does exactly that) -- called
    directly against a default-isolation connection/session, these four
    queries are not guaranteed to agree on a single point in time."""
    claim_rows = conn.execute(text("""
            SELECT claim_id, company_id, claim_type, predicate, source_type,
                   primary_evidence_content_hash, idempotency_key,
                   rule_set_version_id, effective_at
            FROM classification_claims
            ORDER BY claim_id
            """)).all()
    claims = [
        ClaimRow(
            claim_id=str(r[0]),
            company_id=r[1],
            claim_type=r[2],
            predicate=r[3],
            source_type=r[4],
            primary_evidence_content_hash=r[5],
            idempotency_key=r[6],
            rule_set_version_id=r[7],
            effective_at=r[8],
        )
        for r in claim_rows
    ]

    evidence_rows = conn.execute(text("""
            SELECT claim_evidence_id, claim_id, evidence_source, content_hash
            FROM claim_evidence
            ORDER BY claim_evidence_id
            """)).all()
    evidence = [
        EvidenceRow(
            claim_evidence_id=str(r[0]),
            claim_id=str(r[1]),
            evidence_source=r[2],
            content_hash=r[3],
        )
        for r in evidence_rows
    ]

    event_rows = conn.execute(text("""
            SELECT event_id, claim_id, event_type, related_claim_id,
                   rule_set_version_id, event_at
            FROM claim_events
            ORDER BY event_id
            """)).all()
    events = [
        EventRow(
            event_id=str(r[0]),
            claim_id=str(r[1]),
            event_type=r[2],
            related_claim_id=str(r[3]) if r[3] is not None else None,
            rule_set_version_id=r[4],
            event_at=r[5],
        )
        for r in event_rows
    ]

    rule_set_rows = conn.execute(text("""
            SELECT rule_set_version_id, claim_type, effective_from
            FROM rule_set_versions
            ORDER BY rule_set_version_id
            """)).all()
    rule_sets = [
        RuleSetRow(rule_set_version_id=r[0], claim_type=r[1], effective_from=r[2])
        for r in rule_set_rows
    ]

    return {
        "claims": claims,
        "evidence": evidence,
        "events": events,
        "rule_sets": rule_sets,
    }


def compute_dataset_hash(dataset: dict) -> str:
    """Deterministic SHA-256 fingerprint of the fetched snapshot -- changes
    whenever any audited row changes, so a re-run against unchanged data is
    verifiably a no-op. Not the canonical_json used for claim hashing
    (that rejects floats and is scoped to a single claim's value_json);
    this is a simpler whole-dataset fingerprint, sorted-key JSON with a
    str() fallback for datetimes."""
    serializable = {
        "claims": [asdict(c) for c in dataset["claims"]],
        "evidence": [asdict(e) for e in dataset["evidence"]],
        "events": [asdict(e) for e in dataset["events"]],
        "rule_sets": [asdict(r) for r in dataset["rule_sets"]],
    }
    canonical = json.dumps(
        serializable, sort_keys=True, default=str, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def run_claims_consistency_audit(engine: Engine) -> dict:
    """Fetch + evaluate + attach dataset_hash/schema_version, all inside one
    explicit REPEATABLE READ transaction that this function owns end to end
    -- see module docstring for why a caller-supplied Session/Connection
    (whose isolation level this function would not control) is not accepted
    here instead."""
    with engine.connect().execution_options(
        isolation_level=AUDIT_ISOLATION_LEVEL
    ) as conn:
        with conn.begin():
            dataset = fetch_claims_consistency_dataset(conn)
            result = evaluate_claims_consistency(**dataset)
    result["dataset_hash"] = compute_dataset_hash(dataset)
    result["schema_version"] = SCHEMA_VERSION
    return result
