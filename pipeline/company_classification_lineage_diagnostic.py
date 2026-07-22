"""Read-only Class-A single-company classification lineage diagnostic
(PR-MARKET-2D).

Traces exactly why one named company ended up with its current
``company_type`` and whether it is currently eligible for the
construction Market GC/Trade-Contractor cohort -- built to answer the
Read Jones Christoffersen Ltd. / RJC case, but reusable for any company
identity.

Never assumes a UI-supplied numeric profile id equals ``Company.id`` --
the ONLY way to target a company here is
``resolve_company_by_exact_identity``, an exact (never fuzzy, never
normalized, never alias-collapsing) string match against ``Company.name``
or ``Company.display_name``. Zero matches or more than one match fails
closed (``not_found``/``ambiguous``) -- this module never guesses which
row was meant.

Once (and only if) exactly one company is resolved, this module traces,
read-only:

- Company classification fields: ``entity_role``, ``company_type``,
  ``confidence_score``, ``primary_trade``, ``dominant_sector``.
- The relevant CompanyIntelligenceProfile fields (``company_type``,
  ``entity_class``, ``primary_trade``), built via
  ``pipeline.cip_builder.build_cip`` -- deliberately NOT ``get_cip``,
  which can call ``persist_cip`` and mutate the Company row's cached CIP
  columns; ``build_cip`` alone only reads and computes, never writes.
- Which classification rule path applied: calls
  ``pipeline.company_classification.classify_business_type`` (the exact,
  unmodified production function) on the row's current stored fields and
  reports its own ``method``/``internal_category``/``market_category``/
  ``confidence`` verbatim -- never re-implemented.
- Which classification rule paths did NOT apply but could have: every
  ``KNOWN_FIRMS`` entry and every ``CLASSIFICATION_RULES`` category whose
  pattern the name matches (not just the first, priority-ordered one
  ``classify_business_type`` itself uses) -- both reused verbatim from
  ``pipeline.company_classification``.
- The confirmed_conflict/needs_review/not_actionable signal this company
  would receive from ``pipeline.company_classification_audit`` (PR-MARKET-
  2B) -- reuses its exact ``_name_pattern_conflict``/``_trade_tag_conflict``
  predicates verbatim, never re-implemented or loosened.
- Cohort eligibility: the subject-independent portion of the construction
  Market cohort pipeline -- the SQL-equivalent entity-role analytics
  filter, the person-name post-filter
  (``pipeline.competitive_intel.cohort.filter_construction_peer_pool``),
  and the GC-cohort-isolation allowlist check
  (``pipeline.competitive_intel.cohort_isolation.is_allowed_gc_cohort_member``)
  -- all reused verbatim. The subject-relative quality gate
  (``_passes_cohort_quality_gate``) is deliberately NOT evaluated here:
  it compares the company against a specific subject's own sector/trade/
  project-count profile, so there is no single subject-independent answer
  for "would this company appear in cohort X" -- only whether it clears
  the checks that do not depend on who is looking.

This module never writes to the database, never calls AI/enrichment/the
scraper/the scheduler, never changes ``company_type``, ``KNOWN_FIRMS``,
cohort logic, or scoring, and proposes no classification change.

Two-part output, same split as ``pipeline.company_classification_evidence_review``
(PR-MARKET-2C/2C1):

- an aggregate result -- resolution status, candidate count, review
  category, small closed-vocabulary counts, boolean cohort checks, and a
  ``digest`` sensitive to (but never revealing) the full evidence --
  artifact-safe: no company id, name, URL, address, or payload.
- a ``LineageEvidence`` record -- INCLUDING the company's name and id --
  safe ONLY for a human reviewer at an attended terminal. This module
  itself never prints, logs, or writes it anywhere; the CLI runner
  (``scripts/run_company_classification_lineage_diagnostic.py``) is
  solely responsible for gating its display behind ``--show-evidence``
  and an attended-TTY check, and for refusing to combine that flag with
  any output-file path.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from db.company_canonical_constants import COMPANY_ANALYTICS_EXCLUDED_ENTITY_ROLES
from db.models import Company
from pipeline.cip_builder import build_cip
from pipeline.company_classification import (
    CLASSIFICATION_RULES,
    GC_TRADE_TYPES,
    KNOWN_FIRMS,
    classify_business_type,
    compute_permit_stats_24mo,
)
from pipeline.company_classification_audit import (
    LOW_CONFIDENCE_THRESHOLD,
    REVIEW_CONFIRMED_CONFLICT,
    REVIEW_NEEDS_REVIEW,
    REVIEW_NOT_ACTIONABLE,
    SIGNAL_NAME_PATTERN,
    SIGNAL_TRADE_TAG,
    _name_pattern_conflict,  # exact predicate, imported verbatim -- never re-implemented
    _trade_tag_conflict,  # exact predicate, imported verbatim -- never re-implemented
)
from pipeline.competitive_intel.cohort import filter_construction_peer_pool
from pipeline.competitive_intel.cohort_isolation import is_allowed_gc_cohort_member

ARTIFACT_SCHEMA_VERSION = 1

RESOLUTION_RESOLVED = "resolved"
RESOLUTION_AMBIGUOUS = "ambiguous"
RESOLUTION_NOT_FOUND = "not_found"
RESOLUTION_STATUSES = (RESOLUTION_RESOLVED, RESOLUTION_AMBIGUOUS, RESOLUTION_NOT_FOUND)


class LineageDiagnosticError(ValueError):
    """Raised when this module's own internal invariants fail, or its
    input contract is violated (empty identity) -- should never happen,
    and must never be silently reported as a valid result if it somehow
    did."""


@dataclass(frozen=True)
class ResolutionResult:
    """``company_id`` is set ONLY when ``status == "resolved"`` and is an
    identifying value -- attended-terminal-only, same rule as
    ``LineageEvidence``."""

    status: str
    candidate_count: int
    company_id: int | None


@dataclass(frozen=True)
class LineageEvidence:
    """Attended-terminal-only evidence for one resolved company.

    NEVER serialize this to a file, artifact, or log -- see the module
    docstring. Only the CLI's ``--show-evidence`` attended-terminal path
    may ever display it.
    """

    company_id: int
    company_name: str
    display_name: str
    entity_role: str
    company_type: str
    confidence_score: float | None
    primary_trade: str
    dominant_sector: str
    cip_company_type: str
    cip_entity_class: str
    cip_primary_trade: str
    classification_method: str
    classification_internal_category: str
    classification_market_category: str
    classification_confidence: float
    known_firms_match_category: str | None
    matching_rule_categories: tuple[str, ...]
    review_category: str
    conflict_signals: tuple[str, ...]
    passes_entity_analytics_filter: bool
    passes_person_name_filter: bool
    passes_gc_cohort_isolation_allowlist: bool | None
    provenance: tuple[str, ...]


def resolve_company_by_exact_identity(
    session: Session, identity: str
) -> ResolutionResult:
    """Exact-only resolution -- never fuzzy, never normalizes, never
    collapses an applicant alias to a canonical row. Literal string
    equality against ``Company.name`` OR ``Company.display_name``, using
    ``identity`` completely unmodified -- no ``.strip()``, ``.lower()``,
    or any other transformation is ever applied to the lookup value
    itself, since a leading/trailing-space-only difference from the
    stored row is a genuine non-match, not something this exact-only
    resolver may paper over. ``.strip()`` is used ONLY to validate that
    ``identity`` is not empty/whitespace-only (which fails closed before
    any query), never to build the query. A UI-supplied numeric id is
    never accepted or trusted directly by this module; this is the ONLY
    way to target a company. Zero or more than one distinct matching id
    fails closed."""
    if not (identity or "").strip():
        raise LineageDiagnosticError("identity must be a non-empty string")

    rows = session.execute(
        select(Company.id).where(
            or_(Company.name == identity, Company.display_name == identity)
        )
    ).all()
    ids = sorted({int(row[0]) for row in rows})

    if len(ids) == 0:
        return ResolutionResult(
            status=RESOLUTION_NOT_FOUND, candidate_count=0, company_id=None
        )
    if len(ids) > 1:
        return ResolutionResult(
            status=RESOLUTION_AMBIGUOUS, candidate_count=len(ids), company_id=None
        )
    return ResolutionResult(
        status=RESOLUTION_RESOLVED, candidate_count=1, company_id=ids[0]
    )


def _known_firms_match(name: str) -> str | None:
    """Which KNOWN_FIRMS category (if any) the raw name matches -- every
    category, not filtered to professional-services only (unlike
    pipeline.company_classification_audit's own use of this table), since
    this is a full lineage trace, not a conflict-signal check. Reuses
    KNOWN_FIRMS verbatim."""
    raw = name or ""
    for pattern, category in KNOWN_FIRMS:
        if pattern.search(raw):
            return category
    return None


def _matching_rule_categories(name: str) -> tuple[str, ...]:
    """Every CLASSIFICATION_RULES category whose patterns match the raw
    name -- not just the first, priority-ordered match
    classify_business_type() itself stops at -- showing which alternative
    categories the name could also have matched. Reuses
    CLASSIFICATION_RULES verbatim."""
    raw = name or ""
    matched: list[str] = []
    for rule in CLASSIFICATION_RULES:
        if any(exclude.search(raw) for exclude in rule["exclude"]):
            continue
        if any(pattern.search(raw) for pattern in rule["patterns"]):
            matched.append(rule["category"])
    return tuple(matched)


def _build_provenance(
    *,
    classification_method: str,
    classification_market_category: str,
    known_firms_match_category: str | None,
    matching_rule_categories: tuple[str, ...],
    review_category: str,
    conflict_signals: tuple[str, ...],
    passes_entity_analytics_filter: bool,
    passes_person_name_filter: bool,
    passes_gc_cohort_isolation_allowlist: bool | None,
) -> tuple[str, ...]:
    lines: list[str] = [
        f"classify_business_type() selected method={classification_method!r}, "
        f"market_category={classification_market_category!r}.",
    ]
    if known_firms_match_category is not None:
        lines.append(
            f"Company.name matches a KNOWN_FIRMS entry categorized "
            f"{known_firms_match_category!r}."
        )
    else:
        lines.append("Company.name matches no KNOWN_FIRMS entry.")
    if matching_rule_categories:
        lines.append(
            "Company.name matches CLASSIFICATION_RULES pattern group(s): "
            + ", ".join(matching_rule_categories)
            + "."
        )
    else:
        lines.append("Company.name matches no CLASSIFICATION_RULES pattern group.")
    lines.append(
        f"pipeline.company_classification_audit review_category="
        f"{review_category!r} (signals={list(conflict_signals)})."
    )
    lines.append(
        "Cohort eligibility (subject-independent checks only): "
        f"entity_analytics_filter={passes_entity_analytics_filter}, "
        f"person_name_filter={passes_person_name_filter}, "
        f"gc_cohort_isolation_allowlist={passes_gc_cohort_isolation_allowlist!r}."
    )
    return tuple(lines)


def compute_lineage_digest(
    resolution: ResolutionResult, evidence: LineageEvidence | None
) -> str:
    """Stable SHA-256 digest sensitive to the resolution status/candidate
    count and, when resolved, every evidence field -- but never reveals
    them, mirroring
    pipeline.company_classification_evidence_review.compute_review_digest's
    pattern."""
    if evidence is None:
        blob = f"{resolution.status}|{resolution.candidate_count}"
    else:
        blob = "|".join(
            [
                str(evidence.company_id),
                evidence.company_name,
                evidence.display_name,
                evidence.entity_role,
                evidence.company_type,
                str(evidence.confidence_score),
                evidence.primary_trade,
                evidence.dominant_sector,
                evidence.cip_company_type,
                evidence.cip_entity_class,
                evidence.cip_primary_trade,
                evidence.classification_method,
                evidence.classification_internal_category,
                evidence.classification_market_category,
                str(evidence.classification_confidence),
                evidence.known_firms_match_category or "",
                ",".join(sorted(evidence.matching_rule_categories)),
                evidence.review_category,
                ",".join(sorted(evidence.conflict_signals)),
                str(evidence.passes_entity_analytics_filter),
                str(evidence.passes_person_name_filter),
                str(evidence.passes_gc_cohort_isolation_allowlist),
            ]
        )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def build_lineage_diagnostic(
    session: Session,
    *,
    identity: str,
) -> tuple[dict[str, Any], LineageEvidence | None]:
    """Returns ``(aggregate_result, evidence)``.

    ``aggregate_result`` is artifact-safe: resolution status, candidate
    count, review category, small closed-vocabulary counts, boolean
    cohort checks, and a digest -- no company id, name, URL, address, or
    payload.

    ``evidence`` is ``None`` unless resolution status is ``"resolved"``;
    when present it is safe ONLY for an attended terminal -- see the
    module docstring for the caller's obligations. This function itself
    never prints, logs, or persists it.
    """
    resolution = resolve_company_by_exact_identity(session, identity)

    if resolution.status != RESOLUTION_RESOLVED:
        aggregate: dict[str, Any] = {
            "resolution_status": resolution.status,
            "candidate_count": resolution.candidate_count,
            "review_category": None,
            "signal_histogram": None,
            "known_firms_match_count": None,
            "matching_rule_category_count": None,
            "cohort_checks": None,
            "digest": compute_lineage_digest(resolution, None),
        }
        return aggregate, None

    company = session.get(Company, resolution.company_id)
    if company is None:
        raise LineageDiagnosticError(
            f"resolved company_id vanished before load inside this read-only "
            f"transaction -- should never happen (candidate_count="
            f"{resolution.candidate_count})"
        )

    stats24 = compute_permit_stats_24mo(session)
    company_stats = stats24.get(
        company.name or "", {"permit_count_24mo": 0, "permit_value_24mo": 0.0}
    )
    classification = classify_business_type(company, company_stats)

    cip = build_cip(session, company_id=company.id, kind="construction")

    known_firms_category = _known_firms_match(company.name or "")
    matching_rule_categories = _matching_rule_categories(company.name or "")

    name_hit = _name_pattern_conflict(company.name or "")
    trade_hit = _trade_tag_conflict(company.primary_trade or "")
    signals: list[str] = []
    if name_hit is not None:
        signals.append(SIGNAL_NAME_PATTERN)
    if trade_hit is not None:
        signals.append(SIGNAL_TRADE_TAG)
    if signals:
        review_category = REVIEW_CONFIRMED_CONFLICT
    elif (
        company.confidence_score is not None
        and float(company.confidence_score) < LOW_CONFIDENCE_THRESHOLD
    ):
        review_category = REVIEW_NEEDS_REVIEW
    else:
        review_category = REVIEW_NOT_ACTIONABLE

    passes_entity_filter = (
        company.entity_role or ""
    ) not in COMPANY_ANALYTICS_EXCLUDED_ENTITY_ROLES
    passes_person_filter = len(filter_construction_peer_pool([company])) == 1
    passes_gc_allowlist = (
        is_allowed_gc_cohort_member(company, session=session)
        if (company.company_type or "") in GC_TRADE_TYPES
        else None
    )

    provenance = _build_provenance(
        classification_method=classification.method,
        classification_market_category=classification.market_category,
        known_firms_match_category=known_firms_category,
        matching_rule_categories=matching_rule_categories,
        review_category=review_category,
        conflict_signals=tuple(signals),
        passes_entity_analytics_filter=passes_entity_filter,
        passes_person_name_filter=passes_person_filter,
        passes_gc_cohort_isolation_allowlist=passes_gc_allowlist,
    )

    evidence = LineageEvidence(
        company_id=int(company.id),
        company_name=company.name or "",
        display_name=company.display_name or "",
        entity_role=company.entity_role or "",
        company_type=company.company_type or "",
        confidence_score=company.confidence_score,
        primary_trade=company.primary_trade or "",
        dominant_sector=company.dominant_sector or "",
        cip_company_type=cip.company_type,
        cip_entity_class=cip.entity_class,
        cip_primary_trade=cip.primary_trade,
        classification_method=classification.method,
        classification_internal_category=classification.internal_category,
        classification_market_category=classification.market_category,
        classification_confidence=classification.confidence,
        known_firms_match_category=known_firms_category,
        matching_rule_categories=matching_rule_categories,
        review_category=review_category,
        conflict_signals=tuple(signals),
        passes_entity_analytics_filter=passes_entity_filter,
        passes_person_name_filter=passes_person_filter,
        passes_gc_cohort_isolation_allowlist=passes_gc_allowlist,
        provenance=provenance,
    )

    aggregate = {
        "resolution_status": resolution.status,
        "candidate_count": resolution.candidate_count,
        "review_category": review_category,
        "signal_histogram": {
            SIGNAL_NAME_PATTERN: int(SIGNAL_NAME_PATTERN in signals),
            SIGNAL_TRADE_TAG: int(SIGNAL_TRADE_TAG in signals),
        },
        "known_firms_match_count": int(known_firms_category is not None),
        "matching_rule_category_count": len(matching_rule_categories),
        "cohort_checks": {
            "passes_entity_analytics_filter": passes_entity_filter,
            "passes_person_name_filter": passes_person_filter,
            "passes_gc_cohort_isolation_allowlist": passes_gc_allowlist,
        },
        "digest": compute_lineage_digest(resolution, evidence),
    }

    return aggregate, evidence
