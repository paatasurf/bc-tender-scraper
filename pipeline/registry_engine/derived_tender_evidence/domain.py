"""Derived Tender Evidence Link Readiness Audit — domain types.

Pure data + pure hashing. Nothing here touches a database session.

This is a separate contract from ``pipeline/registry_engine/evidence``
(Stage 2A permits/contract_awards Evidence Link audit). It does not modify,
import, or depend on that module's schema, and it must not be read as an
extension of it — ``schema_version`` here is scoped to this package only.

Two existing, unenforced paths are audited (see
``scripts/run_derived_tender_evidence_audit.py``):

- Path A: ``tenders.award_id -> contract_awards.id -> contract_awards.company_id``
  (algorithmic reconciliation, ``pipeline/awards_reconciler.py``).
- Path B: ``tenders.tender_id -> tender_outcomes.tender_id -> tender_outcomes.company_id``
  (human-asserted win/loss self-report).

Neither path is stored anywhere by this audit. No ``tenders.company_id`` is
created or implied.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

SCHEMA_VERSION = 1

PATH_A_ROLE = "awarded_winner"
PATH_B_ROLE = "reported_bidder"

CONFIDENCE_BUCKET_MISSING = "missing"
CONFIDENCE_BUCKET_PARTIAL = "partial"
CONFIDENCE_BUCKET_HIGH = "high"


def _stable_hash(*parts: str) -> str:
    """Deterministic sha256 over pipe-joined, explicitly-ordered parts."""
    joined = "|".join(parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class PathAAuditReport:
    """Path A: award-reconciliation-derived winner.

    All counts are full-dataset aggregates — never derived from or bounded
    by the bounded illustrative samples, which exist purely for
    human-readable context in the report.

    ``resolved_award_company_count`` and ``resolved_awarded_winner_count``
    are deliberately distinct: the former is every tender (any
    lifecycle_status) whose award_id resolves to a company — used only for
    the award_id partition invariant (dangling + award_without_company +
    resolved_award_company_count == total tenders with award_id set). The
    latter is scoped to ``lifecycle_status == 'awarded'`` only, and is what
    coverage_rate and entity_role_counts are actually built from — a
    resolved link on a non-awarded tender is not "winner coverage."
    """

    generated_at: str
    inventory_total: int
    eligible_awarded_total: int
    awarded_with_award_id: int
    awarded_without_award_id: int
    non_awarded_with_award_id: int
    dangling_award_id_count: int
    dangling_award_id_samples: list[dict[str, Any]]
    award_without_company_count: int
    award_without_company_samples: list[dict[str, Any]]
    resolved_award_company_count: int
    resolved_awarded_winner_count: int
    shared_award_id_count: int
    shared_award_id_tender_count: int
    shared_award_id_samples: list[dict[str, Any]]
    match_confidence_distribution: dict[str, int]
    entity_role_counts: dict[str, int]
    dataset_hash: str
    schema_version: int = SCHEMA_VERSION
    report_hash: str = field(default="", compare=False)

    def __post_init__(self) -> None:
        if not self.report_hash:
            computed = _stable_hash(
                str(self.inventory_total),
                str(self.eligible_awarded_total),
                str(self.awarded_with_award_id),
                str(self.awarded_without_award_id),
                str(self.non_awarded_with_award_id),
                str(self.dangling_award_id_count),
                str(self.award_without_company_count),
                str(self.resolved_award_company_count),
                str(self.resolved_awarded_winner_count),
                str(self.shared_award_id_count),
                str(self.shared_award_id_tender_count),
                f"schema_version:{self.schema_version}",
                *[
                    f"confidence:{bucket}:{count}"
                    for bucket, count in sorted(
                        self.match_confidence_distribution.items()
                    )
                ],
                *[
                    f"role:{role}:{count}"
                    for role, count in sorted(self.entity_role_counts.items())
                ],
                self.dataset_hash,
            )
            object.__setattr__(self, "report_hash", computed)


@dataclass(frozen=True)
class PathBAuditReport:
    """Path B: self-reported bidder outcome.

    Ambiguous ``tenders.tender_id`` values (shared by more than one tender
    row) are tracked at two distinct severities, because a duplicate ID
    with no evidence attached is a data-quality nuisance, while a
    duplicate ID that already has ``tender_outcomes`` rows referencing it
    means real evidence cannot currently be safely attributed to a single
    tender:

    - ``ambiguous_external_id_distinct_count`` / ``_tender_count`` — every
      duplicate ``tender_id`` value and every tender row participating in
      one, regardless of whether any outcome evidence exists for it.
    - ``ambiguous_external_id_with_outcomes_distinct_count`` — the subset
      of those duplicate IDs for which at least one ``tender_outcomes``
      row exists (always <= ``ambiguous_external_id_distinct_count``).
    - ``ambiguous_outcome_row_count`` — the total ``tender_outcomes`` rows
      attached to any of those with-outcomes duplicate IDs; these rows
      cannot be safely counted into ``outcomes_breakdown`` /
      ``entity_role_counts`` (which are scoped to non-ambiguous IDs only).
    """

    generated_at: str
    inventory_total: int
    tenders_with_valid_external_id: int
    tenders_missing_external_id: int
    ambiguous_external_id_tender_count: int
    ambiguous_external_id_distinct_count: int
    ambiguous_external_id_samples: list[dict[str, Any]]
    ambiguous_external_id_with_outcomes_distinct_count: int
    ambiguous_outcome_row_count: int
    safely_attributable_tenders: int
    tenders_with_reported_bidders: int
    bidder_count_distribution: dict[str, int]
    outcomes_breakdown: dict[str, int]
    dangling_company_id_count: int
    dangling_company_id_samples: list[dict[str, Any]]
    entity_role_counts: dict[str, int]
    dataset_hash: str
    schema_version: int = SCHEMA_VERSION
    report_hash: str = field(default="", compare=False)

    def __post_init__(self) -> None:
        if not self.report_hash:
            computed = _stable_hash(
                str(self.inventory_total),
                str(self.tenders_with_valid_external_id),
                str(self.tenders_missing_external_id),
                str(self.ambiguous_external_id_tender_count),
                str(self.ambiguous_external_id_distinct_count),
                str(self.ambiguous_external_id_with_outcomes_distinct_count),
                str(self.ambiguous_outcome_row_count),
                str(self.safely_attributable_tenders),
                str(self.tenders_with_reported_bidders),
                str(self.dangling_company_id_count),
                f"schema_version:{self.schema_version}",
                *[
                    f"bidders:{bucket}:{count}"
                    for bucket, count in sorted(self.bidder_count_distribution.items())
                ],
                *[
                    f"outcome:{outcome}:{count}"
                    for outcome, count in sorted(self.outcomes_breakdown.items())
                ],
                *[
                    f"role:{role}:{count}"
                    for role, count in sorted(self.entity_role_counts.items())
                ],
                self.dataset_hash,
            )
            object.__setattr__(self, "report_hash", computed)


@dataclass(frozen=True)
class CrossPathAuditReport:
    """Agreement/contradiction between Path A and Path B for the same tender.

    Only tenders with a safely-attributable (non-ambiguous, non-empty)
    external tender_id AND a resolved, *awarded* Path A winner
    (lifecycle_status == 'awarded') are eligible for comparison — a
    resolved award link on a non-awarded tender is not a winner claim and
    is never compared. Ambiguous tenders are excluded and counted
    explicitly, never silently resolved to "the first match."

    ``winner_marked_pending`` is reported but does not, on its own,
    indicate a contradiction — a pending self-reported outcome is simply
    not yet resolved, unlike ``lost``/``withdrew`` against a confirmed
    winner.
    """

    generated_at: str
    comparable_tender_count: int
    ambiguous_excluded_count: int
    same_winner_confirmed_won: int
    different_winner: int
    winner_marked_lost: int
    winner_marked_withdrawn: int
    winner_marked_pending: int
    schema_version: int = SCHEMA_VERSION
    report_hash: str = field(default="", compare=False)

    def __post_init__(self) -> None:
        if not self.report_hash:
            computed = _stable_hash(
                str(self.comparable_tender_count),
                str(self.ambiguous_excluded_count),
                str(self.same_winner_confirmed_won),
                str(self.different_winner),
                str(self.winner_marked_lost),
                str(self.winner_marked_withdrawn),
                str(self.winner_marked_pending),
                f"schema_version:{self.schema_version}",
            )
            object.__setattr__(self, "report_hash", computed)


@dataclass(frozen=True)
class DerivedTenderEvidenceReport:
    """Top-level bundle: Path A + Path B + cross-path, one JSON document."""

    path_a: PathAAuditReport
    path_b: PathBAuditReport
    cross_path: CrossPathAuditReport
    schema_version: int = SCHEMA_VERSION
