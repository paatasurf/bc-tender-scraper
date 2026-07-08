"""Paths for the experimental LinkedIn discovery research pipeline."""

from __future__ import annotations

from pathlib import Path

RESEARCH_ROOT = Path(__file__).resolve().parent
REPO_ROOT = RESEARCH_ROOT.parents[1]
AUTH_DIR = RESEARCH_ROOT / ".auth"
DEFAULT_SESSION_PATH = AUTH_DIR / "linkedin_session.json"
SESSION_DIR = RESEARCH_ROOT / ".session"
BROWSER_PROFILE_DIR = SESSION_DIR / "browser_profile"
CACHE_DIR = RESEARCH_ROOT / "cache"
PROGRESS_JSON = RESEARCH_ROOT / "progress.json"
BATCH_REPORT_JSON = RESEARCH_ROOT / "batch_report.json"
BATCH_REPORT_MD = RESEARCH_ROOT / "batch_report.md"
VALIDATION_500_JSON = RESEARCH_ROOT / "validation_500_report.json"
VALIDATION_500_MD = RESEARCH_ROOT / "validation_500_report.md"
URL_CACHE_JSON = RESEARCH_ROOT / "url_cache.json"
URL_RESOLUTION_REPORT_JSON = RESEARCH_ROOT / "url_resolution_report.json"
URL_RESOLUTION_REPORT_MD = RESEARCH_ROOT / "url_resolution_report.md"
URL_RESOLUTION_STATISTICS_JSON = RESEARCH_ROOT / "url_resolution_statistics.json"
SMOKE_TEST_JSON = RESEARCH_ROOT / "smoke_test_report.json"
CURATED_VERIFICATION_JSON = RESEARCH_ROOT / "curated_verification_report.json"
CURATED_VERIFICATION_MD = RESEARCH_ROOT / "curated_verification_report.md"
CURATED_VERIFICATION_STATS_JSON = RESEARCH_ROOT / "curated_verification_statistics.json"

RAW_JSON = RESEARCH_ROOT / "linkedin_companies_raw.json"
NORMALIZED_JSON = RESEARCH_ROOT / "linkedin_companies_normalized.json"
REPORT_JSON = RESEARCH_ROOT / "report.json"
REPORT_MD = RESEARCH_ROOT / "report.md"
VALIDATION_JSON = RESEARCH_ROOT / "validation_report.json"
VALIDATION_MD = RESEARCH_ROOT / "validation_report.md"
HIGH_CONFIDENCE_NEW_JSON = RESEARCH_ROOT / "high_confidence_new_companies.json"
COMPANIES_ENRICHED_JSON = RESEARCH_ROOT / "companies_enriched.json"
COMPANIES_ENRICHED_CSV = RESEARCH_ROOT / "companies_enriched.csv"
ENRICHMENT_METADATA_JSON = RESEARCH_ROOT / "metadata.json"
COVERAGE_REPORT_MD = RESEARCH_ROOT / "coverage_report.md"
INPUT_DIR = RESEARCH_ROOT / "input"
URLS_FILE = INPUT_DIR / "company_urls.txt"
BC_CANDIDATES_JSON = INPUT_DIR / "bc_construction_url_candidates.json"
SAMPLE_RAW = INPUT_DIR / "sample_companies_raw.json"
SESSION_ENV = "LINKEDIN_SESSION_PATH"

DEFAULT_MARKET_REGISTRY_BASELINE = (
    REPO_ROOT
    / "specs"
    / "008-canonical-company-registry"
    / "data"
    / "enterprise_registry_seed_baseline_no_db.json"
)
DEFAULT_ENTERPRISE_SEED = (
    REPO_ROOT
    / "specs"
    / "008-canonical-company-registry"
    / "data"
    / "enterprise_registry_seed.json"
)
DEFAULT_ODBUS_CSV = REPO_ROOT / "exports" / "odbus_cache" / "ODBus_v1.csv"

ASSOCIATION_SOURCES = ("sica", "vica", "mcabc", "nrca")
ASSOCIATION_LAKE_GLOB = "data/sources/associations/{source}/2026-07-05/members.json"
ASSOCIATION_EXPORTS = "exports/{source}_members.json"
