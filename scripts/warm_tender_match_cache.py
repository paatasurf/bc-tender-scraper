"""Warm tender_matches cache for hybrid Discover (rule top-N → Haiku scorer-only).

Scores cache misses for the rule-ranked tender shortlist. Fresh rows are kept
for 7 days (TENDER_MATCH_CACHE_MAX_AGE_HOURS); rediscover skips Haiku until stale.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config.env  # noqa: F401
from db.connection import get_session
from db.models import ArchCompany, Company
from pipeline.ai_matching import (
    HYBRID_AI_CANDIDATE_LIMIT,
    TenderPairCandidate,
    build_match_reason_from_rules,
    warm_hybrid_tender_cache,
)
from pipeline.opportunity_discovery import (
    CompanySignals,
    _scan_architecture_rule_tenders,
    _scan_construction_rule_tenders,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Warm hybrid tender match cache for one company.")
    parser.add_argument("--company-id", type=int, required=True)
    parser.add_argument("--kind", choices=["construction", "architecture"], required=True)
    parser.add_argument(
        "--max-candidates",
        type=int,
        default=400,
        help="Open tenders to rule-scan before taking top-N (default: 400)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=HYBRID_AI_CANDIDATE_LIMIT,
        help=f"Rule top-N pairs to send to Haiku (default: {HYBRID_AI_CANDIDATE_LIMIT})",
    )
    args = parser.parse_args()

    session = get_session()
    try:
        if args.kind == "construction":
            company = session.get(Company, args.company_id)
            if company is None:
                print(f"Construction company {args.company_id} not found", file=sys.stderr)
                return 1
            signals = CompanySignals.from_company(company)
            rule_candidates = _scan_construction_rule_tenders(session, signals, args.max_candidates)
            label = company.name
        else:
            company = session.get(ArchCompany, args.company_id)
            if company is None:
                print(f"Architecture company {args.company_id} not found", file=sys.stderr)
                return 1
            signals = CompanySignals.from_arch_company(company)
            rule_candidates = _scan_architecture_rule_tenders(session, signals, args.max_candidates)
            label = company.name

        top = sorted(rule_candidates, key=lambda item: item.rule_score, reverse=True)[: max(1, args.limit)]
        pair_candidates = [
            TenderPairCandidate(
                tender_source=item.tender_source,
                tender_id=item.tender_id,
                match_reason=build_match_reason_from_rules(item.reasons),
            )
            for item in top
        ]

        print(f"Warming cache for {label} ({args.kind}, id={args.company_id})")
        print(f"  rule-scanned: {len(rule_candidates)} open tenders")
        print(f"  top-N for AI: {len(pair_candidates)}")

        result = warm_hybrid_tender_cache(
            session,
            company_id=args.company_id,
            kind=args.kind,
            candidates=pair_candidates,
            inline_cap=None,
        )

        summary = {
            "company_id": args.company_id,
            "kind": args.kind,
            "company_name": label,
            "rule_scanned": len(rule_candidates),
            "candidates_sent": len(pair_candidates),
            "cache_hits": result.get("cache_hits", 0),
            "freshly_scored": result.get("freshly_scored", 0),
            "skipped_cap": result.get("skipped_cap", 0),
            "api_errors": result.get("api_errors", 0),
            "api_key_missing": result.get("api_key_missing", False),
        }
        print(json.dumps(summary, indent=2))
        return 0 if not summary["api_key_missing"] and summary["api_errors"] == 0 else 2
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
