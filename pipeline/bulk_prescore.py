"""Bulk pre-score construction companies into tender_matches (Discover hybrid path)."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from db.connection import get_session
from db.models import Company, TenderMatch
from pipeline.ai_matching import (
    HYBRID_AI_CANDIDATE_LIMIT,
    TenderPairCandidate,
    _upsert_tender_match,
    build_match_reason_from_rules,
    warm_hybrid_tender_cache,
)
from pipeline.opportunity_discovery import CompanySignals, _scan_construction_rule_tenders

logger = logging.getLogger(__name__)

DEFAULT_BATCH_SIZE = 100
DEFAULT_MAX_CANDIDATES = 400
CONSTRUCTION_KIND = "construction"
PRESCORE_MARKER_TENDER_SOURCE = "federal"
PRESCORE_MARKER_TENDER_ID = 0


def _company_has_tender_matches(session: Session, company_id: int) -> bool:
    return (
        session.scalar(
            select(TenderMatch.id)
            .where(
                TenderMatch.company_kind == CONSTRUCTION_KIND,
                TenderMatch.company_id == company_id,
            )
            .limit(1)
        )
        is not None
    )


def _ensure_prescore_marker(session: Session, company_id: int) -> bool:
    """Record that bulk prescore ran when no pair rows were persisted."""
    if _company_has_tender_matches(session, company_id):
        return False
    _upsert_tender_match(
        session,
        company_kind=CONSTRUCTION_KIND,
        company_id=company_id,
        tender_source=PRESCORE_MARKER_TENDER_SOURCE,
        tender_id=PRESCORE_MARKER_TENDER_ID,
        score=0,
        reasoning="bulk_prescore:no_matches_persisted",
        breakdown_json={"marker": True},
    )
    session.commit()
    return True


def _pending_construction_filter():
    return ~exists(
        select(TenderMatch.id).where(
            TenderMatch.company_kind == CONSTRUCTION_KIND,
            TenderMatch.company_id == Company.id,
        )
    )


def count_pending_construction_companies(session: Session) -> int:
    """Construction companies with no tender_matches rows yet."""
    return int(
        session.scalar(
            select(func.count()).select_from(Company).where(_pending_construction_filter())
        )
        or 0
    )


def list_pending_construction_company_ids(session: Session, *, limit: int) -> list[int]:
    rows = session.scalars(
        select(Company.id)
        .where(_pending_construction_filter())
        .order_by(Company.id)
        .limit(max(1, limit))
    ).all()
    return list(rows)


def prescore_construction_company(
    session: Session,
    company_id: int,
    *,
    max_candidates: int = DEFAULT_MAX_CANDIDATES,
    pair_limit: int = HYBRID_AI_CANDIDATE_LIMIT,
) -> dict[str, Any]:
    """Run Discover-style rule scan + hybrid scoring for one construction company."""
    company = session.get(Company, company_id)
    if company is None:
        return {
            "company_id": company_id,
            "status": "skipped",
            "reason": "company_not_found",
        }

    signals = CompanySignals.from_company(company)
    rule_candidates = _scan_construction_rule_tenders(session, signals, max_candidates)
    top = sorted(rule_candidates, key=lambda item: item.rule_score, reverse=True)[
        : max(1, pair_limit)
    ]
    pair_candidates = [
        TenderPairCandidate(
            tender_source=item.tender_source,
            tender_id=item.tender_id,
            match_reason=build_match_reason_from_rules(item.reasons),
        )
        for item in top
    ]

    if not pair_candidates:
        marked = _ensure_prescore_marker(session, company_id)
        return {
            "company_id": company_id,
            "company_name": company.name,
            "status": "skipped",
            "reason": "no_rule_candidates",
            "rule_scanned": len(rule_candidates),
            "candidates_sent": 0,
            "marker_inserted": marked,
        }

    result = warm_hybrid_tender_cache(
        session,
        company_id=company_id,
        kind=CONSTRUCTION_KIND,
        candidates=pair_candidates,
        inline_cap=None,
    )
    marked = _ensure_prescore_marker(session, company_id)
    return {
        "company_id": company_id,
        "company_name": company.name,
        "status": "scored",
        "rule_scanned": len(rule_candidates),
        "candidates_sent": len(pair_candidates),
        "cache_hits": result.get("cache_hits", 0),
        "freshly_scored": result.get("freshly_scored", 0),
        "skipped_cap": result.get("skipped_cap", 0),
        "skipped_no_key": result.get("skipped_no_key", 0),
        "api_errors": result.get("api_errors", 0),
        "api_key_missing": result.get("api_key_missing", False),
        "marker_inserted": marked,
    }


def _merge_company_stats(totals: dict[str, Any], company_result: dict[str, Any]) -> None:
    totals["companies_processed"] += 1
    status = company_result.get("status")
    if status == "scored":
        totals["companies_scored"] += 1
    elif status == "skipped":
        totals["companies_skipped"] += 1
    totals["rule_scanned"] += int(company_result.get("rule_scanned") or 0)
    totals["candidates_sent"] += int(company_result.get("candidates_sent") or 0)
    totals["cache_hits"] += int(company_result.get("cache_hits") or 0)
    totals["freshly_scored"] += int(company_result.get("freshly_scored") or 0)
    totals["skipped_cap"] += int(company_result.get("skipped_cap") or 0)
    totals["skipped_no_key"] += int(company_result.get("skipped_no_key") or 0)
    totals["api_errors"] += int(company_result.get("api_errors") or 0)
    if company_result.get("api_key_missing"):
        totals["api_key_missing"] = True


def _empty_totals(*, pending_before: int) -> dict[str, Any]:
    return {
        "pending_before": pending_before,
        "pending_after": pending_before,
        "batches_run": 0,
        "companies_processed": 0,
        "companies_scored": 0,
        "companies_skipped": 0,
        "rule_scanned": 0,
        "candidates_sent": 0,
        "cache_hits": 0,
        "freshly_scored": 0,
        "skipped_cap": 0,
        "skipped_no_key": 0,
        "api_errors": 0,
        "api_key_missing": False,
    }


def run_bulk_prescore_batch(
    session: Session,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    max_candidates: int = DEFAULT_MAX_CANDIDATES,
    pair_limit: int = HYBRID_AI_CANDIDATE_LIMIT,
) -> dict[str, Any]:
    """Score up to batch_size construction companies that lack tender_matches."""
    pending_before = count_pending_construction_companies(session)
    company_ids = list_pending_construction_company_ids(session, limit=batch_size)
    totals = _empty_totals(pending_before=pending_before)
    totals["batch_size"] = batch_size
    totals["company_ids"] = company_ids

    for company_id in company_ids:
        company_result = prescore_construction_company(
            session,
            company_id,
            max_candidates=max_candidates,
            pair_limit=pair_limit,
        )
        _merge_company_stats(totals, company_result)
        logger.info(
            "[BulkPrescore] company_id=%s status=%s freshly_scored=%s",
            company_id,
            company_result.get("status"),
            company_result.get("freshly_scored", 0),
        )

    totals["pending_after"] = count_pending_construction_companies(session)
    totals["batches_run"] = 1 if company_ids else 0
    return totals


def run_bulk_prescore(
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    max_batches: int | None = None,
    max_candidates: int = DEFAULT_MAX_CANDIDATES,
    pair_limit: int = HYBRID_AI_CANDIDATE_LIMIT,
) -> dict[str, Any]:
    """Process pending construction companies in batches until none remain."""
    session = get_session()
    try:
        pending_before = count_pending_construction_companies(session)
        totals = _empty_totals(pending_before=pending_before)
        totals["batch_size"] = batch_size

        batches_run = 0
        while True:
            if max_batches is not None and batches_run >= max_batches:
                break

            company_ids = list_pending_construction_company_ids(session, limit=batch_size)
            if not company_ids:
                break

            batches_run += 1
            logger.info(
                "[BulkPrescore] Starting batch %s (%s companies, %s pending)",
                batches_run,
                len(company_ids),
                count_pending_construction_companies(session),
            )

            for company_id in company_ids:
                company_result = prescore_construction_company(
                    session,
                    company_id,
                    max_candidates=max_candidates,
                    pair_limit=pair_limit,
                )
                _merge_company_stats(totals, company_result)

            totals["pending_after"] = count_pending_construction_companies(session)
            logger.info(
                "[BulkPrescore] Finished batch %s — processed=%s pending=%s",
                batches_run,
                totals["companies_processed"],
                totals["pending_after"],
            )

        totals["batches_run"] = batches_run
        return totals
    finally:
        session.close()


def run_bulk_prescore_single_batch(
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    max_candidates: int = DEFAULT_MAX_CANDIDATES,
    pair_limit: int = HYBRID_AI_CANDIDATE_LIMIT,
) -> dict[str, Any]:
    """Score one batch using a dedicated DB session."""
    session = get_session()
    try:
        return run_bulk_prescore_batch(
            session,
            batch_size=batch_size,
            max_candidates=max_candidates,
            pair_limit=pair_limit,
        )
    finally:
        session.close()
