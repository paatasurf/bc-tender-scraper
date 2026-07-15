"""Registry Engine (Stage 1 / RE1) constants.

Reuses the Gateway's decision vocabulary (pipeline/registry_gateway/constants.py)
rather than redefining match/merge/create/reject as a second set of strings —
see ADR-3 of the unified Registry Engine architecture.
"""

from __future__ import annotations

from pipeline.registry_gateway.constants import (
    DECISION_CREATE,
    DECISION_MATCH,
    DECISION_MERGE,
    DECISION_REJECT,
    REJECT_REASON_PERSON,
)

__all__ = [
    "DECISION_CREATE",
    "DECISION_MATCH",
    "DECISION_MERGE",
    "DECISION_REJECT",
    "REJECT_REASON_PERSON",
    "REJECT_REASON_EMPTY",
    "POLICY_VERSION_ENGINE_V1",
    "SOURCE_PATH_REGISTRY_ENGINE_DECIDE",
    "REGISTRY_CONFIDENCE_MEDIUM",
    "REGISTRY_CONFIDENCE_LOW",
    "REGISTRY_CONFIDENCE_UNVERIFIED",
]

POLICY_VERSION_ENGINE_V1 = "engine-v1"
SOURCE_PATH_REGISTRY_ENGINE_DECIDE = "registry_engine.decide"

REJECT_REASON_EMPTY = "empty_identity"

# Registry Confidence (spec.md Section 7.3). Stage 1's decide() performs no
# OrgBook/ODB cross-check, so it must never assign "verified" or "high" —
# both require registry corroboration decide() does not yet have.
REGISTRY_CONFIDENCE_MEDIUM = "medium"
REGISTRY_CONFIDENCE_LOW = "low"
REGISTRY_CONFIDENCE_UNVERIFIED = "unverified"
