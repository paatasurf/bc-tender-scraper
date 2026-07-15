"""Registry Engine — Stage 1 (RE1): shadow decide() over the existing CompanyResolver.

See docs/architecture (unified Registry Engine architecture) for the full
design. This package is additive and inert by default: decide() never writes,
and record_shadow_decision() only writes when REGISTRY_ENGINE_SHADOW is set.
"""

from __future__ import annotations

from pipeline.registry_engine.decide import decide
from pipeline.registry_engine.domain import EngineDecision
from pipeline.registry_engine.store import record_shadow_decision

__all__ = ["decide", "EngineDecision", "record_shadow_decision"]
