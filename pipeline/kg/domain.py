"""Observation domain model for the KG Phase 1 spine."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class ObservationDraft:
    """In-memory observation before persistence.

    Immutable once built by an adapter. The store computes content_hash from payload.
    """

    source: str
    external_id: str
    payload: dict[str, Any]
    entity_type: str
    observed_at: datetime | None = None
    schema_version: str = "1"
    adapter_version: str = "1"


@dataclass(frozen=True)
class RecordObservationResult:
    """Outcome of a single observation persist attempt."""

    observation_id: int
    created: bool
    superseded_prior: bool = False
    idempotent_replay: bool = False


@dataclass
class DualWriteStats:
    """Aggregate counters for adapter batch dual-write (logging/metrics only)."""

    recorded: int = 0
    idempotent: int = 0
    superseded: int = 0
    skipped: int = 0
    errors: int = 0

    def merge(self, result: RecordObservationResult | None, *, error: bool = False) -> None:
        if error:
            self.errors += 1
            return
        if result is None:
            self.skipped += 1
            return
        if result.idempotent_replay:
            self.idempotent += 1
            return
        self.recorded += 1
        if result.superseded_prior:
            self.superseded += 1
