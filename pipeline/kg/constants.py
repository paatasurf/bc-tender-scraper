"""Constants for the KG Observation spine."""

from __future__ import annotations

OBSERVATION_STATUS_ACTIVE = "active"
OBSERVATION_STATUS_SUPERSEDED = "superseded"
OBSERVATION_STATUS_QUARANTINED = "quarantined"
OBSERVATION_STATUS_NEEDS_NORMALIZE = "needs_normalize"

OUTBOX_STATUS_PENDING = "pending"
OUTBOX_EVENT_OBSERVATION_RECORDED = "ObservationRecorded"

SCHEMA_VERSION_V1 = "1"
ADAPTER_VERSION_PERMIT_V1 = "permit_v1"

ENTITY_TYPE_PERMIT = "permit"

# Env flag: when false, dual-write is a no-op (rollback / kill switch).
ENV_KG_OBSERVATION_DUAL_WRITE = "KG_OBSERVATION_DUAL_WRITE"
