"""Run early signal enrichment on a small batch for manual verification."""

from __future__ import annotations

import json
import sys

from scraper.vancouver_early_signal_enrichment import run_early_signal_enrichment


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    limit = 10
    if len(sys.argv) > 1:
        limit = int(sys.argv[1])
    result = run_early_signal_enrichment(limit=limit, force=True, fetch_details=True, persist=True)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
