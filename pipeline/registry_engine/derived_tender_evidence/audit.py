"""Derived Tender Evidence Link Readiness Audit — Path A / Path B / cross-path.

Read-only. Every query in this module is a SELECT (via SQLAlchemy Core
``select()``), executed through ``session.execute()``/``session.scalar()``.
Nothing here calls ``session.add``, ``session.merge``, ``session.delete``,
``session.flush``, or ``session.commit`` — there is nothing to write.

This module does not add ``tenders.company_id``, does not create any FK or
index, and does not modify ``pipeline/awards_reconciler.py`` or the Stage 2A
Evidence Link audit (``pipeline/registry_engine/evidence``). It only reads
two paths that already exist in the schema, unenforced:

- Path A: ``tenders.award_id -> contract_awards.id -> contract_awards.company_id``
  (algorithmic, populated by ``pipeline/awards_reconciler.py``).
- Path B: ``tenders.tender_id -> tender_outcomes.tender_id -> tender_outcomes.company_id``
  (human-asserted win/loss self-report).

All full-dataset counts are computed from unbounded aggregate queries —
never derived from the bounded illustrative samples.

Ambiguous ``tenders.tender_id`` values (shared by more than one tender row)
are further split into "no evidence attached" vs. "evidence attached" — see
``PathBAuditReport`` in ``domain.py`` for the exact field semantics and
``evaluate.py`` for the resulting WARN/BLOCKED severity split.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import exists, func, or_, select
from sqlalchemy.orm import Session

from db.lifecycle_constants import LIFECYCLE_STATUS_AWARDED
from db.models import Company, ContractAward, Tender, TenderOutcome
from pipeline.awards_reconciler import AWARD_MATCH_CONFIDENCE_HIGH
from pipeline.registry_engine.derived_tender_evidence.domain import (
    CONFIDENCE_BUCKET_HIGH,
    CONFIDENCE_BUCKET_MISSING,
    CONFIDENCE_BUCKET_PARTIAL,
    CrossPathAuditReport,
    DerivedTenderEvidenceReport,
    PathAAuditReport,
    PathBAuditReport,
)

SAMPLE_LIMIT = 50
DATASET_HASH_BATCH_SIZE = 1000

OUTCOME_WON = "won"
OUTCOME_LOST = "lost"
OUTCOME_WITHDREW = "withdrew"
OUTCOME_PENDING = "pending"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _non_awarded_predicate():
    """True when a tender is NOT in the 'awarded' lifecycle state — including
    when lifecycle_status is NULL.

    SQL's three-valued logic means `lifecycle_status != 'awarded'` evaluates
    to NULL (not TRUE) for a NULL column, silently excluding such rows from
    both the awarded count (correctly, via `==`) and this non-awarded count
    (incorrectly, via bare `!=`) — a real gap even though
    tenders.lifecycle_status is schema-declared NOT NULL; this audit does
    not trust that guarantee without an explicit check. A NULL row is
    structurally impossible to insert under the current NOT NULL
    constraint, which is exactly why this predicate is unit-tested via its
    compiled SQL rather than a real inserted row (see
    tests/unit/test_derived_tender_evidence_audit.py).
    """
    return or_(
        Tender.lifecycle_status != LIFECYCLE_STATUS_AWARDED,
        Tender.lifecycle_status.is_(None),
    )


def _ambiguous_tender_id_subquery():
    """Non-empty tenders.tender_id values shared by more than one tender row."""
    return (
        select(Tender.tender_id)
        .where(Tender.tender_id.isnot(None))
        .where(Tender.tender_id != "")
        .group_by(Tender.tender_id)
        .having(func.count() > 1)
    )


def _compute_path_a_dataset_hash(session: Session) -> str:
    """Streamed, batched, full-dataset hash over every tender row — including
    rows without an award_id — so the hash is sensitive to any change in the
    base population, not just the linked subset.
    """
    hasher = hashlib.sha256()
    stmt = (
        select(
            Tender.id,
            Tender.lifecycle_status,
            Tender.award_id,
            ContractAward.company_id,
            Tender.award_match_confidence,
        )
        .select_from(Tender)
        .outerjoin(ContractAward, ContractAward.id == Tender.award_id)
        .order_by(Tender.id)
    )
    result = session.execute(stmt)
    for row in result.yield_per(DATASET_HASH_BATCH_SIZE):
        tender_id, lifecycle_status, award_id, company_id, confidence = row
        part = "|".join(
            [
                str(tender_id),
                str(lifecycle_status),
                str(award_id),
                str(company_id),
                str(confidence),
            ]
        )
        hasher.update(part.encode("utf-8"))
    return hasher.hexdigest()


def audit_path_a_awarded_winner(session: Session) -> PathAAuditReport:
    inventory_total = session.scalar(select(func.count()).select_from(Tender)) or 0

    eligible_awarded_total = (
        session.scalar(
            select(func.count())
            .select_from(Tender)
            .where(Tender.lifecycle_status == LIFECYCLE_STATUS_AWARDED)
        )
        or 0
    )
    awarded_with_award_id = (
        session.scalar(
            select(func.count())
            .select_from(Tender)
            .where(Tender.lifecycle_status == LIFECYCLE_STATUS_AWARDED)
            .where(Tender.award_id.isnot(None))
        )
        or 0
    )
    awarded_without_award_id = eligible_awarded_total - awarded_with_award_id

    non_awarded_with_award_id = (
        session.scalar(
            select(func.count())
            .select_from(Tender)
            .where(_non_awarded_predicate())
            .where(Tender.award_id.isnot(None))
        )
        or 0
    )

    dangling_filter = (
        select(Tender.id, Tender.award_id)
        .where(Tender.award_id.isnot(None))
        .where(~exists().where(ContractAward.id == Tender.award_id))
    )
    dangling_award_id_count = (
        session.scalar(
            select(func.count())
            .select_from(Tender)
            .where(Tender.award_id.isnot(None))
            .where(~exists().where(ContractAward.id == Tender.award_id))
        )
        or 0
    )
    dangling_rows = session.execute(dangling_filter.limit(SAMPLE_LIMIT)).all()
    dangling_award_id_samples = [
        {"tender_id": int(r.id), "award_id": int(r.award_id)} for r in dangling_rows
    ]

    award_without_company_count = (
        session.scalar(
            select(func.count())
            .select_from(Tender)
            .join(ContractAward, ContractAward.id == Tender.award_id)
            .where(ContractAward.company_id.is_(None))
        )
        or 0
    )
    awc_rows = session.execute(
        select(Tender.id, Tender.award_id)
        .select_from(Tender)
        .join(ContractAward, ContractAward.id == Tender.award_id)
        .where(ContractAward.company_id.is_(None))
        .limit(SAMPLE_LIMIT)
    ).all()
    award_without_company_samples = [
        {"tender_id": int(r.id), "award_id": int(r.award_id)} for r in awc_rows
    ]

    # All-status resolution — feeds the award_id partition invariant only.
    resolved_award_company_count = (
        session.scalar(
            select(func.count())
            .select_from(Tender)
            .join(ContractAward, ContractAward.id == Tender.award_id)
            .where(ContractAward.company_id.isnot(None))
        )
        or 0
    )

    # Awarded-only resolution — the actual "winner coverage" signal. A
    # resolved award link on a tender that isn't (yet) marked 'awarded' is
    # not winner coverage; it's tracked separately via
    # non_awarded_with_award_id.
    resolved_awarded_winner_count = (
        session.scalar(
            select(func.count())
            .select_from(Tender)
            .join(ContractAward, ContractAward.id == Tender.award_id)
            .where(ContractAward.company_id.isnot(None))
            .where(Tender.lifecycle_status == LIFECYCLE_STATUS_AWARDED)
        )
        or 0
    )

    shared_rows = session.execute(
        select(Tender.award_id, func.count())
        .select_from(Tender)
        .where(Tender.award_id.isnot(None))
        .group_by(Tender.award_id)
        .having(func.count() > 1)
    ).all()
    shared_award_id_count = len(shared_rows)
    shared_award_id_tender_count = sum(int(cnt) for _aid, cnt in shared_rows)
    shared_award_id_samples = [
        {"award_id": int(aid), "tender_count": int(cnt)}
        for aid, cnt in shared_rows[:SAMPLE_LIMIT]
    ]

    confidence_rows = session.execute(
        select(Tender.award_match_confidence).where(Tender.award_id.isnot(None))
    ).all()
    missing = partial = high = 0
    for (confidence,) in confidence_rows:
        if confidence is None:
            missing += 1
        elif confidence >= AWARD_MATCH_CONFIDENCE_HIGH:
            high += 1
        else:
            partial += 1
    match_confidence_distribution = {
        CONFIDENCE_BUCKET_MISSING: missing,
        CONFIDENCE_BUCKET_PARTIAL: partial,
        CONFIDENCE_BUCKET_HIGH: high,
    }

    # Scoped to awarded-only, matching resolved_awarded_winner_count — a
    # non-awarded resolved link must not contribute to this breakdown.
    role_rows = session.execute(
        select(Company.entity_role, func.count())
        .select_from(Tender)
        .join(ContractAward, ContractAward.id == Tender.award_id)
        .join(Company, Company.id == ContractAward.company_id)
        .where(Tender.lifecycle_status == LIFECYCLE_STATUS_AWARDED)
        .group_by(Company.entity_role)
    ).all()
    entity_role_counts = {str(role): int(count) for role, count in role_rows}

    return PathAAuditReport(
        generated_at=_now_iso(),
        inventory_total=inventory_total,
        eligible_awarded_total=eligible_awarded_total,
        awarded_with_award_id=awarded_with_award_id,
        awarded_without_award_id=awarded_without_award_id,
        non_awarded_with_award_id=non_awarded_with_award_id,
        dangling_award_id_count=dangling_award_id_count,
        dangling_award_id_samples=dangling_award_id_samples,
        award_without_company_count=award_without_company_count,
        award_without_company_samples=award_without_company_samples,
        resolved_award_company_count=resolved_award_company_count,
        resolved_awarded_winner_count=resolved_awarded_winner_count,
        shared_award_id_count=shared_award_id_count,
        shared_award_id_tender_count=shared_award_id_tender_count,
        shared_award_id_samples=shared_award_id_samples,
        match_confidence_distribution=match_confidence_distribution,
        entity_role_counts=entity_role_counts,
        dataset_hash=_compute_path_a_dataset_hash(session),
    )


def _compute_path_b_dataset_hash(session: Session) -> str:
    """Streamed, batched, full-dataset hash over two ordered streams, each
    with a distinct prefix so they can never collide with each other:

    1. Every tender's identity (id, external tender_id) — without this,
       the hash would be blind to a tender_id change (e.g. an ambiguity
       being fixed), since tenders.tender_id is Path B's join key but
       tender_outcomes never stores anything about the tenders table
       itself.
    2. Every tender_outcomes row (the raw evidence source).
    """
    hasher = hashlib.sha256()

    tender_stmt = select(Tender.id, Tender.tender_id).order_by(Tender.id)
    for row in session.execute(tender_stmt).yield_per(DATASET_HASH_BATCH_SIZE):
        tender_pk, tender_ext_id = row
        part = "|".join(["tender", str(tender_pk), str(tender_ext_id)])
        hasher.update(part.encode("utf-8"))

    outcome_stmt = select(
        TenderOutcome.id,
        TenderOutcome.tender_id,
        TenderOutcome.company_id,
        TenderOutcome.outcome,
    ).order_by(TenderOutcome.id)
    for row in session.execute(outcome_stmt).yield_per(DATASET_HASH_BATCH_SIZE):
        outcome_id, tender_id, company_id, outcome = row
        part = "|".join(
            ["outcome", str(outcome_id), str(tender_id), str(company_id), str(outcome)]
        )
        hasher.update(part.encode("utf-8"))

    return hasher.hexdigest()


def audit_path_b_reported_bidder(session: Session) -> PathBAuditReport:
    inventory_total = session.scalar(select(func.count()).select_from(Tender)) or 0

    tenders_with_valid_external_id = (
        session.scalar(
            select(func.count())
            .select_from(Tender)
            .where(Tender.tender_id.isnot(None))
            .where(Tender.tender_id != "")
        )
        or 0
    )
    tenders_missing_external_id = inventory_total - tenders_with_valid_external_id

    ambiguous_subq = _ambiguous_tender_id_subquery()
    ambiguous_rows_with_counts = session.execute(
        select(Tender.tender_id, func.count())
        .where(Tender.tender_id.isnot(None))
        .where(Tender.tender_id != "")
        .group_by(Tender.tender_id)
        .having(func.count() > 1)
    ).all()
    ambiguous_external_id_distinct_count = len(ambiguous_rows_with_counts)
    ambiguous_external_id_tender_count = sum(
        int(cnt) for _tid, cnt in ambiguous_rows_with_counts
    )
    ambiguous_external_id_samples = [
        {"tender_id": str(tid), "tender_count": int(cnt)}
        for tid, cnt in ambiguous_rows_with_counts[:SAMPLE_LIMIT]
    ]

    # Of the ambiguous tender_id values, how many already have
    # tender_outcomes evidence attached — those are the ones where the
    # ambiguity is not just a data-quality nuisance but actively blocks
    # attributing real self-reported evidence to a single tender.
    ambiguous_outcome_rows = session.execute(
        select(TenderOutcome.tender_id, func.count())
        .where(TenderOutcome.tender_id.in_(ambiguous_subq))
        .group_by(TenderOutcome.tender_id)
    ).all()
    ambiguous_external_id_with_outcomes_distinct_count = len(ambiguous_outcome_rows)
    ambiguous_outcome_row_count = sum(int(cnt) for _tid, cnt in ambiguous_outcome_rows)

    safely_attributable_tenders = (
        tenders_with_valid_external_id - ambiguous_external_id_tender_count
    )

    attributable_tender_ids_subq = (
        select(Tender.tender_id)
        .where(Tender.tender_id.isnot(None))
        .where(Tender.tender_id != "")
        .where(Tender.tender_id.notin_(ambiguous_subq))
    )

    bidder_counts_rows = session.execute(
        select(
            TenderOutcome.tender_id,
            func.count(func.distinct(TenderOutcome.company_id)),
        )
        .where(TenderOutcome.tender_id.in_(attributable_tender_ids_subq))
        .group_by(TenderOutcome.tender_id)
    ).all()
    tenders_with_reported_bidders = len(bidder_counts_rows)
    bucket_1 = bucket_2 = bucket_3_plus = 0
    for _tender_id, distinct_company_count in bidder_counts_rows:
        if distinct_company_count == 1:
            bucket_1 += 1
        elif distinct_company_count == 2:
            bucket_2 += 1
        else:
            bucket_3_plus += 1
    bidder_count_distribution = {
        "1": bucket_1,
        "2": bucket_2,
        "3_plus": bucket_3_plus,
    }

    outcomes_rows = session.execute(
        select(TenderOutcome.outcome, func.count())
        .where(TenderOutcome.tender_id.in_(attributable_tender_ids_subq))
        .group_by(TenderOutcome.outcome)
    ).all()
    outcomes_breakdown = {str(outcome): int(count) for outcome, count in outcomes_rows}

    attributable_outcomes_filter = TenderOutcome.tender_id.in_(
        attributable_tender_ids_subq
    )
    dangling_company_id_count = (
        session.scalar(
            select(func.count())
            .select_from(TenderOutcome)
            .where(attributable_outcomes_filter)
            .where(~exists().where(Company.id == TenderOutcome.company_id))
        )
        or 0
    )
    dangling_rows = session.execute(
        select(TenderOutcome.id, TenderOutcome.tender_id, TenderOutcome.company_id)
        .where(attributable_outcomes_filter)
        .where(~exists().where(Company.id == TenderOutcome.company_id))
        .limit(SAMPLE_LIMIT)
    ).all()
    dangling_company_id_samples = [
        {
            "outcome_id": int(r.id),
            "tender_id": str(r.tender_id),
            "company_id": int(r.company_id),
        }
        for r in dangling_rows
    ]

    role_rows = session.execute(
        select(Company.entity_role, func.count())
        .select_from(TenderOutcome)
        .join(Company, Company.id == TenderOutcome.company_id)
        .where(attributable_outcomes_filter)
        .group_by(Company.entity_role)
    ).all()
    entity_role_counts = {str(role): int(count) for role, count in role_rows}

    return PathBAuditReport(
        generated_at=_now_iso(),
        inventory_total=inventory_total,
        tenders_with_valid_external_id=tenders_with_valid_external_id,
        tenders_missing_external_id=tenders_missing_external_id,
        ambiguous_external_id_tender_count=ambiguous_external_id_tender_count,
        ambiguous_external_id_distinct_count=ambiguous_external_id_distinct_count,
        ambiguous_external_id_samples=ambiguous_external_id_samples,
        ambiguous_external_id_with_outcomes_distinct_count=(
            ambiguous_external_id_with_outcomes_distinct_count
        ),
        ambiguous_outcome_row_count=ambiguous_outcome_row_count,
        safely_attributable_tenders=safely_attributable_tenders,
        tenders_with_reported_bidders=tenders_with_reported_bidders,
        bidder_count_distribution=bidder_count_distribution,
        outcomes_breakdown=outcomes_breakdown,
        dangling_company_id_count=dangling_company_id_count,
        dangling_company_id_samples=dangling_company_id_samples,
        entity_role_counts=entity_role_counts,
        dataset_hash=_compute_path_b_dataset_hash(session),
    )


def audit_cross_path(session: Session) -> CrossPathAuditReport:
    """Compare Path A's resolved winner against Path B's self-reported
    outcomes for the same tender. Only tenders with a safely-attributable
    (non-ambiguous, non-empty) external tender_id AND a resolved Path A
    winner are eligible; ambiguous ones are excluded and counted, never
    silently resolved to "the first match."
    """
    ambiguous_subq = _ambiguous_tender_id_subquery()
    ambiguous_id_set = {row[0] for row in session.execute(ambiguous_subq).all()}

    # Only genuinely awarded winners are comparable — a resolved award
    # link on a non-awarded tender is not a winner claim and must not be
    # cross-checked against Path B's self-reported outcomes.
    resolved_rows = session.execute(
        select(Tender.id, Tender.tender_id, ContractAward.company_id)
        .select_from(Tender)
        .join(ContractAward, ContractAward.id == Tender.award_id)
        .where(ContractAward.company_id.isnot(None))
        .where(Tender.lifecycle_status == LIFECYCLE_STATUS_AWARDED)
        .where(Tender.tender_id.isnot(None))
        .where(Tender.tender_id != "")
    ).all()

    comparable: list[tuple[int, str, int]] = []
    ambiguous_excluded_count = 0
    for tender_pk, tender_ext_id, winner_company_id in resolved_rows:
        if tender_ext_id in ambiguous_id_set:
            ambiguous_excluded_count += 1
            continue
        comparable.append((tender_pk, tender_ext_id, winner_company_id))

    comparable_ext_ids = [c[1] for c in comparable]
    outcome_rows: list[Any] = []
    if comparable_ext_ids:
        outcome_rows = session.execute(
            select(
                TenderOutcome.tender_id,
                TenderOutcome.company_id,
                TenderOutcome.outcome,
            ).where(TenderOutcome.tender_id.in_(comparable_ext_ids))
        ).all()

    outcomes_by_tender_ext_id: dict[str, list[tuple[int, str]]] = {}
    for tender_ext_id, company_id, outcome in outcome_rows:
        outcomes_by_tender_ext_id.setdefault(tender_ext_id, []).append(
            (company_id, outcome)
        )

    same_winner_confirmed_won = 0
    different_winner = 0
    winner_marked_lost = 0
    winner_marked_withdrawn = 0
    winner_marked_pending = 0

    for _tender_pk, tender_ext_id, winner_company_id in comparable:
        for company_id, outcome in outcomes_by_tender_ext_id.get(tender_ext_id, []):
            if company_id == winner_company_id:
                if outcome == OUTCOME_WON:
                    same_winner_confirmed_won += 1
                elif outcome == OUTCOME_LOST:
                    winner_marked_lost += 1
                elif outcome == OUTCOME_WITHDREW:
                    winner_marked_withdrawn += 1
                elif outcome == OUTCOME_PENDING:
                    winner_marked_pending += 1
            elif outcome == OUTCOME_WON:
                different_winner += 1

    return CrossPathAuditReport(
        generated_at=_now_iso(),
        comparable_tender_count=len(comparable),
        ambiguous_excluded_count=ambiguous_excluded_count,
        same_winner_confirmed_won=same_winner_confirmed_won,
        different_winner=different_winner,
        winner_marked_lost=winner_marked_lost,
        winner_marked_withdrawn=winner_marked_withdrawn,
        winner_marked_pending=winner_marked_pending,
    )


def run_derived_tender_evidence_audit(session: Session) -> DerivedTenderEvidenceReport:
    return DerivedTenderEvidenceReport(
        path_a=audit_path_a_awarded_winner(session),
        path_b=audit_path_b_reported_bidder(session),
        cross_path=audit_cross_path(session),
    )
