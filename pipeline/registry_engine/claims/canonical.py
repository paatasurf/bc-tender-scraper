"""Deterministic canonical JSON and content-addressed hashing for the claims
domain.

Pure functions only — no I/O, no randomness, no wall-clock reads.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class NonCanonicalValueError(ValueError):
    """Raised when a value payload contains a type that cannot be canonicalized
    deterministically for V1 — currently: floats. V1 claim predicates
    (sector_classification, licence_registration) are string/enum-only, so
    this is a deliberate simplification: float formatting is not portably
    deterministic across platforms/versions, and nothing in V1 needs it.
    """


class InvalidHashInputError(ValueError):
    """Raised when a value expected to be a lowercase SHA-256 hex digest is not
    one, caught before it is folded into a further hash computation."""


def _to_json_safe(obj: Any) -> Any:
    """Convert domain-frozen containers (MappingProxyType, tuple) back to
    plain dict/list so ``json.dumps`` can serialize them — a claim's
    ``value_json`` is deep-frozen at construction time (see domain.py), so
    canonical hashing must accept both the frozen and the equivalent plain
    form and produce identical bytes for either.
    """
    if isinstance(obj, Mapping):
        return {k: _to_json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_json_safe(v) for v in obj]
    return obj


def _reject_floats(obj: Any, path: str = "$") -> None:
    if isinstance(obj, float):
        raise NonCanonicalValueError(
            f"floats are not permitted in V1 value_json (found at {path})"
        )
    if isinstance(obj, dict):
        for key, value in obj.items():
            if not isinstance(key, str):
                raise NonCanonicalValueError(f"non-string key at {path}: {key!r}")
            _reject_floats(value, f"{path}.{key}")
    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            _reject_floats(value, f"{path}[{index}]")


def canonical_json(obj: Any) -> bytes:
    """Deterministic canonical JSON: recursively sorted keys, no whitespace,
    UTF-8. Accepts plain dict/list or the deep-frozen MappingProxyType/tuple
    equivalent — both produce identical output."""
    safe = _to_json_safe(obj)
    _reject_floats(safe)
    return json.dumps(
        safe, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def is_valid_sha256(value: str) -> bool:
    """True iff value is a lowercase 64-character hex SHA-256 digest string."""
    return isinstance(value, str) and bool(_SHA256_RE.fullmatch(value))


def compute_evidence_fingerprint(
    *, evidence_source: str, evidence_locator: dict, payload_digest: str | None = None
) -> str:
    """Deterministic SHA-256 fingerprint of an evidence citation."""
    if payload_digest is not None and not is_valid_sha256(payload_digest):
        raise InvalidHashInputError(
            f"payload_digest is not a valid SHA-256 hex digest: {payload_digest!r}"
        )
    return hashlib.sha256(
        canonical_json(
            {
                "evidence_source": evidence_source,
                "evidence_locator": evidence_locator,
                "payload_digest": payload_digest,
            }
        )
    ).hexdigest()


def compute_claim_idempotency_key(
    *,
    company_id: int,
    claim_type: str,
    predicate: str,
    source_type: str,
    primary_evidence_content_hash: str,
    extraction_method: str,
    rule_set_version_id: str,
    value_json: dict,
) -> str:
    """Deterministic SHA-256 idempotency key. No date/time component by
    design — an identical fact re-extracted from the same source, by the same
    method and rule set, collapses to the same key rather than duplicating.
    """
    if not is_valid_sha256(primary_evidence_content_hash):
        raise InvalidHashInputError(
            "primary_evidence_content_hash is not a valid SHA-256 hex digest: "
            f"{primary_evidence_content_hash!r}"
        )
    return hashlib.sha256(
        canonical_json(
            {
                "company_id": company_id,
                "claim_type": claim_type,
                "predicate": predicate,
                "source_type": source_type,
                "primary_evidence_content_hash": primary_evidence_content_hash,
                "extraction_method": extraction_method,
                "rule_set_version_id": rule_set_version_id,
                "value": value_json,
            }
        )
    ).hexdigest()
