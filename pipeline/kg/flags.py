"""Feature flags for KG Observation dual-write."""

from __future__ import annotations

from config.env import env_flag
from pipeline.kg.constants import ENV_KG_OBSERVATION_DUAL_WRITE


def dual_write_enabled() -> bool:
    """Return True when Observation dual-write should run.

    Default False so Phase 1 deploy is behavior-neutral until ops enables collection.
    Set KG_OBSERVATION_DUAL_WRITE=1 to begin dual-write after migration 025 is applied.
    """
    return env_flag(ENV_KG_OBSERVATION_DUAL_WRITE, default=False)
