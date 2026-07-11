"""Persist Registry Gateway decision records."""

from __future__ import annotations

from sqlalchemy.orm import Session

from db.models import KgEngineDecisionRecord
from pipeline.registry_gateway.constants import POLICY_VERSION_V1
from pipeline.registry_gateway.domain import DecisionDraft, DecisionRecordResult


def record_engine_decision(session: Session, draft: DecisionDraft) -> DecisionRecordResult:
    row = KgEngineDecisionRecord(
        decision=draft.decision,
        source_path=draft.source_path,
        trigger_source=draft.trigger_source,
        raw_identity=draft.raw_identity,
        canonical_key=draft.canonical_key,
        company_id=draft.company_id,
        policy_version=POLICY_VERSION_V1,
        gateway_mode=draft.gateway_mode,
        legacy_proceeded=draft.legacy_proceeded,
        reject_reason=draft.reject_reason,
        metadata_json=dict(draft.metadata),
    )
    session.add(row)
    session.flush()
    return DecisionRecordResult(record_id=int(row.id), decision=draft.decision)
