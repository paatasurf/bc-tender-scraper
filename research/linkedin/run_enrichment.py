"""CLI — build LinkedIn company enrichment dataset (read-only)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.linkedin.coverage_report import write_coverage_report  # noqa: E402
from research.linkedin.enrich_dataset import run_enrichment  # noqa: E402
from research.linkedin.paths import (  # noqa: E402
    COMPANIES_ENRICHED_CSV,
    COMPANIES_ENRICHED_JSON,
    COVERAGE_REPORT_MD,
    ENRICHMENT_METADATA_JSON,
    RAW_JSON,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build BC construction LinkedIn enrichment dataset from local source snapshots."
    )
    parser.add_argument(
        "--raw",
        type=Path,
        default=RAW_JSON,
        help="Path to linkedin_companies_raw.json cache",
    )
    parser.add_argument(
        "--fetch-missing",
        action="store_true",
        help="Public-fetch LinkedIn pages not yet in cache (slow; may hit bot detection)",
    )
    parser.add_argument(
        "--max-fetch",
        type=int,
        default=0,
        help="Max new LinkedIn URLs to fetch (0 = no limit when --fetch-missing)",
    )
    parser.add_argument(
        "--fetch-delay",
        type=float,
        default=1.0,
        help="Seconds between public fetch requests",
    )
    parser.add_argument(
        "--include-all-odbus",
        action="store_true",
        help="Include all ODB BC rows, not only NAICS-23 construction",
    )
    args = parser.parse_args()

    result = run_enrichment(
        raw_path=args.raw,
        fetch_missing=args.fetch_missing,
        max_fetch=args.max_fetch,
        fetch_delay=args.fetch_delay,
        bc_construction_only=not args.include_all_odbus,
    )
    write_coverage_report()

    meta = result["metadata"]
    print(f"[enrich] pool={meta['pool_size']:,} verified={meta['linkedin_enrichment_status']['verified']}")
    print(f"[enrich] wrote {COMPANIES_ENRICHED_JSON}")
    print(f"[enrich] wrote {COMPANIES_ENRICHED_CSV}")
    print(f"[enrich] wrote {ENRICHMENT_METADATA_JSON}")
    print(f"[enrich] wrote {COVERAGE_REPORT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
