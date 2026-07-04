"""Compare construction opportunity tender counts (local DB)."""
from __future__ import annotations


from pathlib import Path
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
from sqlalchemy import select

from db.connection import get_session
from db.db_safety import guard_readonly_db
_SCRIPT = Path(__file__).name
from db.models import Company
from pipeline.opportunity_discovery import discover_opportunities

COMPANY_IDS = [1735, 1517, 8756, 292, 5, 102, 84, 213, 268, 1921, 670, 420]


def main() -> None:
    guard_readonly_db(_SCRIPT)
    load_dotenv()
    session = get_session()
    try:
        print("company_id | name | total | tender | permit | award")
        print("-" * 80)
        for cid in COMPANY_IDS:
            company = session.get(Company, cid)
            if not company:
                continue
            result = discover_opportunities(
                company_id=cid,
                kind="construction",
                min_score=65,
                limit=15,
            )
            matches = result.get("matches", [])
            counts = {"tender": 0, "permit": 0, "contract_award": 0}
            for m in matches:
                counts[m["type"]] = counts.get(m["type"], 0) + 1
            name = company.name[:35]
            print(
                f"{cid:10} | {name:35} | {len(matches):5} | "
                f"{counts.get('tender', 0):6} | {counts.get('permit', 0):6} | "
                f"{counts.get('contract_award', 0):5}"
            )
    finally:
        session.close()


if __name__ == "__main__":
    main()
