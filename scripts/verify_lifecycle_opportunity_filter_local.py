#!/usr/bin/env python3
"""P2-07 Step 2 local verification — candidate pool counts (LOCAL DATABASE ONLY)."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

import config.env  # noqa: F401

from db.connection import get_session, init_db
from db.models import CommercialTender, Tender
from pipeline.opportunity_discovery import _load_tender_candidates, _scan_construction_rule_tenders_from_rows
from pipeline.opportunity_discovery import CompanySignals
from sqlalchemy import func, select


def _require_local_database_url() -> None:
    database_url = os.getenv("DATABASE_URL", "")
    if not database_url:
        raise SystemExit("DATABASE_URL is not configured")
    lowered = database_url.lower()
    if any(token in lowered for token in ("railway", "rlwy.net", "production")):
        raise SystemExit("Refusing lifecycle filter verification against production DATABASE_URL")


def main() -> int:
    _require_local_database_url()
    company_id = int(sys.argv[1]) if len(sys.argv) > 1 else 8638
    max_candidates = 400

    init_db(raise_on_failure=True)
    session = get_session()
    try:
        from db.models import Company

        company = session.get(Company, company_id)
        if company is None:
            print(json.dumps({"error": f"Company {company_id} not found"}, indent=2))
            return 1

        federal_total = session.scalar(select(func.count()).select_from(Tender)) or 0
        federal_open = session.scalar(
            select(func.count()).select_from(Tender).where(Tender.is_open.is_(True))
        ) or 0
        commercial_total = session.scalar(select(func.count()).select_from(CommercialTender)) or 0
        commercial_open = session.scalar(
            select(func.count()).select_from(CommercialTender).where(CommercialTender.is_open.is_(True))
        ) or 0

        default_rows = _load_tender_candidates(
            session, "construction", max_candidates, include_closed=False
        )
        closed_rows = _load_tender_candidates(
            session, "construction", max_candidates, include_closed=True
        )

        signals = CompanySignals.from_company(company)
        default_scored = _scan_construction_rule_tenders_from_rows(
            default_rows, signals, include_closed=False
        )
        closed_scored = _scan_construction_rule_tenders_from_rows(
            closed_rows, signals, include_closed=True
        )

        report = {
            "verified_at": datetime.now(timezone.utc).isoformat(),
            "company_id": company_id,
            "company_name": company.name,
            "db_counts": {
                "federal_total": federal_total,
                "federal_open": federal_open,
                "commercial_total": commercial_total,
                "commercial_open": commercial_open,
            },
            "candidate_pool_loaded": {
                "include_closed_false": len(default_rows),
                "include_closed_true": len(closed_rows),
            },
            "rule_scan_candidates": {
                "include_closed_false": len(default_scored),
                "include_closed_true": len(closed_scored),
            },
        }
        print(json.dumps(report, indent=2))
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
