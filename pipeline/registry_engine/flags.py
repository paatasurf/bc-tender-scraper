"""Registry Engine (Stage 1) feature flags."""

from __future__ import annotations

from config.env import env_flag

ENV_REGISTRY_ENGINE_SHADOW = "REGISTRY_ENGINE_SHADOW"


def registry_engine_shadow_enabled() -> bool:
    """Log decide() shadow decisions to kg_engine_decision_records.

    Off by default. Never blocks or mutates any caller either way — decide()
    itself performs no writes; this flag only controls whether its output is
    audit-logged.
    """
    return env_flag(ENV_REGISTRY_ENGINE_SHADOW, default=False)
