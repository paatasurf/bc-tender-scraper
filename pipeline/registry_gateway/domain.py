"""Registry Gateway domain types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class DecisionDraft:
    decision: str
    source_path: str
    trigger_source: str = ""
    raw_identity: str = ""
    canonical_key: str = ""
    company_id: int | None = None
    gateway_mode: str = "legacy"
    legacy_proceeded: bool = False
    reject_reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DecisionRecordResult:
    record_id: int
    decision: str
