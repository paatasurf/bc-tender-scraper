"""Smoke test BD intelligence endpoints for a construction and architecture company."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config.env  # noqa: F401
from db.connection import get_session, init_db
from db.db_safety import guard_readonly_db
_SCRIPT = Path(__file__).name
from pipeline.bd_recommendations import recommend_bd_intelligence


def _summarize(label: str, result: dict) -> None:
    profile = result["capability_profile"]
    print(f"\n=== {label} (company_id={result['company_id']}) ===")
    print(f"  primary_trade: {profile.get('primary_trade')}")
    print(f"  profile_completeness: {profile.get('profile_completeness')}")
    for key in (
        "active_opportunities",
        "market_pipeline",
        "competitive_intelligence",
        "relationship_opportunities",
        "growth_opportunities",
    ):
        section = result[key]
        items = section["items"]
        print(
            f"  {section['label']}: {len(items)} shown / "
            f"{section['total_passed_filter']} passed (evaluated {section['total_candidates_evaluated']})"
        )
        if items:
            top = items[0]
            title = top.get("payload", {}).get("title") or top.get("entity_name") or "—"
            print(f"    top: score={top.get('score')} | {str(title)[:60]}")
            breakdown = top.get("explanation", {}).get("breakdown", [])
            print(f"    explainability factors: {len(breakdown)}")


def main() -> int:
    guard_readonly_db(_SCRIPT)
    parser = argparse.ArgumentParser(description="Smoke test BD intelligence")
    parser.add_argument("--construction-id", type=int, default=1735, help="GHL Consultants")
    parser.add_argument("--architecture-id", type=int, default=126, help="DIALOG")
    parser.add_argument("--json", action="store_true", help="Dump full JSON for construction company")
    args = parser.parse_args()
    session = get_session()
    try:
        construction = recommend_bd_intelligence(
            session,
            company_id=args.construction_id,
            kind="construction",
            active_limit=5,
            pipeline_limit=3,
            intel_limit=3,
            relationship_limit=2,
            growth_limit=2,
        )
        _summarize("Construction", construction)

        architecture = recommend_bd_intelligence(
            session,
            company_id=args.architecture_id,
            kind="architecture",
            active_limit=5,
            pipeline_limit=3,
            intel_limit=0,
            relationship_limit=2,
            growth_limit=2,
        )
        _summarize("Architecture", architecture)

        if args.json:
            print(json.dumps(construction, indent=2, default=str)[:8000])
    finally:
        session.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
