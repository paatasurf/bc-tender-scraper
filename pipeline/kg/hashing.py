"""Deterministic content hashing for Observation idempotency."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json(payload: dict[str, Any]) -> str:
    """Serialize payload for stable hashing (sorted keys, compact separators)."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def content_hash_for_payload(payload: dict[str, Any]) -> str:
    """Return SHA-256 hex digest of canonical JSON payload."""
    digest = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    return digest
