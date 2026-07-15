"""Registry Engine Stage 2A — Evidence Link readiness domain types.

Pure data + pure hashing. Nothing here touches a database session.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

EVIDENCE_TYPE_PERMIT = "permit"
EVIDENCE_TYPE_CONTRACT_AWARD = "contract_award"

SOURCE_TABLE_PERMITS = "permits"
SOURCE_TABLE_CONTRACT_AWARDS = "contract_awards"


def _stable_hash(*parts: str) -> str:
    """Deterministic sha256 over pipe-joined, explicitly-ordered parts."""
    joined = "|".join(parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class EvidenceReference:
    """A stable, reproducible reference to one piece of activity evidence.

    ``evidence_type`` (what kind of activity: permit/contract_award) is kept
    distinct from ``source_table`` (the literal DB table) and ``source_system``
    (the originating portal/scraper, e.g. permits.source == "vancouver",
    contract_awards.source == "bc_bid") — these are three different concepts
    that happened to collapse into one field before this revision.

    ``external_id`` is the source system's own identifier, distinct from
    ``internal_id`` (the local DB primary key).

    ``reference_hash`` covers source_table, source_system, external_id,
    internal_id, company_id, and timestamp — every field that identifies
    this specific evidence link.
    """

    evidence_type: str
    source_table: str
    source_system: str
    external_id: str
    internal_id: int
    company_id: int
    timestamp: str  # ISO-8601 scraped_at
    reference_hash: str = field(default="", compare=False)

    def __post_init__(self) -> None:
        if not self.reference_hash:
            computed = _stable_hash(
                self.source_table,
                self.source_system,
                self.external_id,
                str(self.internal_id),
                str(self.company_id),
                self.timestamp,
            )
            object.__setattr__(self, "reference_hash", computed)

    @staticmethod
    def sort_key(ref: "EvidenceReference") -> tuple[str, int, int, str, str]:
        """Full deterministic ordering — every identifying field, not just
        the (already-unique) internal_id, so the order itself is
        self-documenting and immune to any future internal_id reuse policy
        change.
        """
        return (
            ref.source_table,
            ref.internal_id,
            ref.company_id,
            ref.timestamp,
            ref.external_id,
        )


@dataclass(frozen=True)
class CanonicalTargetResult:
    """Result of resolving one company_id target to a canonical company.

    A "broken redirect" is a canonical_company_id pointing at a row that no
    longer exists. A "cycle" is a redirect chain that revisits a company_id
    already seen. "depth_exhausted" is a distinct third failure mode: the
    chain never broke and never cycled, but exceeded MAX_REDIRECT_DEPTH
    without reaching a canonical row — previously indistinguishable from "no
    redirect available at all," which is a different, benign case.
    """

    direct_company_id: int
    direct_entity_role: str
    is_canonical: bool
    resolved_canonical_id: int | None
    redirect_broken: bool
    redirect_cycle: bool
    redirect_depth_exhausted: bool
    excluded: bool


@dataclass(frozen=True)
class EvidenceLinkAuditReport:
    """Read-only audit output for one evidence source (permit or contract_award).

    All counts (orphan/non_canonical/broken/cycle/depth_exhausted/excluded)
    are computed over the FULL dataset via aggregate queries — never derived
    from the bounded samples, which exist purely for human-readable
    illustration in the report.

    ``report_hash`` is a cheap, in-memory fingerprint over summary counts
    plus the bounded reference sample — useful as a fast "did anything
    change" signal, but it is NOT sufficient to prove report integrity,
    since it does not cover the full dataset. ``dataset_hash`` is the
    authoritative fingerprint: a streamed, batched hash over every matching
    row in canonical order, with no sample bound.
    """

    source: str
    generated_at: str
    total_rows: int
    rows_with_company_id: int
    rows_without_company_id: int
    orphan_count: int
    orphan_samples: list[dict[str, Any]]
    non_canonical_count: int
    non_canonical_samples: list[dict[str, Any]]
    broken_redirect_count: int
    cycle_count: int
    depth_exhausted_count: int
    excluded_target_count: int
    reference_sample: list[EvidenceReference]
    dataset_hash: str
    report_hash: str = field(default="", compare=False)

    def __post_init__(self) -> None:
        if not self.report_hash:
            ordered = sorted(self.reference_sample, key=EvidenceReference.sort_key)
            computed = _stable_hash(
                self.source,
                str(self.total_rows),
                str(self.rows_with_company_id),
                str(self.rows_without_company_id),
                str(self.orphan_count),
                str(self.non_canonical_count),
                str(self.broken_redirect_count),
                str(self.cycle_count),
                str(self.depth_exhausted_count),
                str(self.excluded_target_count),
                *[ref.reference_hash for ref in ordered],
            )
            object.__setattr__(self, "report_hash", computed)


@dataclass(frozen=True)
class TenderEvidenceLinkageReport:
    """Reports the tenders.company_id schema gap — does not fix it."""

    total_tenders: int
    has_company_id_column: bool
    schema_gap: bool
    note: str
