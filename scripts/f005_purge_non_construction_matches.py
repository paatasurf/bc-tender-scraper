"""F005 one-off cleanup: purge construction tender_matches where the referenced
tender title matches the non-construction procurement heuristic
(vehicles, vending, food, goods, IT, forestry).

Why DELETE (not "mark stale"):
  - tender_matches is a cache (TTL 168h via TENDER_MATCH_CACHE_MAX_AGE_HOURS).
  - There is no stale/archived flag in the schema; freshness is purely created_at.
  - No FK references the row id, so deletion is local.
  - With CONSTRUCTION_TENDER_RELEVANCE_V1=1, _load_tender_candidates filters the
    same titles out of the candidate pool, so the deleted rows will not be
    re-inserted by future Discover runs.
  - Backdating created_at would abuse a server-default column and could be
    silently overwritten on the next AI re-score, undoing the cleanup.

Usage:
  python scripts/f005_purge_non_construction_matches.py            # dry-run (default)
  python scripts/f005_purge_non_construction_matches.py --apply    # actually delete
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config.env  # noqa: F401  # load .env before db/pipeline imports
from sqlalchemy import select

from db.connection import session_scope
from db.models import CommercialTender, Tender, TenderMatch
from pipeline.opportunity_discovery import _is_non_construction_procurement


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually delete matching rows. Default is dry-run (no writes).",
    )
    args = parser.parse_args()
    dry_run = not args.apply

    print(f"Mode: {'DRY-RUN (no writes)' if dry_run else 'APPLY (deleting rows)'}")

    with session_scope() as session:
        rows = session.scalars(
            select(TenderMatch).where(TenderMatch.company_kind == "construction")
        ).all()
        total = len(rows)
        print(f"Scanned: {total} construction tender_matches rows")

        by_source: dict[str, set[int]] = defaultdict(set)
        for row in rows:
            by_source[row.tender_source].add(row.tender_id)

        titles: dict[tuple[str, int], str] = {}
        if by_source.get("federal"):
            for t in session.scalars(
                select(Tender).where(Tender.id.in_(by_source["federal"]))
            ).all():
                titles[("federal", t.id)] = t.title or ""
        if by_source.get("commercial"):
            for t in session.scalars(
                select(CommercialTender).where(CommercialTender.id.in_(by_source["commercial"]))
            ).all():
                titles[("commercial", t.id)] = t.title or ""

        to_delete: list[TenderMatch] = []
        missing = 0
        for row in rows:
            title = titles.get((row.tender_source, row.tender_id))
            if title is None:
                missing += 1
                continue
            if _is_non_construction_procurement(title):
                to_delete.append(row)

        print(f"Tender row missing (orphaned tender_match): {missing}")
        print(f"To delete: {len(to_delete)}")

        print("Sample (up to 10):")
        for r in to_delete[:10]:
            title = titles.get((r.tender_source, r.tender_id), "")
            print(f"  [{r.tender_source}] tender_id={r.tender_id} score={r.score}  {title[:100]}")

        if dry_run:
            print("\nDry-run complete. Re-run with --apply to delete.")
            return 0

        for r in to_delete:
            session.delete(r)
        session.commit()
        print(f"\nDeleted {len(to_delete)} rows.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
