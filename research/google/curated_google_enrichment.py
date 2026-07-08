"""Curated Google Business enrichment — Class A/B only, existing companies."""

from __future__ import annotations

import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal
from urllib.parse import urlparse

from research.enrichment.paths import COMPANY_PROFILES_JSON
from research.google.paths import REPO_ROOT
from research.linkedin.curated_verifier import (
    CuratedCompany,
    SourceClass,
    load_curated_companies,
)

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pipeline.google_enrichment.config import GoogleEnrichmentSettings, load_settings  # noqa: E402
from pipeline.google_enrichment.log_contract import (  # noqa: E402
    LOG_STATUS_ERROR,
    LOG_STATUS_SUCCESS,
    evaluate_lookup,
)
from pipeline.google_enrichment.models import CompanyMatchContext, PlaceCandidate  # noqa: E402
from pipeline.google_enrichment.provider import GoogleEnrichmentProvider, get_provider  # noqa: E402

GOOGLE_VERIFIED_STATUS = "Google Verified"
GOOGLE_NOT_FOUND_STATUS = "Not Found"
GOOGLE_FAILED_STATUS = "Failed"
GoogleVerificationStatus = Literal["Google Verified", "Not Found", "Failed"] | None


@dataclass
class GoogleEnrichmentRecord:
    normalized_name: str
    company_name: str
    source_class: SourceClass
    status: GoogleVerificationStatus
    google_verified: bool
    curated_website: str | None = None
    google_place_id: str | None = None
    official_website: str | None = None
    google_business_category: str | None = None
    google_address: str | None = None
    google_phone: str | None = None
    google_rating: float | None = None
    google_review_count: int | None = None
    google_maps_url: str | None = None
    google_business_status: str | None = None
    match_confidence: float | None = None
    query_used: str | None = None
    provider: str | None = None
    skip_reason: str | None = None
    city: str | None = None
    province: str | None = None
    sources_present: list[str] = field(default_factory=list)
    enriched_at: str | None = None

    def __post_init__(self) -> None:
        if self.enriched_at is None and self.status is not None:
            self.enriched_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _website_domain(website: str | None) -> str:
    if not website:
        return ""
    text = website.strip()
    if not text.startswith("http"):
        text = "https://" + text
    host = urlparse(text).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return host.split("/")[0]


def build_curated_search_query(company: CuratedCompany) -> str:
    """Build a Google Maps search query from curated company fields only."""
    name = company.company_name.strip()
    city = (company.city or "").strip()
    province = (company.province or "BC").strip() or "BC"
    if city and city not in (".", ".."):
        return f"{name} {city} {province}".strip()
    domain = _website_domain(company.website)
    if domain:
        return f"{name} {domain}".strip()
    return f"{name} BC Canada".strip()


def _match_context(company: CuratedCompany) -> CompanyMatchContext:
    return CompanyMatchContext(
        company_id=0,
        name=company.company_name,
        city=company.city or "",
        province=company.province or "BC",
        address="",
        phone="",
    )


def _apply_place(record: GoogleEnrichmentRecord, place: PlaceCandidate) -> None:
    record.google_place_id = place.place_id
    record.official_website = place.google_website or None
    record.google_business_category = place.category or None
    record.google_address = place.formatted_address or None
    record.google_phone = place.phone or None
    record.google_rating = place.rating
    record.google_review_count = place.review_count
    record.google_maps_url = place.google_maps_url or None
    record.google_business_status = place.business_status or None


async def enrich_company_async(
    company: CuratedCompany,
    *,
    provider: GoogleEnrichmentProvider | None = None,
    settings: GoogleEnrichmentSettings | None = None,
    run_lookup: bool = True,
    reserved_place_ids: frozenset[str] | None = None,
) -> GoogleEnrichmentRecord:
    cfg = settings or load_settings()
    prov = provider or get_provider(cfg)

    record = GoogleEnrichmentRecord(
        normalized_name=company.normalized_name,
        company_name=company.company_name,
        source_class=company.source_class,
        status=None,
        google_verified=False,
        curated_website=company.website,
        city=company.city,
        province=company.province,
        sources_present=company.sources_present,
        provider=prov.provider_name,
    )

    if not run_lookup:
        record.skip_reason = "lookup_skipped"
        return record

    query = build_curated_search_query(company)
    record.query_used = query
    run_id = str(uuid.uuid4())
    started = time.perf_counter()
    provider_error: str | None = None
    candidates: list[PlaceCandidate] = []

    try:
        candidates = await prov.lookup(query, limit=3)
    except Exception as exc:
        provider_error = f"{type(exc).__name__}:{exc}"

    latency_ms = int((time.perf_counter() - started) * 1000)
    evaluation = evaluate_lookup(
        _match_context(company),
        candidates=candidates,
        provider=prov.provider_name,
        query_used=query,
        run_id=run_id,
        latency_ms=latency_ms,
        settings=cfg,
        reserved_place_ids=reserved_place_ids,
        provider_error=provider_error,
    )

    record.match_confidence = evaluation.log_record.match_confidence

    if provider_error or evaluation.log_record.status == LOG_STATUS_ERROR:
        record.status = GOOGLE_FAILED_STATUS
        record.skip_reason = provider_error or evaluation.log_record.error_message
        return record

    if evaluation.log_record.status == LOG_STATUS_SUCCESS and evaluation.best:
        record.status = GOOGLE_VERIFIED_STATUS
        record.google_verified = True
        _apply_place(record, evaluation.best.candidate)
        return record

    record.status = GOOGLE_NOT_FOUND_STATUS
    record.skip_reason = evaluation.enrichment_status
    if evaluation.best and not evaluation.best.hard_rejected:
        _apply_place(record, evaluation.best.candidate)
    return record


def _result_bucket(row: GoogleEnrichmentRecord) -> str:
    if row.status == GOOGLE_VERIFIED_STATUS:
        return "google_verified"
    if row.status == GOOGLE_NOT_FOUND_STATUS:
        return "not_found"
    if row.status == GOOGLE_FAILED_STATUS:
        return "failed"
    return "skipped"


def compute_class_statistics(results: list[GoogleEnrichmentRecord]) -> dict[str, Any]:
    stats: dict[str, Any] = {}
    for source_class in ("A", "B"):
        rows = [row for row in results if row.source_class == source_class]
        total = len(rows)
        counts = {
            "google_verified": sum(1 for row in rows if _result_bucket(row) == "google_verified"),
            "not_found": sum(1 for row in rows if _result_bucket(row) == "not_found"),
            "failed": sum(1 for row in rows if _result_bucket(row) == "failed"),
            "skipped": sum(1 for row in rows if _result_bucket(row) == "skipped"),
        }
        stats[source_class] = {
            "total_companies": total,
            **counts,
            "google_verified_pct": round((100.0 * counts["google_verified"] / total), 2) if total else 0.0,
        }
    return stats


def compute_run_statistics(results: list[GoogleEnrichmentRecord]) -> dict[str, Any]:
    total = len(results)
    counts = {
        "total_companies_processed": total,
        "google_verified": sum(1 for row in results if _result_bucket(row) == "google_verified"),
        "not_found": sum(1 for row in results if _result_bucket(row) == "not_found"),
        "failed": sum(1 for row in results if _result_bucket(row) == "failed"),
        "skipped": sum(1 for row in results if _result_bucket(row) == "skipped"),
    }
    counts["google_verified_pct"] = round((100.0 * counts["google_verified"] / total), 2) if total else 0.0
    return counts


__all__ = [
    "GOOGLE_VERIFIED_STATUS",
    "GOOGLE_NOT_FOUND_STATUS",
    "GOOGLE_FAILED_STATUS",
    "GoogleEnrichmentRecord",
    "build_curated_search_query",
    "enrich_company_async",
    "compute_class_statistics",
    "compute_run_statistics",
    "load_curated_companies",
]
