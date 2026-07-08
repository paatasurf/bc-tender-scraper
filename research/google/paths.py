"""Paths for curated Google Business research enrichment."""

from __future__ import annotations

from pathlib import Path

RESEARCH_ROOT = Path(__file__).resolve().parent
REPO_ROOT = RESEARCH_ROOT.parents[1]
CACHE_DIR = RESEARCH_ROOT / "cache"

CURATED_GOOGLE_REPORT_JSON = RESEARCH_ROOT / "curated_google_report.json"
CURATED_GOOGLE_REPORT_MD = RESEARCH_ROOT / "curated_google_report.md"
CURATED_GOOGLE_STATS_JSON = RESEARCH_ROOT / "curated_google_statistics.json"
