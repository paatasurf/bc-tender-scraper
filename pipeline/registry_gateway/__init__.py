"""Registry Gateway — Phase 2 Constitution enforcement wrapper."""

from __future__ import annotations

from pipeline.registry_gateway.constants import (
    DECISION_CREATE,
    DECISION_MATCH,
    DECISION_MERGE,
    DECISION_REJECT,
    DECISION_REVIEW,
)
from pipeline.registry_gateway.flags import (
    gateway_active,
    gateway_enforce_enabled,
    gateway_shadow_enabled,
)
from pipeline.registry_gateway.gateway import RegistryGateway, get_registry_gateway

__all__ = [
    "DECISION_CREATE",
    "DECISION_MATCH",
    "DECISION_MERGE",
    "DECISION_REJECT",
    "DECISION_REVIEW",
    "RegistryGateway",
    "gateway_active",
    "gateway_enforce_enabled",
    "gateway_shadow_enabled",
    "get_registry_gateway",
]
