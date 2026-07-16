"""Derived Tender Evidence Link Readiness Audit.

Read-only. Audits two existing, unenforced paths from tenders to companies
(Path A: award reconciliation; Path B: self-reported bidder outcomes) plus
their cross-path agreement/contradiction. Does not create, fix, or persist
anything — no ``tenders.company_id``, no FK, no index, no migration.

Separate contract from ``pipeline.registry_engine.evidence`` (Stage 2A
permits/contract_awards audit) — no shared code, no shared schema version.
"""

from __future__ import annotations

from pipeline.registry_engine.derived_tender_evidence.audit import (
    audit_cross_path,
    audit_path_a_awarded_winner,
    audit_path_b_reported_bidder,
    run_derived_tender_evidence_audit,
)
from pipeline.registry_engine.derived_tender_evidence.domain import (
    SCHEMA_VERSION,
    CrossPathAuditReport,
    DerivedTenderEvidenceReport,
    PathAAuditReport,
    PathBAuditReport,
)

__all__ = [
    "audit_path_a_awarded_winner",
    "audit_path_b_reported_bidder",
    "audit_cross_path",
    "run_derived_tender_evidence_audit",
    "PathAAuditReport",
    "PathBAuditReport",
    "CrossPathAuditReport",
    "DerivedTenderEvidenceReport",
    "SCHEMA_VERSION",
]
