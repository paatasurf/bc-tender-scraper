"""Registry Gateway — sole future gate for canonical identity mutations (Phase 2 shadow)."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from pipeline.company_name_heuristics import is_probable_person_name
from pipeline.registry_gateway.constants import (
    DECISION_CREATE,
    DECISION_REJECT,
    DECISION_REVIEW,
    GATEWAY_MODE_ENFORCE,
    GATEWAY_MODE_SHADOW,
    REJECT_REASON_BYPASS_BLOCKED,
    REJECT_REASON_PERSON,
    SOURCE_PATH_COMPANY_RESOLVER_CREATE,
    SOURCE_PATH_POPULATE_AWARDS,
)
from pipeline.registry_gateway.domain import DecisionDraft
from pipeline.registry_gateway.flags import gateway_active, gateway_enforce_enabled, gateway_shadow_enabled
from pipeline.registry_gateway.store import record_engine_decision

logger = logging.getLogger(__name__)


class RegistryGateway:
    """Wrap legacy identity CREATE paths with Constitution-aware audit."""

    def __init__(self, session: Session) -> None:
        self.session = session

    @staticmethod
    def _mode_label() -> str:
        if gateway_enforce_enabled():
            return GATEWAY_MODE_ENFORCE
        if gateway_shadow_enabled():
            return GATEWAY_MODE_SHADOW
        return "legacy"

    def allow_resolver_create(
        self,
        *,
        raw_name: str,
        canonical_key: str,
        trigger_source: str,
        method: str,
    ) -> tuple[bool, str]:
        """Return (allowed, reject_reason). When enforce off, always allowed."""
        if is_probable_person_name(raw_name):
            if gateway_active():
                self._persist(
                    DecisionDraft(
                        decision=DECISION_REJECT,
                        source_path=SOURCE_PATH_COMPANY_RESOLVER_CREATE,
                        trigger_source=trigger_source,
                        raw_identity=raw_name,
                        canonical_key=canonical_key,
                        gateway_mode=self._mode_label(),
                        legacy_proceeded=False,
                        reject_reason=REJECT_REASON_PERSON,
                        metadata={"method": method, "phase": "pre_create"},
                    )
                )
            if gateway_enforce_enabled():
                return False, REJECT_REASON_PERSON
            return True, ""

        if gateway_enforce_enabled():
            # Enforce: resolver CREATE still permitted for non-person until Engine full cutover.
            # populate_companies_from_awards bypass is blocked separately.
            return True, ""

        return True, ""

    def record_resolver_create_outcome(
        self,
        *,
        raw_name: str,
        canonical_key: str,
        trigger_source: str,
        company_id: int | None,
        created: bool,
        method: str,
        confidence: float,
    ) -> None:
        if not gateway_shadow_enabled() and not gateway_enforce_enabled():
            return
        if not created or company_id is None:
            return

        self._persist(
            DecisionDraft(
                decision=DECISION_CREATE,
                source_path=SOURCE_PATH_COMPANY_RESOLVER_CREATE,
                trigger_source=trigger_source,
                raw_identity=raw_name,
                canonical_key=canonical_key,
                company_id=company_id,
                gateway_mode=self._mode_label(),
                legacy_proceeded=True,
                metadata={
                    "method": method,
                    "confidence": confidence,
                    "shadow_note": "legacy_create_proceeded",
                },
            )
        )

    def filter_award_populate_payload(
        self,
        payload: list[dict[str, Any]],
        *,
        trigger_source: str = "contract_awards",
    ) -> tuple[list[dict[str, Any]], dict[str, int]]:
        """Return (allowed_payload, stats). Enforce blocks direct award CREATE bypass."""
        if not gateway_active():
            return payload, {"blocked": 0, "logged": 0}

        if not gateway_enforce_enabled():
            for row in payload:
                self._persist(
                    DecisionDraft(
                        decision=DECISION_CREATE,
                        source_path=SOURCE_PATH_POPULATE_AWARDS,
                        trigger_source=trigger_source,
                        raw_identity=str(row.get("name") or ""),
                        canonical_key=str(row.get("canonical_vendor_name") or ""),
                        gateway_mode=GATEWAY_MODE_SHADOW,
                        legacy_proceeded=True,
                        metadata={"shadow_note": "award_populate_bypass_logged"},
                    )
                )
            return payload, {"blocked": 0, "logged": len(payload)}

        for row in payload:
            self._persist(
                DecisionDraft(
                    decision=DECISION_REVIEW,
                    source_path=SOURCE_PATH_POPULATE_AWARDS,
                    trigger_source=trigger_source,
                    raw_identity=str(row.get("name") or ""),
                    canonical_key=str(row.get("canonical_vendor_name") or ""),
                    gateway_mode=GATEWAY_MODE_ENFORCE,
                    legacy_proceeded=False,
                    reject_reason=REJECT_REASON_BYPASS_BLOCKED,
                    metadata={"note": "awaiting_engine_create_path"},
                )
            )
        return [], {"blocked": len(payload), "logged": len(payload)}

    def _persist(self, draft: DecisionDraft) -> None:
        try:
            record_engine_decision(self.session, draft)
            self.session.commit()
        except Exception:
            logger.exception(
                "Registry Gateway failed to persist decision source_path=%s decision=%s",
                draft.source_path,
                draft.decision,
            )
            self.session.rollback()


def get_registry_gateway(session: Session) -> RegistryGateway:
    return RegistryGateway(session)
