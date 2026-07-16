"""Pure unit tests for pipeline.registry_engine.claims.canonical.

No database, no I/O — every test here is a plain function call.
"""

from __future__ import annotations

import pytest

from pipeline.registry_engine.claims.canonical import (
    InvalidHashInputError,
    NonCanonicalValueError,
    canonical_json,
    compute_claim_idempotency_key,
    compute_evidence_fingerprint,
    is_valid_sha256,
)


def test_canonical_json_sorts_nested_keys_deterministically():
    a = canonical_json({"b": 1, "a": {"y": 2, "x": 1}})
    b = canonical_json({"a": {"x": 1, "y": 2}, "b": 1})
    assert a == b


def test_canonical_json_deterministic_across_calls():
    obj = {"z": [3, 2, 1], "a": "value"}
    assert canonical_json(obj) == canonical_json(obj)


def test_canonical_json_sensitive_to_value_change():
    a = canonical_json({"sector": "roofing"})
    b = canonical_json({"sector": "electrical"})
    assert a != b


def test_canonical_json_rejects_top_level_float():
    with pytest.raises(NonCanonicalValueError):
        canonical_json({"value": 1.5})


def test_canonical_json_rejects_nested_float():
    with pytest.raises(NonCanonicalValueError):
        canonical_json({"a": {"b": [1, 2.0]}})


def test_canonical_json_accepts_int_bool_str_null():
    # ints, bools, strings, and null are all permitted — only float is rejected.
    result = canonical_json({"count": 3, "active": True, "name": "x", "note": None})
    assert result


def test_evidence_fingerprint_deterministic():
    a = compute_evidence_fingerprint(
        evidence_source="permit", evidence_locator={"id": 1}
    )
    b = compute_evidence_fingerprint(
        evidence_source="permit", evidence_locator={"id": 1}
    )
    assert a == b
    assert is_valid_sha256(a)


def test_evidence_fingerprint_sensitive_to_locator():
    a = compute_evidence_fingerprint(
        evidence_source="permit", evidence_locator={"id": 1}
    )
    b = compute_evidence_fingerprint(
        evidence_source="permit", evidence_locator={"id": 2}
    )
    assert a != b


def test_evidence_fingerprint_sensitive_to_source():
    a = compute_evidence_fingerprint(
        evidence_source="permit", evidence_locator={"id": 1}
    )
    b = compute_evidence_fingerprint(
        evidence_source="contract_award", evidence_locator={"id": 1}
    )
    assert a != b


def test_evidence_fingerprint_sensitive_to_payload_digest():
    a = compute_evidence_fingerprint(
        evidence_source="permit", evidence_locator={"id": 1}, payload_digest=None
    )
    b = compute_evidence_fingerprint(
        evidence_source="permit", evidence_locator={"id": 1}, payload_digest="c" * 64
    )
    assert a != b


_IDEMPOTENCY_BASE_KWARGS = dict(
    company_id=1,
    claim_type="sector_classification",
    predicate="dominant_sector",
    source_type="government_registry",
    primary_evidence_content_hash="a" * 64,
    extraction_method="test-extractor:v1",
    rule_set_version_id="sector_classification_v1",
    value_json={"sector": "roofing"},
)


def test_claim_idempotency_key_deterministic():
    a = compute_claim_idempotency_key(**_IDEMPOTENCY_BASE_KWARGS)
    b = compute_claim_idempotency_key(**_IDEMPOTENCY_BASE_KWARGS)
    assert a == b
    assert is_valid_sha256(a)


@pytest.mark.parametrize(
    "field,override",
    [
        ("company_id", 2),
        ("claim_type", "licence_registration"),
        ("predicate", "primary_trade"),
        ("source_type", "licence_authority"),
        ("primary_evidence_content_hash", "b" * 64),
        ("extraction_method", "test-extractor:v2"),
        ("rule_set_version_id", "sector_classification_v2"),
        ("value_json", {"sector": "electrical"}),
    ],
)
def test_claim_idempotency_key_sensitive_to_each_field(field, override):
    changed = dict(_IDEMPOTENCY_BASE_KWARGS)
    changed[field] = override
    assert compute_claim_idempotency_key(
        **_IDEMPOTENCY_BASE_KWARGS
    ) != compute_claim_idempotency_key(**changed)


def test_claim_idempotency_key_ignores_key_order_in_value_json():
    a = compute_claim_idempotency_key(
        **{**_IDEMPOTENCY_BASE_KWARGS, "value_json": {"sector": "roofing", "note": "x"}}
    )
    b = compute_claim_idempotency_key(
        **{**_IDEMPOTENCY_BASE_KWARGS, "value_json": {"note": "x", "sector": "roofing"}}
    )
    assert a == b


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "a" * 63,
        "a" * 65,
        "A" * 64,
        "g" * 64,
        "not-a-hash",
        "a" * 64 + " ",
        "a" * 64 + "\n",
    ],
)
def test_is_valid_sha256_rejects_malformed(bad):
    assert is_valid_sha256(bad) is False


def test_is_valid_sha256_rejects_non_string():
    assert is_valid_sha256(None) is False  # type: ignore[arg-type]
    assert is_valid_sha256(12345) is False  # type: ignore[arg-type]


def test_is_valid_sha256_accepts_valid():
    assert is_valid_sha256("a" * 64) is True
    assert is_valid_sha256("0123456789abcdef" * 4) is True


# --- SHA-256 input validation before hashing ----------------------------------------


def test_compute_claim_idempotency_key_rejects_malformed_primary_evidence_hash():
    bad_kwargs = dict(_IDEMPOTENCY_BASE_KWARGS)
    bad_kwargs["primary_evidence_content_hash"] = "not-a-hash"
    with pytest.raises(InvalidHashInputError):
        compute_claim_idempotency_key(**bad_kwargs)


def test_compute_evidence_fingerprint_rejects_malformed_payload_digest():
    with pytest.raises(InvalidHashInputError):
        compute_evidence_fingerprint(
            evidence_source="permit",
            evidence_locator={"id": 1},
            payload_digest="not-a-hash",
        )


def test_compute_evidence_fingerprint_accepts_none_payload_digest():
    # None explicitly means "no payload available" -- it is not itself validated as a hash.
    result = compute_evidence_fingerprint(
        evidence_source="permit", evidence_locator={"id": 1}, payload_digest=None
    )
    assert is_valid_sha256(result)


# --- canonical hashing of frozen vs. plain structures -------------------------------


def test_canonical_json_treats_frozen_and_plain_structures_equivalently():
    from types import MappingProxyType

    plain = {"a": 1, "b": [1, 2, 3], "c": {"nested": True}}
    frozen = MappingProxyType(
        {"a": 1, "b": (1, 2, 3), "c": MappingProxyType({"nested": True})}
    )
    assert canonical_json(plain) == canonical_json(frozen)
