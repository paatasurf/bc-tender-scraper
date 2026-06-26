"""Debug award market member selection for a company id."""
from __future__ import annotations

import sys

from db.connection import get_session
from pipeline.competitive_intel.awards import AwardCountResolver, select_award_market_members
from pipeline.competitive_intel.cohort import build_market_cohort
from pipeline.cip_builder import get_cip
from db.models import Company


def main() -> None:
    company_id = int(sys.argv[1] if len(sys.argv) > 1 else 6999)
    session = get_session()
    try:
        subject = session.get(Company, company_id)
        if subject is None:
            raise SystemExit(f"Company {company_id} not found")
        cip = get_cip(session, company_id=company_id, kind="construction", refresh=False)
        cohort = build_market_cohort(session, subject, cip, kind="construction")
        resolver = AwardCountResolver(session)
        awarded_cohort = [m for m in cohort.members if resolver.count_for(m) > 0]
        market = select_award_market_members(
            session, subject, cohort.members, resolver, kind="construction"
        )
        print(f"subject={subject.name} sector={subject.dominant_sector} trade={subject.primary_trade} city={subject.primary_city}")
        print(f"subject awards resolved={resolver.count_for(subject)} db={subject.award_count}")
        print(f"cohort_size={len(cohort.members)} awarded_in_cohort={len(awarded_cohort)}")
        print(f"award_market_size={len(market)}")
        counts = sorted((resolver.count_for(m) for m in market), reverse=True)
        print(f"award_market_counts_top10={counts[:10]}")
        if counts:
            import statistics

            print(f"median={statistics.median(counts)}")
    finally:
        session.close()


if __name__ == "__main__":
    main()
