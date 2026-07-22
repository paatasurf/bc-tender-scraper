"""Read-only Class-A evidence-review planner for confirmed_conflict
company-classification candidates (PR-MARKET-2C).

Builds on ``pipeline.company_classification_audit`` (PR-MARKET-2B, merged)
without duplicating or changing its conflict-determination logic: the
actual predicates -- ``_name_pattern_conflict`` and ``_trade_tag_conflict``
-- are imported and called verbatim, and the aggregate result this module
returns is built by calling ``audit_company_classification`` itself. The
only new code here is the per-row decision wrapper (``_classify_row``),
which mirrors that function's own per-row loop exactly so a standalone
per-candidate record can be produced; ``test_matches_audit_aggregate_counts``
cross-validates that the two paths always agree, as a regression guard
against this module's copy ever drifting from the audit's own decision.

Purpose: before ANY classification-data fix is ever proposed or applied,
a human reviewer needs to see exactly which companies the audit flagged
``confirmed_conflict`` and why -- but that raw candidate list (company id,
current type, trade, signals, proposed category, rule provenance) must
never end up in a JSON artifact, a file, or an application log, since
those are commonly shared, committed, or shipped to log aggregation.
This module therefore returns two separate things:

- an aggregate result (the audit's own aggregate, plus
  ``review_candidate_count`` and ``review_digest`` -- both artifact-safe:
  no company id, name, URL, address, or payload; ``review_digest`` is
  sensitive to each confirmed_conflict candidate's identity, current
  type, trade, and signals, but never reveals them -- see
  ``compute_review_digest``).
- a list of ``ConflictCandidate`` records, safe ONLY for a human reviewer
  looking at an attended terminal. This module itself never prints,
  logs, or writes them anywhere -- it only returns them; the CLI runner
  (``scripts/run_company_classification_evidence_review.py``) is solely
  responsible for gating their display behind ``--show-candidates`` and
  an attended-TTY check, and for refusing to combine that flag with any
  output-file path.

This module never writes to the database, never calls AI/enrichment/the
scraper/the scheduler, and never changes ``company_type``,
``primary_trade``, or any other Company column. It never proposes or
applies a classification change -- see PR-MARKET-2B's own "next safe
step" note for what happens after a human reviews the evidence this
module surfaces.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import Company
from pipeline.company_classification import GC_TRADE_TYPES
from pipeline.company_classification_audit import (
    LOW_CONFIDENCE_THRESHOLD,
    REVIEW_CONFIRMED_CONFLICT,
    REVIEW_NEEDS_REVIEW,
    REVIEW_NOT_ACTIONABLE,
    SIGNAL_NAME_PATTERN,
    SIGNAL_TRADE_TAG,
    _name_pattern_conflict,  # exact predicate, imported verbatim -- never re-implemented
    _trade_tag_conflict,  # exact predicate, imported verbatim -- never re-implemented
    audit_company_classification,
)

ARTIFACT_SCHEMA_VERSION = 1


class EvidenceReviewError(ValueError):
    """Raised when this module's own internal invariants fail -- should
    never happen, and must never be silently reported as a valid
    aggregate or candidate list if it somehow did."""


@dataclass(frozen=True)
class ConflictCandidate:
    """Attended-terminal-only evidence for one confirmed_conflict company.

    Never serialize this to a file, artifact, or log -- see the module
    docstring. ``company_id`` is the only identifying field here
    (``pipeline.company_classification_audit`` never persists or exposes
    a company name in any of its own output, and this module follows the
    same rule); a reviewer uses it to look the row up manually.
    """

    company_id: int
    company_type: str
    primary_trade: str
    signals: tuple[str, ...]
    proposed_category: str | None
    provenance: tuple[str, ...]


def _classify_row(
    *,
    company_type: str | None,
    primary_trade: str | None,
    name: str | None,
    confidence_score: float | None,
) -> tuple[str, list[str], str | None]:
    """Per-row decision, mirroring
    ``audit_company_classification``'s own per-row loop exactly -- calls
    only the imported ``_name_pattern_conflict``/``_trade_tag_conflict``
    predicates (never re-implemented), and applies the identical
    signals-fired / low-confidence / not-actionable branching. Kept here
    only because ``audit_company_classification`` does not itself expose
    a standalone per-row classify step; see
    ``test_matches_audit_aggregate_counts`` for the cross-validation
    guard against this ever drifting from the audit's own counts.
    """
    signals: list[str] = []
    proposed_category: str | None = None

    name_hit = _name_pattern_conflict(name or "")
    if name_hit is not None:
        signals.append(SIGNAL_NAME_PATTERN)
        proposed_category = name_hit

    trade_hit = _trade_tag_conflict(primary_trade or "")
    if trade_hit is not None:
        signals.append(SIGNAL_TRADE_TAG)
        if proposed_category is None:
            proposed_category = trade_hit

    if signals:
        review_category = REVIEW_CONFIRMED_CONFLICT
    elif (
        confidence_score is not None
        and float(confidence_score) < LOW_CONFIDENCE_THRESHOLD
    ):
        review_category = REVIEW_NEEDS_REVIEW
    else:
        review_category = REVIEW_NOT_ACTIONABLE

    return review_category, signals, proposed_category


def _build_provenance(
    *,
    signals: list[str],
    company_type: str,
    primary_trade: str,
    proposed_category: str | None,
) -> tuple[str, ...]:
    """Human-readable explanation of why a row is a confirmed_conflict --
    only references the rule/signal names and the row's own already-
    displayed fields (company_type, primary_trade, proposed_category),
    never the company name."""
    lines: list[str] = []
    if SIGNAL_NAME_PATTERN in signals:
        lines.append(
            "name_pattern_conflict: Company.name matches an already-deployed "
            "professional-services name pattern in pipeline.company_classification "
            "(a KNOWN_FIRMS entry categorized Architect/Engineering Firm/Building "
            "Code Consultant, or a CLASSIFICATION_RULES rule in one of those same "
            f"groups); proposed category = {proposed_category!r}."
        )
    if SIGNAL_TRADE_TAG in signals:
        lines.append(
            f"trade_tag_conflict: Company.primary_trade={primary_trade!r} is a "
            "professional-services trade (engineering/architecture/consulting) "
            f"while Company.company_type={company_type!r} is General Contractor "
            f"or Trade Contractor; proposed category = {proposed_category!r}."
        )
    return tuple(lines)


def compute_review_digest(candidates: list[ConflictCandidate]) -> str:
    """Stable SHA-256 digest sensitive to every confirmed_conflict
    candidate's identity, current type, trade, and signals -- changes if
    any of those changes for any candidate -- but never reveals them: each
    candidate is hashed individually first (so no raw field is ever
    concatenated into the final digest input directly), the per-candidate
    hashes are sorted for order-independence, then hashed again."""
    per_candidate_digests = sorted(
        hashlib.sha256(
            f"{c.company_id}|{c.company_type}|{c.primary_trade}|{','.join(sorted(c.signals))}".encode(
                "utf-8"
            )
        ).hexdigest()
        for c in candidates
    )
    blob = ",".join(per_candidate_digests)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _validate_sample_size(sample_size: int | None) -> None:
    if sample_size is None:
        return
    if type(sample_size) is not int or sample_size < 0:
        raise EvidenceReviewError(
            f"sample_size must be a non-negative int or None, got {sample_size!r}"
        )


def build_evidence_review(
    session: Session,
    *,
    sample_size: int | None = None,
) -> tuple[dict[str, Any], list[ConflictCandidate]]:
    """Returns ``(aggregate_result, confirmed_conflict_candidates)``.

    ``aggregate_result`` is ``audit_company_classification``'s own
    aggregate (unmodified in shape or values) plus ``review_candidate_count``
    and ``review_digest`` -- safe for any artifact/file/log.

    ``confirmed_conflict_candidates`` is a list of ``ConflictCandidate``,
    deterministically ordered by ``Company.id`` ascending (the audit's own
    canonical ordering) -- safe ONLY for an attended terminal. This
    function itself never prints, logs, or persists them; see the module
    docstring for the caller's obligations.

    Raises ``EvidenceReviewError`` if ``review_candidate_count`` ever
    disagrees with the audit's own ``counts["confirmed_conflict"]`` --
    the two must always agree, since both are derived from the same
    predicates over the same row set.
    """
    _validate_sample_size(sample_size)

    aggregate = audit_company_classification(session, sample_size=sample_size)

    query = (
        select(
            Company.id,
            Company.name,
            Company.company_type,
            Company.primary_trade,
            Company.confidence_score,
        )
        .where(Company.company_type.in_(tuple(GC_TRADE_TYPES)))
        .order_by(Company.id)
    )
    if sample_size is not None:
        query = query.limit(sample_size)

    candidates: list[ConflictCandidate] = []
    for (
        company_id,
        name,
        company_type,
        primary_trade,
        confidence_score,
    ) in session.execute(query).all():
        review_category, signals, proposed_category = _classify_row(
            company_type=company_type,
            primary_trade=primary_trade,
            name=name,
            confidence_score=confidence_score,
        )
        if review_category != REVIEW_CONFIRMED_CONFLICT:
            continue
        candidates.append(
            ConflictCandidate(
                company_id=int(company_id),
                company_type=company_type or "",
                primary_trade=primary_trade or "",
                signals=tuple(signals),
                proposed_category=proposed_category,
                provenance=_build_provenance(
                    signals=signals,
                    company_type=company_type or "",
                    primary_trade=primary_trade or "",
                    proposed_category=proposed_category,
                ),
            )
        )

    if len(candidates) != aggregate["counts"][REVIEW_CONFIRMED_CONFLICT]:
        raise EvidenceReviewError(
            "review_candidate_count disagrees with the audit's own "
            f"confirmed_conflict count: {len(candidates)} vs "
            f"{aggregate['counts'][REVIEW_CONFIRMED_CONFLICT]}"
        )

    review_digest = compute_review_digest(candidates)
    aggregate_with_review = {
        **aggregate,
        "review_candidate_count": len(candidates),
        "review_digest": review_digest,
    }
    return aggregate_with_review, candidates
