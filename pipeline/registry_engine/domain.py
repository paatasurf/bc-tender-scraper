"""Registry Engine (Stage 1) domain types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pipeline.identity_parser import ParsedIdentity


@dataclass(frozen=True)
class EngineDecision:
    """Shadow-mode decision output.

    Stage 1 never mutates the database on its own account — decide() is a
    read-only computation, and record_shadow_decision() is the only thing
    that writes (an audit row), gated by REGISTRY_ENGINE_SHADOW.
    """

    decision: str
    parsed_identity: ParsedIdentity
    company_id: int | None = None
    registry_confidence: str = ""
    method: str = ""
    reject_reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
