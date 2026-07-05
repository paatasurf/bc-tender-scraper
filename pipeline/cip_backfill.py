"""Batch CIP / dominant_sector backfill for CI-eligible construction companies."""

from __future__ import annotations

import time
from collections import Counter
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from db.models import Company, Permit
from pipeline.cip_builder import build_cip, persist_cip
from pipeline.competitive_intel.cohort import (
    _quality_clause,
    construction_company_analytics_clause,
    filter_construction_peer_pool,
)


def load_ci_eligible_companies(session: Session) -> list[Company]:
    rows = list(
        session.scalars(
            select(Company)
            .where(_quality_clause(Company, "construction"))
            .where(construction_company_analytics_clause())
            .order_by(Company.id)
        ).all()
    )
    return filter_construction_peer_pool(rows)


def backfill_company_cips(
    session: Session,
    *,
    dry_run: bool = True,
    sample_size: int | None = None,
    company_ids: list[int] | None = None,
) -> dict[str, Any]:
    """Build (and optionally persist) CIPs for the CI-eligible pool."""
    companies_all = load_ci_eligible_companies(session)
    total_eligible = len(companies_all)
    companies = companies_all
    if company_ids:
        id_set = set(company_ids)
        companies = [row for row in companies if row.id in id_set]
    if sample_size is not None:
        companies = companies[: max(0, sample_size)]

    sector_counts: Counter[str] = Counter()
    trade_counts: Counter[str] = Counter()
    permit_source_counts: Counter[str] = Counter()
    empty_sector_focus = 0
    errors: list[dict[str, Any]] = []
    timings: list[float] = []

    processed = 0
    persisted = 0
    started = time.perf_counter()

    for company in companies:
        company_started = time.perf_counter()
        try:
            cip = build_cip(session, company_id=company.id, kind="construction")
            sector = (cip.dominant_sector or "").strip() or "(blank)"
            sector_counts[sector] += 1
            trade = (cip.primary_trade or "").strip() or "(blank)"
            trade_counts[trade] += 1
            if not cip.sector_focus:
                empty_sector_focus += 1
            linked_permits = session.scalar(
                select(func.count())
                .select_from(Permit)
                .where(Permit.company_id == company.id)
            ) or 0
            if linked_permits:
                permit_source_counts["company_id"] += 1
            elif cip.sector_focus:
                permit_source_counts["name_match"] += 1
            else:
                permit_source_counts["none"] += 1
            if not dry_run:
                persist_cip(session, cip)
                persisted += 1
            processed += 1
        except Exception as exc:
            errors.append({"company_id": company.id, "error": str(exc)[:500]})
        timings.append(time.perf_counter() - company_started)

    elapsed = time.perf_counter() - started
    avg_ms = (sum(timings) / len(timings) * 1000) if timings else 0.0
    p95_ms = sorted(timings)[int(len(timings) * 0.95)] * 1000 if timings else 0.0

    total_eligible = len(companies_all)
    estimate_full_seconds = (elapsed / processed * total_eligible) if processed else 0.0

    return {
        "dry_run": dry_run,
        "eligible_pool_total": total_eligible,
        "sample_size_requested": sample_size,
        "companies_processed": processed,
        "companies_persisted": persisted,
        "errors": errors,
        "error_count": len(errors),
        "elapsed_seconds": round(elapsed, 2),
        "avg_ms_per_company": round(avg_ms, 1),
        "p95_ms_per_company": round(p95_ms, 1),
        "estimated_full_run_seconds": round(estimate_full_seconds, 1),
        "estimated_full_run_minutes": round(estimate_full_seconds / 60, 1),
        "dominant_sector_distribution": dict(sector_counts.most_common()),
        "primary_trade_distribution": dict(trade_counts.most_common(15)),
        "empty_sector_focus_count": empty_sector_focus,
        "permit_load_source_counts": dict(permit_source_counts.most_common()),
    }
