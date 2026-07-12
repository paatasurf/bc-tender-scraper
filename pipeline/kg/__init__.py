"""Knowledge Graph Phase 1 — Observation spine (dual-write only; no product consumers)."""

from __future__ import annotations

__all__ = [
    "OBSERVATION_STATUS_ACTIVE",
    "OBSERVATION_STATUS_SUPERSEDED",
    "ObservationDraft",
    "RecordObservationResult",
    "PermitObservationAdapter",
    "content_hash_for_payload",
    "dual_write_enabled",
    "record_observation",
]

from pipeline.kg.constants import (
    OBSERVATION_STATUS_ACTIVE,
    OBSERVATION_STATUS_SUPERSEDED,
)
from pipeline.kg.domain import ObservationDraft, RecordObservationResult
from pipeline.kg.adapters.permit import PermitObservationAdapter
from pipeline.kg.hashing import content_hash_for_payload
from pipeline.kg.flags import dual_write_enabled
from pipeline.kg.store import record_observation
