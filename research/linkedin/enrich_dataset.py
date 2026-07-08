"""Merge source pool with cached LinkedIn fetch results into enriched dataset."""

from __future__ import annotations

import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from research.linkedin.paths import (
    COMPANIES_ENRICHED_CSV,
    COMPANIES_ENRICHED_JSON,
    ENRICHMENT_METADATA_JSON,
    RAW_JSON,
    REPO_ROOT,
)
from research.linkedin.public_fetch import (
    _parse_company_size,
    _parse_followers,
    _parse_founded,
    _parse_specialties,
    batch_fetch_public_pages,
)
from research.linkedin.source_pool import build_source_pool

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pipeline.company_matching import normalize_vendor_name  # noqa: E402


ENRICHMENT_FIELDS = (
    "company_name",
    "normalized_name_key",
    "provenance_sources",
    "linkedin_url",
    "linkedin_url_candidate",
    "linkedin_enrichment_status",
    "website",
    "industry",
    "company_size",
    "headquarters",
    "founded_year",
    "description",
    "specialties",
    "locations",
    "followers",
    "source_website",
    "source_city",
    "source_industry_code",
    "source_specialties",
    "source_trade_hint",
)


def _normalize_url(url: str | None) -> str:
    if not url:
        return ""
    parsed = urlparse(url.strip().lower())
    path = parsed.path.rstrip("/")
    return f"{parsed.netloc}{path}"


def _coalesce(*values: str | None) -> str | None:
    for value in values:
        if value and str(value).strip():
            return str(value).strip()
    return None


def load_linkedin_cache(raw_path: Path | None = None) -> tuple[dict[str, dict], dict[str, dict]]:
    """Index raw LinkedIn records by normalized name key and by URL."""
    path = raw_path or RAW_JSON
    if not path.exists():
        return {}, {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    by_key: dict[str, dict] = {}
    by_url: dict[str, dict] = {}
    for rec in payload.get("records") or []:
        guess = rec.get("company_name_guess") or rec.get("company_name") or ""
        key = normalize_vendor_name(guess)
        if key and key not in by_key:
            by_key[key] = rec
        url_key = _normalize_url(rec.get("linkedin_company_url"))
        if url_key:
            by_url[url_key] = rec
    return by_key, by_url


def _linkedin_record_to_enrichment(rec: dict[str, Any]) -> dict[str, Any]:
    followers = None
    source_fields = rec.get("source_fields") or {}
    if source_fields.get("followers") is not None:
        followers = source_fields["followers"]
    description = rec.get("description")
    if followers is None and description:
        followers = _parse_followers(description)
    company_size = rec.get("company_size") or _parse_company_size(description)
    founded = rec.get("founded") or _parse_founded(description)
    specialties = rec.get("specialties") or _parse_specialties(description)
    status = "verified" if rec.get("scrape_status") == "ok" else "attempted_failed"
    return {
        "linkedin_url": rec.get("linkedin_company_url"),
        "linkedin_enrichment_status": status,
        "website": rec.get("website"),
        "industry": rec.get("industry"),
        "company_size": company_size,
        "headquarters": _coalesce(rec.get("headquarters"), rec.get("location")),
        "founded_year": founded,
        "description": description,
        "specialties": specialties,
        "locations": rec.get("location"),
        "followers": followers,
        "linkedin_company_name": rec.get("company_name"),
        "scrape_error": rec.get("scrape_error"),
        "linkedin_page_verified": rec.get("linkedin_page_verified"),
    }


def merge_pool_with_linkedin(
    pool: dict[str, Any],
    *,
    by_key: dict[str, dict],
    by_url: dict[str, dict],
) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    for row in pool.values():
        base = row.to_dict() if hasattr(row, "to_dict") else dict(row)
        key = base["normalized_name_key"]
        candidate_url = base.get("linkedin_url_candidate") or ""
        li = by_key.get(key)
        if not li:
            url_key = _normalize_url(candidate_url)
            li = by_url.get(url_key)

        li_fields: dict[str, Any] = {}
        if li:
            li_fields = _linkedin_record_to_enrichment(li)

        status = li_fields.get("linkedin_enrichment_status", "not_attempted")
        linkedin_url = _coalesce(
            li_fields.get("linkedin_url") if status == "verified" else None,
            candidate_url if status == "verified" else None,
            candidate_url,
        )

        record = {
            "company_name": base["company_name"],
            "normalized_name_key": key,
            "provenance_sources": base.get("provenance_sources") or [],
            "linkedin_url": linkedin_url,
            "linkedin_url_candidate": candidate_url,
            "linkedin_enrichment_status": status,
            "website": _coalesce(li_fields.get("website"), base.get("source_website")),
            "industry": li_fields.get("industry"),
            "source_industry_code": base.get("source_industry"),
            "company_size": li_fields.get("company_size"),
            "headquarters": _coalesce(li_fields.get("headquarters"), base.get("source_city")),
            "founded_year": li_fields.get("founded_year"),
            "description": li_fields.get("description"),
            "specialties": _coalesce(li_fields.get("specialties"), base.get("source_specialties")),
            "locations": _coalesce(li_fields.get("locations"), base.get("source_city")),
            "followers": li_fields.get("followers"),
            "source_website": base.get("source_website"),
            "source_city": base.get("source_city"),
            "source_industry_code": base.get("source_industry"),
            "source_specialties": base.get("source_specialties"),
            "source_trade_hint": base.get("source_trade_hint"),
        }
        if li_fields.get("linkedin_company_name"):
            record["linkedin_company_name"] = li_fields["linkedin_company_name"]
        if li_fields.get("scrape_error"):
            record["linkedin_scrape_error"] = li_fields["scrape_error"]
        enriched.append(record)
    return enriched


def write_enriched_csv(records: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    csv_fields = list(ENRICHMENT_FIELDS) + ["linkedin_company_name", "linkedin_scrape_error"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=csv_fields, extrasaction="ignore")
        writer.writeheader()
        for rec in records:
            row = dict(rec)
            row["provenance_sources"] = "|".join(rec.get("provenance_sources") or [])
            writer.writerow(row)


def write_enriched_json(
    records: list[dict[str, Any]],
    *,
    path: Path,
    metadata: dict[str, Any],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    artifact = {
        "schema_version": "1.0.0",
        "artifact_type": "companies_enriched",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "read_only": True,
        "db_writes": False,
        "record_count": len(records),
        "metadata": metadata,
        "records": records,
    }
    path.write_text(json.dumps(artifact, indent=2, default=str), encoding="utf-8")


def write_metadata(metadata: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(metadata, indent=2, default=str), encoding="utf-8")


def run_enrichment(
    *,
    raw_path: Path | None = None,
    fetch_missing: bool = False,
    max_fetch: int = 0,
    fetch_delay: float = 1.0,
    bc_construction_only: bool = True,
) -> dict[str, Any]:
    pool = build_source_pool(bc_construction_only=bc_construction_only)
    by_key, by_url = load_linkedin_cache(raw_path)

    fetched_count = 0
    if fetch_missing and max_fetch != 0:
        missing_urls: list[str] = []
        for row in pool.values():
            key = row.normalized_name_key
            if key in by_key:
                continue
            url = row.linkedin_url_candidate
            if url and _normalize_url(url) not in by_url:
                missing_urls.append(url)
        if max_fetch > 0:
            missing_urls = missing_urls[:max_fetch]
        if missing_urls:
            print(f"[enrich] Fetching {len(missing_urls)} LinkedIn pages (public)...", flush=True)
            new_records = batch_fetch_public_pages(missing_urls, delay_seconds=fetch_delay)
            fetched_count = len(new_records)
            for rec in new_records:
                rec_dict = rec.to_dict()
                rec_dict["company_name_guess"] = rec_dict.get("company_name")
                guess_key = normalize_vendor_name(rec_dict.get("company_name") or "")
                if guess_key:
                    by_key.setdefault(guess_key, rec_dict)
                url_key = _normalize_url(rec_dict.get("linkedin_company_url"))
                if url_key:
                    by_url[url_key] = rec_dict

    records = merge_pool_with_linkedin(pool, by_key=by_key, by_url=by_url)

    verified = sum(1 for r in records if r["linkedin_enrichment_status"] == "verified")
    attempted_failed = sum(1 for r in records if r["linkedin_enrichment_status"] == "attempted_failed")
    not_attempted = sum(1 for r in records if r["linkedin_enrichment_status"] == "not_attempted")

    metadata = {
        "version": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "pipeline": "linkedin_company_enrichment",
        "read_only": True,
        "db_writes": False,
        "registry_integration": False,
        "sources": [
            "enterprise_seed",
            "odbus_bc_naics23",
            "associations_sica_vica_mcabc_nrca",
            "market_registry_baseline",
        ],
        "pool_size": len(records),
        "linkedin_cache_records": len(by_key),
        "linkedin_fetched_this_run": fetched_count,
        "linkedin_enrichment_status": {
            "verified": verified,
            "attempted_failed": attempted_failed,
            "not_attempted": not_attempted,
        },
        "fetch_method": "public_unauthenticated_cache_reuse",
        "raw_artifact": str(raw_path or RAW_JSON),
    }

    write_enriched_json(records, path=COMPANIES_ENRICHED_JSON, metadata=metadata)
    write_enriched_csv(records, COMPANIES_ENRICHED_CSV)
    write_metadata(metadata, ENRICHMENT_METADATA_JSON)

    return {
        "records": records,
        "metadata": metadata,
    }
