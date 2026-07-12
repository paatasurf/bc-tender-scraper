"""Registry Gateway feature flags."""

from __future__ import annotations

from config.env import env_flag
from pipeline.registry_gateway.constants import ENV_KG_GATEWAY_ENFORCE, ENV_KG_GATEWAY_SHADOW


def gateway_shadow_enabled() -> bool:
    """Log Engine decisions alongside legacy creates without blocking."""
    return env_flag(ENV_KG_GATEWAY_SHADOW, default=False)


def gateway_enforce_enabled() -> bool:
    """Block Constitution bypass creates; require Gateway approval path."""
    return env_flag(ENV_KG_GATEWAY_ENFORCE, default=False)


def gateway_active() -> bool:
    return gateway_shadow_enabled() or gateway_enforce_enabled()
