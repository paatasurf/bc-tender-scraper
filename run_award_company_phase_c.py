#!/usr/bin/env python3
"""Phase C: link contract awards to companies and refresh award intelligence fields."""

from __future__ import annotations

import json

import config.env  # noqa: F401

from db.connection import get_session, init_db
from db.import_contract_awards import match_contract_awards_to_companies
from db.models import Company, ContractAward
from pipeline.refresh_company_award_stats import refresh_company_award_stats
from sqlalchemy import func, select


def _snapshot(session) -> dict:
    total_awards = session.scalar(select(func.count()).select_from(ContractAward)) or 0
    linked = (
        session.scalar(
            select(func.count()).select_from(ContractAward).where(ContractAward.company_id.isnot(None))
        )
        or 0
    )
    with_award_stats = (
        session.scalar(select(func.count()).select_from(Company).where(Company.award_count > 0)) or 0
    )
    overlap = (
        session.scalar(
            select(func.count())
            .select_from(Company)
            .where(
                Company.total_projects > 0,
                Company.data_sources.contains(["permits"]),
                Company.data_sources.contains(["contract_awards"]),
            )
        )
        or 0
    )
    top20 = session.execute(
        select(Company.name, Company.total_award_value, Company.award_count)
        .where(Company.award_count > 0)
        .order_by(Company.total_award_value.desc())
        .limit(20)
    ).all()
    return {
        "total_awards": total_awards,
        "linked_awards": linked,
        "link_percentage": round(linked / total_awards * 100, 1) if total_awards else 0.0,
        "companies_with_award_count": with_award_stats,
        "overlap_companies": overlap,
        "top_20_by_total_award_value": [
            {
                "name": row.name,
                "total_award_value": float(row.total_award_value),
                "award_count": row.award_count,
            }
            for row in top20
        ],
    }


def main() -> None:
    init_db()
    session = get_session()
    try:
        before = _snapshot(session)
        print("[PhaseC] BEFORE", json.dumps(before, indent=2))

        print("[PhaseC] Matching contract awards to companies...")
        match_results = match_contract_awards_to_companies(session)
        print("[PhaseC] Match results:", match_results)

        print("[PhaseC] Refreshing company award intelligence...")
        refresh_results = refresh_company_award_stats(session)
        print("[PhaseC] Refresh results:", refresh_results)

        after = _snapshot(session)
        print("[PhaseC] AFTER", json.dumps(after, indent=2))
    finally:
        session.close()


if __name__ == "__main__":
    main()
