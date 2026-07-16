"""Registry Engine — Stage 2A: Evidence Link readiness audit.

Read-only. Validates existing activity Evidence Links (permits.company_id,
contract_awards.company_id per Registry spec Section 10.2 — company_registry_links
is reserved for OrgBook/ODBUS Registry Evidence and is not touched here) without
creating, fixing, or persisting anything.
"""

from __future__ import annotations

from pipeline.registry_engine.evidence.audit import (
    audit_contract_award_evidence_links,
    audit_permit_evidence_links,
    audit_tender_evidence_linkage,
)
from pipeline.registry_engine.evidence.domain import (
    CURRENT_SCHEMA_VERSION,
    SCHEMA_VERSION_V1,
    SCHEMA_VERSION_V2,
    CanonicalTargetResult,
    EvidenceLinkAuditReport,
    EvidenceReference,
)

__all__ = [
    "audit_permit_evidence_links",
    "audit_contract_award_evidence_links",
    "audit_tender_evidence_linkage",
    "EvidenceReference",
    "CanonicalTargetResult",
    "EvidenceLinkAuditReport",
    "SCHEMA_VERSION_V1",
    "SCHEMA_VERSION_V2",
    "CURRENT_SCHEMA_VERSION",
]
