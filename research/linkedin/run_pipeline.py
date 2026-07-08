#!/usr/bin/env python3
"""Run the experimental LinkedIn Company Discovery research pipeline (no DB writes)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.linkedin.compare import run_compare  # noqa: E402
from research.linkedin.discover import discover_companies, write_raw_artifact  # noqa: E402
from research.linkedin.normalize import run_normalize  # noqa: E402
from research.linkedin.paths import INPUT_DIR, URLS_FILE  # noqa: E402
from research.linkedin.report import build_report, write_reports  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="LinkedIn Company Discovery research pipeline (experimental, local only)."
    )
    parser.add_argument(
        "--use-sample",
        action="store_true",
        help="Use input/sample_companies_raw.json instead of live LinkedIn scraping.",
    )
    parser.add_argument(
        "--urls-file",
        type=Path,
        default=URLS_FILE,
        help="Text file with one LinkedIn company URL per line.",
    )
    parser.add_argument(
        "--session",
        default="",
        help="Path to LinkedIn session JSON (or set LINKEDIN_SESSION_PATH).",
    )
    parser.add_argument(
        "--headless",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Run Playwright headless for live scrape (default: true).",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=2.0,
        help="Seconds between live scrape requests.",
    )
    parser.add_argument(
        "--skip-discover",
        action="store_true",
        help="Reuse existing linkedin_companies_raw.json.",
    )
    args = parser.parse_args()

    INPUT_DIR.mkdir(parents=True, exist_ok=True)

    if args.skip_discover:
        from research.linkedin.normalize import load_raw

        raw = load_raw()
    else:
        urls = None
        if not args.use_sample and args.urls_file.exists():
            urls = [
                line.strip()
                for line in args.urls_file.read_text(encoding="utf-8").splitlines()
                if line.strip() and not line.strip().startswith("#")
            ]
        raw = discover_companies(
            urls=urls,
            use_sample=args.use_sample,
            session_path=args.session or None,
            headless=args.headless,
            delay_seconds=args.delay,
        )
        out = write_raw_artifact(raw)
        print(f"[discover] Wrote {out} ({raw.get('record_count')} records, mode={raw.get('mode')})")

    normalized = run_normalize()
    print(f"[normalize] Wrote linkedin_companies_normalized.json ({normalized.get('record_count')} records)")

    comparison = run_compare()
    print(
        "[compare] "
        f"known={comparison.get('already_known_count')} "
        f"new={comparison.get('potentially_new_count')} "
        f"dupes={comparison.get('possible_duplicates_count')}"
    )

    report = build_report(raw=raw, normalized=normalized, comparison=comparison)
    json_path, md_path = write_reports(report)
    print(f"[report] Wrote {json_path}")
    print(f"[report] Wrote {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
