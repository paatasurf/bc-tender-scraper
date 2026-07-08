#!/usr/bin/env python3
"""LinkedIn Discovery validation — BC construction research (no DB writes)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.linkedin.batch_discover import discover_bc_construction_batch  # noqa: E402
from research.linkedin.discover import write_raw_artifact  # noqa: E402
from research.linkedin.normalize import run_normalize  # noqa: E402
from research.linkedin.validate import (  # noqa: E402
    build_validation_report,
    compare_multi_source,
    write_validation_outputs,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--min-urls", type=int, default=300, help="Minimum URL candidates (default 300).")
    parser.add_argument("--max-urls", type=int, default=500, help="Maximum URL candidates (default 500).")
    parser.add_argument("--delay", type=float, default=1.2, help="Seconds between public fetch requests.")
    parser.add_argument("--session", default="", help="LinkedIn session JSON (or LINKEDIN_SESSION_PATH).")
    parser.add_argument(
        "--no-public-fetch",
        action="store_true",
        help="Require linkedin_scraper session; do not use public page fetch.",
    )
    parser.add_argument(
        "--skip-discover",
        action="store_true",
        help="Reuse existing linkedin_companies_raw.json.",
    )
    args = parser.parse_args()

    if args.skip_discover:
        from research.linkedin.normalize import load_raw

        raw = load_raw()
        candidates_meta = None
    else:
        raw, candidates_meta = discover_bc_construction_batch(
            min_count=args.min_urls,
            max_count=args.max_urls,
            delay_seconds=args.delay,
            session_path=args.session or None,
            use_public_fetch=not args.no_public_fetch,
        )
        out = write_raw_artifact(raw)
        ok = sum(1 for r in raw.get("records") or [] if r.get("scrape_status") == "ok")
        print(
            f"[discover] Wrote {out} — {raw.get('record_count')} attempts, "
            f"{ok} OK, method={raw.get('fetch_method')}",
            flush=True,
        )

    normalized = run_normalize()
    print(f"[normalize] {normalized.get('record_count')} records", flush=True)

    comparison = compare_multi_source(normalized)
    print(
        "[compare] "
        f"known={comparison.get('already_known_count')} "
        f"new={comparison.get('potentially_new_count')} "
        f"dupes={comparison.get('possible_duplicates_count')}",
        flush=True,
    )

    report = build_validation_report(
        raw=raw,
        normalized=normalized,
        comparison=comparison,
        candidates_meta=candidates_meta,
    )
    paths = write_validation_outputs(report)
    print(f"[validation] Wrote {paths[0]}")
    print(f"[validation] Wrote {paths[1]}")
    print(f"[validation] Wrote {paths[2]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
