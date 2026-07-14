"""Persist Registry Engine (Stage 1) shadow decisions.

Reuses the existing kg_engine_decision_records table and its store/domain
machinery (pipeline/registry_gateway) rather than introducing a parallel
Registry Audit table — see ADR-9 of the unified Registry Engine architecture.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from pipeline.registry_engine.constants import SOURCE_PATH_REGISTRY_ENGINE_DECIDE
from pipeline.registry_engine.domain import EngineDecision
from pipeline.registry_engine.flags import registry_engine_shadow_enabled
from pipeline.registry_gateway.domain import DecisionDraft, DecisionRecordResult
from pipeline.registry_gateway.store import record_engine_decision

logger = logging.getLogger(__name__)


def record_shadow_decision(
    session: Session,
    decision: EngineDecision,
    *,
    trigger_source: str,
) -> DecisionRecordResult | None:
    """Log one Engine decision. No-op unless REGISTRY_ENGINE_SHADOW is enabled.

    Never raises: a logging failure must not break the caller's import flow,
    matching RegistryGateway._persist's existing resilience pattern.
    """
    if not registry_engine_shadow_enabled():
        return None

    metadata = dict(decision.metadata)
    metadata["parsed_identity"] = decision.parsed_identity.to_dict()
    metadata["registry_confidence"] = decision.registry_confidence
    metadata["method"] = decision.method

    draft = DecisionDraft(
        decision=decision.decision,
        source_path=SOURCE_PATH_REGISTRY_ENGINE_DECIDE,
        trigger_source=trigger_source,
        raw_identity=decision.parsed_identity.raw_identity,
        canonical_key=str(metadata.get("canonical_key") or ""),
        company_id=decision.company_id,
        gateway_mode="shadow",
        legacy_proceeded=False,
        reject_reason=decision.reject_reason,
        metadata=metadata,
    )
    try:
        result = record_engine_decision(session, draft)
        session.commit()
        return result
    except Exception:
        logger.exception(
            "Registry Engine failed to persist shadow decision decision=%s trigger_source=%s",
            decision.decision,
            trigger_source,
        )
        session.rollback()
        return None
