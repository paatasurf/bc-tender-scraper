"""Read-only Company classification truth audit (PR-MARKET-2B, Class A).

Reports, in aggregate only, how many currently GC/Trade-Contractor-tagged
``Company`` rows -- the population that feeds the construction Market
cohort's GC/builder allowlist (``pipeline.competitive_intel.cohort_isolation``)
and therefore ``Top competitors`` -- carry a deterministic contradiction
against other data already stored on that same row, without ever writing
anything, calling AI/enrichment/the scraper/the scheduler, or fuzzy-matching.

Classification data flow this audit inspects (traced, not altered):

    Company.name / Company.ai_summary / permit stats
        -> pipeline.company_classification.classify_business_type()
        -> Company.company_type, Company.confidence_score
        -> pipeline.cip_builder.build_cip() copies Company.company_type
           verbatim into CompanyIntelligenceProfile.company_type
           (construction branch: ``company_type = company.company_type or ""``)
        -> pipeline.competitive_intel.cohort_isolation.is_gc_builder_profile()
           / is_allowed_gc_cohort_member() gate the construction Market
           cohort using that same company_type
        -> Top competitors / Potential opportunity gaps / Competitor
           fit-scoring coverage (pipeline.competitive_intel.service /
           peers / tender_activity)

Two signals, both a direct comparison between two pieces of data that are
already stored on the row -- neither is a new guess, and neither touches
``Company.ai_summary`` (a free-text AI-generated field, which this audit
deliberately never text-matches, to stay clear of anything resembling
fuzzy/AI classification):

1. ``name_pattern_conflict``: ``Company.name`` matches one of the
   *already-deployed, already-in-production* deterministic name-pattern
   rules in ``pipeline.company_classification`` that would classify it as
   a professional-services firm. ``KNOWN_FIRMS`` is NOT used wholesale --
   only its entries whose own already-assigned category is Architect,
   Engineering Firm, or Building Code Consultant are considered (see
   ``_PROFESSIONAL_SERVICES_KNOWN_FIRMS``); a ``KNOWN_FIRMS`` entry
   categorized General Contractor or Trade Contractor is never a
   professional-services signal, no matter how it is named, and must
   never contribute to ``confirmed_conflict`` on its own. The same
   restriction applies to ``CLASSIFICATION_RULES``: only its Architect /
   Engineering Firm / Building Code Consultant rule groups are considered
   (see ``_PROFESSIONAL_SERVICES_RULES``). Both are reused verbatim, never
   re-implemented or loosened here, and checked only against the raw name
   (no CamelCase/DBA normalization is replicated, so this signal is
   deliberately conservative -- it can under-flag but never invents a new
   pattern).
2. ``trade_tag_conflict``: ``Company.primary_trade`` -- populated by a
   *separate* deterministic tagger (``pipeline.taxonomy.tag_company`` /
   ``pipeline.cip_builder._resolve_trades``) from permit/award/project-type
   text, independently of ``classify_business_type`` -- is one of
   ``engineering`` / ``architecture`` / ``consulting`` while
   ``Company.company_type`` is ``General Contractor`` or ``Trade
   Contractor``. Two independently-derived, already-persisted columns
   disagreeing is itself the signal; nothing new is computed about the
   company.

Either signal firing is a ``confirmed_conflict``: a real number is being
consulted, both sides state the same expectation and disagree.

Absent either signal, a currently GC/Trade-Contractor-tagged row whose own
``Company.confidence_score`` (set by ``classify_business_type`` at
classification time) is below ``LOW_CONFIDENCE_THRESHOLD`` is a
``needs_review`` candidate: the classifier itself was not confident, but
there is no deterministic contradiction to point to.

Everything else is ``not_actionable`` and is excluded from every
candidate count -- only ``counts["total_scanned"]`` reflects it.

This module never mutates a row, never calls ``session.add``/``commit``,
never invokes AI/enrichment/the scraper/the scheduler, and never returns a
company id, name, or any other identifying value -- only aggregate counts,
small closed-vocabulary category breakdowns (the eight
``pipeline.company_classification.MARKET_CATEGORIES`` values and the two
signal names above), and a SHA-256 digest over the examined id set (the
same pattern as ``pipeline.surrey_resolution_audit.compute_examined_digest``
-- proves *that* a row-set was examined without ever serializing it).
"""

from __future__ import annotations

import hashlib
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import Company
from pipeline.company_classification import (
    CLASSIFICATION_RULES,
    GC_TRADE_TYPES,
    KNOWN_FIRMS,
    to_market_category,
)

ARTIFACT_SCHEMA_VERSION = 1

REVIEW_CONFIRMED_CONFLICT = "confirmed_conflict"
REVIEW_NEEDS_REVIEW = "needs_review"
REVIEW_NOT_ACTIONABLE = "not_actionable"
REVIEW_CATEGORIES = (
    REVIEW_CONFIRMED_CONFLICT,
    REVIEW_NEEDS_REVIEW,
    REVIEW_NOT_ACTIONABLE,
)

SIGNAL_NAME_PATTERN = "name_pattern_conflict"
SIGNAL_TRADE_TAG = "trade_tag_conflict"

# Internal categories (pipeline.company_classification's own vocabulary)
# whose name-pattern rules identify a professional-services firm, not a
# construction contractor.
_PROFESSIONAL_SERVICES_INTERNAL_CATEGORIES = frozenset(
    {"Architect", "Engineering Firm", "Building Code Consultant"}
)

# Company.primary_trade values (pipeline.taxonomy.TRADES) that are
# professional-services trades, not construction trades.
_PROFESSIONAL_SERVICES_TRADES = frozenset({"engineering", "architecture", "consulting"})

_TRADE_TO_MARKET_CATEGORY: dict[str, str] = {
    "engineering": "Engineering",
    "architecture": "Architect",
    "consulting": "Consultant",
}

LOW_CONFIDENCE_THRESHOLD = 0.5


class CompanyClassificationAuditError(ValueError):
    """Raised when this module's own internal count invariants fail, or
    when its own professional-services filters admit a category they
    must not -- should never happen, and must never be silently reported
    as a valid aggregate if it somehow did."""


_PROFESSIONAL_SERVICES_RULES = [
    rule
    for rule in CLASSIFICATION_RULES
    if rule["category"] in _PROFESSIONAL_SERVICES_INTERNAL_CATEGORIES
]
_PROFESSIONAL_SERVICES_KNOWN_FIRMS = [
    (pattern, category)
    for pattern, category in KNOWN_FIRMS
    if category in _PROFESSIONAL_SERVICES_INTERNAL_CATEGORIES
]

# Fail-closed at import time: KNOWN_FIRMS/CLASSIFICATION_RULES must never
# be used wholesale as a professional-services signal. If either filtered
# subset above ever admits a General Contractor/Trade Contractor (or any
# other non-professional-services) category -- e.g. a future edit to
# _PROFESSIONAL_SERVICES_INTERNAL_CATEGORIES accidentally widens it --
# this raises immediately rather than silently letting a GC/Trade
# Contractor entry contribute to confirmed_conflict.
for _pattern, _category in _PROFESSIONAL_SERVICES_KNOWN_FIRMS:
    if _category not in _PROFESSIONAL_SERVICES_INTERNAL_CATEGORIES:
        raise CompanyClassificationAuditError(
            f"KNOWN_FIRMS filter admitted a non-professional-services "
            f"category: {_category!r}"
        )
for _rule in _PROFESSIONAL_SERVICES_RULES:
    if _rule["category"] not in _PROFESSIONAL_SERVICES_INTERNAL_CATEGORIES:
        raise CompanyClassificationAuditError(
            f"CLASSIFICATION_RULES filter admitted a non-professional-"
            f"services category: {_rule['category']!r}"
        )
del _pattern, _category, _rule


def _name_pattern_conflict(name: str) -> str | None:
    """Return the proposed market category if ``name`` matches an
    already-deployed professional-services name-pattern rule; ``None``
    otherwise. Pure regex match against the raw name -- no DB access, no
    text generation, no AI."""
    raw = name or ""
    for pattern, category in _PROFESSIONAL_SERVICES_KNOWN_FIRMS:
        if pattern.search(raw):
            return to_market_category(category)
    for rule in _PROFESSIONAL_SERVICES_RULES:
        if any(exclude.search(raw) for exclude in rule["exclude"]):
            continue
        if any(pattern.search(raw) for pattern in rule["patterns"]):
            return to_market_category(rule["category"])
    return None


def _trade_tag_conflict(primary_trade: str) -> str | None:
    trade = (primary_trade or "").strip().lower()
    if trade in _PROFESSIONAL_SERVICES_TRADES:
        return _TRADE_TO_MARKET_CATEGORY[trade]
    return None


def _validate_sample_size(sample_size: int | None) -> None:
    if sample_size is None:
        return
    if type(sample_size) is not int or sample_size < 0:
        raise CompanyClassificationAuditError(
            f"sample_size must be a non-negative int or None, got {sample_size!r}"
        )


def compute_examined_digest(company_ids: list[int]) -> str:
    """SHA-256 hex digest over the sorted, de-duplicated set of examined
    ``Company.id`` values -- proves *that* a particular row-set was
    examined without ever serializing the set itself."""
    ordered = sorted(set(company_ids))
    blob = ",".join(str(company_id) for company_id in ordered)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def audit_company_classification(
    session: Session,
    *,
    sample_size: int | None = None,
) -> dict[str, Any]:
    """Single read-only pass over ``Company`` rows currently tagged
    ``General Contractor`` or ``Trade Contractor`` -- the population that
    feeds the construction Market cohort -- deterministically ordered by
    ``Company.id`` ascending (``LIMIT sample_size`` when given).

    Never issues a write, never calls AI/enrichment/the scraper/the
    scheduler. Never returns a company id, name, or any other identifying
    value -- only aggregate counts, small closed-vocabulary breakdowns,
    and a content digest over the examined id set.

    ``counts["confirmed_conflict"] + counts["needs_review"] +
    counts["not_actionable"] == counts["total_scanned"]`` always holds, or
    ``CompanyClassificationAuditError`` is raised rather than ever
    returning an inconsistent aggregate.
    """
    _validate_sample_size(sample_size)

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

    counts: dict[str, int] = {"total_scanned": 0}
    for category in REVIEW_CATEGORIES:
        counts[category] = 0
    signal_histogram: dict[str, int] = {SIGNAL_NAME_PATTERN: 0, SIGNAL_TRADE_TAG: 0}
    candidates_by_current_type: dict[str, int] = {}
    candidates_by_review_category: dict[str, int] = {}
    examined_ids: list[int] = []

    for (
        company_id,
        name,
        company_type,
        primary_trade,
        confidence_score,
    ) in session.execute(query).all():
        examined_ids.append(int(company_id))
        counts["total_scanned"] += 1

        signals_fired: list[str] = []
        proposed_category: str | None = None

        name_hit = _name_pattern_conflict(name or "")
        if name_hit is not None:
            signals_fired.append(SIGNAL_NAME_PATTERN)
            proposed_category = name_hit

        trade_hit = _trade_tag_conflict(primary_trade or "")
        if trade_hit is not None:
            signals_fired.append(SIGNAL_TRADE_TAG)
            if proposed_category is None:
                proposed_category = trade_hit

        if signals_fired:
            review_category = REVIEW_CONFIRMED_CONFLICT
        elif (
            confidence_score is not None
            and float(confidence_score) < LOW_CONFIDENCE_THRESHOLD
        ):
            review_category = REVIEW_NEEDS_REVIEW
        else:
            review_category = REVIEW_NOT_ACTIONABLE

        if review_category not in counts:
            raise CompanyClassificationAuditError(
                f"unrecognized review category: {review_category!r}"
            )
        counts[review_category] += 1

        for signal in signals_fired:
            if signal not in signal_histogram:
                raise CompanyClassificationAuditError(
                    f"unrecognized signal: {signal!r}"
                )
            signal_histogram[signal] += 1

        if review_category != REVIEW_NOT_ACTIONABLE:
            current_type_key = company_type or "Unknown"
            candidates_by_current_type[current_type_key] = (
                candidates_by_current_type.get(current_type_key, 0) + 1
            )
            review_key = f"{review_category}:{proposed_category or 'unspecified'}"
            candidates_by_review_category[review_key] = (
                candidates_by_review_category.get(review_key, 0) + 1
            )

    review_total = sum(counts[category] for category in REVIEW_CATEGORIES)
    if review_total != counts["total_scanned"]:
        raise CompanyClassificationAuditError(
            "review-category counts do not sum to total_scanned"
        )

    candidates_with_conflicting_signals = (
        counts[REVIEW_CONFIRMED_CONFLICT] + counts[REVIEW_NEEDS_REVIEW]
    )

    return {
        "counts": counts,
        "candidates_with_conflicting_signals": candidates_with_conflicting_signals,
        "signal_histogram": signal_histogram,
        "candidates_by_current_type": dict(sorted(candidates_by_current_type.items())),
        "candidates_by_review_category": dict(
            sorted(candidates_by_review_category.items())
        ),
        "examined_count": len(examined_ids),
        "digest": compute_examined_digest(examined_ids),
    }
