"""Curated company LinkedIn verification — Class A/B only, no generic discovery."""

from __future__ import annotations

import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from research.enrichment.paths import COMPANY_PROFILES_JSON
from research.linkedin.paths import REPO_ROOT
from research.linkedin.url_resolver import (
    CompanyTarget,
    _name_tokens,
    _score_candidate,
    extract_linkedin_from_website_structured,
    is_rejected_url_pattern,
    normalize_company_url,
    verify_url_authenticated,
)

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pipeline.company_matching import normalize_vendor_name  # noqa: E402

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
PARKING_MARKERS = (
    "domain is for sale",
    "this domain has expired",
    "buy this domain",
    "parked free",
    "godaddy",
    "namecheap",
    "coming soon",
    "under construction",
)

LINKEDIN_VERIFIED_STATUS = "LinkedIn Verified"
LINKEDIN_NOT_FOUND_STATUS = "Not Found"
LINKEDIN_FAILED_STATUS = "Failed"
LinkedInVerificationStatus = Literal["LinkedIn Verified", "Not Found", "Failed"] | None
SourceClass = Literal["A", "B"]


@dataclass
class CuratedCompany:
    normalized_name: str
    company_name: str
    website: str | None
    linkedin_url: str | None
    city: str | None
    province: str | None
    source_class: SourceClass
    sources_present: list[str]
    website_verified_flag: bool
    primary_trade: str | None = None

    @classmethod
    def from_profile(cls, record: dict[str, Any]) -> CuratedCompany | None:
        source_class = record.get("source_confidence")
        if source_class not in ("A", "B"):
            return None
        presence = record.get("presence") or {}
        evidence = record.get("evidence") or {}
        business = record.get("business") or {}
        return cls(
            normalized_name=str(record.get("normalized_name") or ""),
            company_name=str(record.get("canonical_company_name") or "").strip(),
            website=presence.get("website") or None,
            linkedin_url=presence.get("linkedin_url") or None,
            city=presence.get("city") or None,
            province=presence.get("province") or "BC",
            source_class=source_class,
            sources_present=list(record.get("sources_present") or []),
            website_verified_flag=bool(evidence.get("website_verified")),
            primary_trade=business.get("primary_trade"),
        )

    def to_target(self) -> CompanyTarget:
        return CompanyTarget(
            company_name=self.company_name,
            normalized_name_key=self.normalized_name or normalize_vendor_name(self.company_name),
            website=self.website,
            city=self.city,
            trade=self.primary_trade,
            provenance_sources=self.sources_present,
        )


@dataclass
class CompanyVerificationResult:
    normalized_name: str
    company_name: str
    source_class: SourceClass
    status: LinkedInVerificationStatus
    linkedin_verified: bool
    website: str | None
    website_verified: bool
    website_verification_method: str
    website_verification_reason: str | None = None
    linkedin_url: str | None = None
    linkedin_discovery_method: str | None = None
    linkedin_confidence: int | None = None
    linkedin_skip_reason: str | None = None
    city: str | None = None
    province: str | None = None
    sources_present: list[str] = field(default_factory=list)
    verified_at: str | None = None

    def __post_init__(self) -> None:
        if self.verified_at is None and self.status is not None:
            self.verified_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_curated_companies(
    *,
    profiles_path: Path | None = None,
    source_classes: tuple[SourceClass, ...] = ("A", "B"),
    with_website_only: bool = False,
) -> list[CuratedCompany]:
    path = COMPANY_PROFILES_JSON if profiles_path is None else profiles_path
    payload = __import__("json").loads(path.read_text(encoding="utf-8"))
    companies: list[CuratedCompany] = []
    for record in payload.get("records") or []:
        if record.get("source_confidence") not in source_classes:
            continue
        company = CuratedCompany.from_profile(record)
        if not company or not company.company_name:
            continue
        if with_website_only and not company.website:
            continue
        companies.append(company)
    return companies


def _normalize_http_url(raw: str) -> str:
    raw = raw.strip()
    if not raw.startswith("http"):
        raw = "https://" + raw.lstrip("/")
    return raw.split("#")[0].split("?")[0]


def _domain_root_from_url(url: str | None) -> str | None:
    if not url:
        return None
    host = urlparse(url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    parts = host.split(".")
    return parts[0] if parts else None


def verify_official_website(company: CuratedCompany) -> dict[str, Any]:
    """HTTP verification of the curated official website (metadata only)."""
    if company.website_verified_flag and company.website:
        return {
            "verified": True,
            "method": "curated_flag",
            "reason": "website_verified=true in curated profile",
            "final_url": _normalize_http_url(company.website),
        }

    if not company.website:
        return {
            "verified": False,
            "method": "none",
            "reason": "no_official_website_in_curated_record",
            "final_url": None,
        }

    url = _normalize_http_url(company.website)
    try:
        response = requests.get(
            url,
            headers={"User-Agent": USER_AGENT},
            timeout=25,
            allow_redirects=True,
        )
    except requests.RequestException as exc:
        return {
            "verified": False,
            "method": "http",
            "reason": f"request_error:{type(exc).__name__}",
            "final_url": url,
        }

    final_url = response.url
    if response.status_code >= 400:
        return {
            "verified": False,
            "method": "http",
            "reason": f"http_{response.status_code}",
            "final_url": final_url,
        }

    html_lower = response.text[:80000].lower()
    if any(marker in html_lower for marker in PARKING_MARKERS):
        return {
            "verified": False,
            "method": "http",
            "reason": "parking_or_placeholder_page",
            "final_url": final_url,
        }

    soup = BeautifulSoup(response.text, "html.parser")
    title_text = soup.title.get_text(" ", strip=True) if soup.title else ""
    tokens = _name_tokens(company.company_name)
    if tokens:
        title_lower = title_text.lower()
        token_hits = sum(1 for token in tokens if token in title_lower)
        domain_root = _domain_root_from_url(final_url)
        domain_hits = sum(1 for token in tokens[:3] if domain_root and token in domain_root)
        if token_hits == 0 and domain_hits == 0:
            return {
                "verified": False,
                "method": "http",
                "reason": "website_title_domain_mismatch",
                "final_url": final_url,
            }

    return {
        "verified": True,
        "method": "http",
        "reason": "http_ok",
        "final_url": final_url,
    }


def _extract_linkedin_from_homepage(website: str) -> list[tuple[str, str, int]]:
    website = _normalize_http_url(website)
    try:
        response = requests.get(
            website,
            headers={"User-Agent": USER_AGENT},
            timeout=20,
            allow_redirects=True,
        )
        if response.status_code >= 400:
            return []
    except requests.RequestException:
        return []

    found: list[tuple[str, str, int]] = []
    seen: set[str] = set()
    soup = BeautifulSoup(response.text, "html.parser")
    for tag in soup.find_all("a", href=True):
        href = tag.get("href") or ""
        if "linkedin.com/company/" not in href.lower():
            continue
        url = normalize_company_url(href)
        if not url or url in seen or is_rejected_url_pattern(url):
            continue
        seen.add(url)
        section = "footer" if tag.find_parent("footer") else "header" if tag.find_parent(["header", "nav"]) else "body"
        boost = {"footer": 8, "header": 10, "body": 6}.get(section, 6)
        found.append((url, f"website_{section}_home", boost))
    return found


def discover_linkedin_curated(
    company: CuratedCompany,
    *,
    deep_website_scan: bool = False,
) -> dict[str, Any]:
    """Collect LinkedIn candidates from curated profile URL and official website links only."""
    target = company.to_target()
    candidates: dict[str, tuple[str, int, str]] = {}

    curated_url = normalize_company_url(company.linkedin_url)
    if curated_url and not is_rejected_url_pattern(curated_url):
        candidates[curated_url] = ("curated_profile", 92, "linkedin_url from curated company profile")

    if company.website:
        website_url = _normalize_http_url(company.website)
        extracted = (
            extract_linkedin_from_website_structured(website_url)
            if deep_website_scan
            else _extract_linkedin_from_homepage(website_url)
        )
        for url, section, boost in extracted:
            if is_rejected_url_pattern(url):
                continue
            score, reason = _score_candidate(
                url=url,
                title="",
                snippet=section,
                target=target,
                stage_boost=boost,
            )
            if score >= 45:
                prev = candidates.get(url)
                if not prev or prev[1] < score:
                    candidates[url] = (section, score, reason)

    if not candidates:
        return {
            "linkedin_url": None,
            "method": None,
            "confidence": None,
            "skip_reason": "no_curated_linkedin_candidate",
        }

    ranked = sorted(
        ((url, method, score, reason) for url, (method, score, reason) in candidates.items()),
        key=lambda row: row[2],
        reverse=True,
    )
    best_url, best_method, best_score, best_reason = ranked[0]
    if len(ranked) > 1:
        second_url, _, second_score, _ = ranked[1]
        if second_url != best_url and abs(best_score - second_score) < 12:
            return {
                "linkedin_url": None,
                "method": None,
                "confidence": None,
                "skip_reason": f"ambiguous_curated_candidates:{best_url}|{second_url}",
            }

    if best_score < 50 and best_method != "curated_profile":
        return {
            "linkedin_url": None,
            "method": None,
            "confidence": None,
            "skip_reason": "candidate_below_confidence_threshold",
        }

    return {
        "linkedin_url": best_url,
        "method": best_method,
        "confidence": best_score,
        "reason": best_reason,
        "skip_reason": None,
    }


async def verify_linkedin_authenticated(
    company: CuratedCompany,
    linkedin_url: str,
    *,
    page: Any,
) -> dict[str, Any]:
    auth = await verify_url_authenticated(page, linkedin_url, company.to_target())
    if auth.get("ok"):
        return {
            "verified": True,
            "url": normalize_company_url(auth.get("canonical_url") or auth.get("final_url") or linkedin_url),
            "reason": auth.get("reason"),
        }
    return {"verified": False, "url": None, "reason": auth.get("reason")}


async def verify_company_async(
    company: CuratedCompany,
    *,
    page: Any | None = None,
    deep_website_scan: bool = False,
    confirm_linkedin: bool = True,
) -> CompanyVerificationResult:
    website_result = verify_official_website(company)

    result = CompanyVerificationResult(
        normalized_name=company.normalized_name,
        company_name=company.company_name,
        source_class=company.source_class,
        status=None,
        linkedin_verified=False,
        website=company.website,
        website_verified=bool(website_result.get("verified")),
        website_verification_method=str(website_result.get("method") or "none"),
        website_verification_reason=website_result.get("reason"),
        city=company.city,
        province=company.province,
        sources_present=company.sources_present,
    )

    if not confirm_linkedin or page is None:
        result.linkedin_skip_reason = "linkedin_confirmation_skipped"
        return result

    linkedin_result = discover_linkedin_curated(company, deep_website_scan=deep_website_scan)
    candidate_url = linkedin_result.get("linkedin_url")
    if not candidate_url:
        result.status = LINKEDIN_NOT_FOUND_STATUS
        result.linkedin_skip_reason = linkedin_result.get("skip_reason")
        return result

    auth = await verify_linkedin_authenticated(company, candidate_url, page=page)
    result.linkedin_discovery_method = linkedin_result.get("method")
    result.linkedin_confidence = linkedin_result.get("confidence")

    if auth.get("verified"):
        result.status = LINKEDIN_VERIFIED_STATUS
        result.linkedin_verified = True
        result.linkedin_url = auth.get("url") or candidate_url
        return result

    reason = str(auth.get("reason") or "unknown")
    result.linkedin_skip_reason = f"linkedin_not_confirmed:{reason}"
    if reason.startswith("verify_error") or reason == "login_redirect":
        result.status = LINKEDIN_FAILED_STATUS
    else:
        result.status = LINKEDIN_NOT_FOUND_STATUS
    return result


def _result_bucket(row: CompanyVerificationResult) -> str:
    if row.status == LINKEDIN_VERIFIED_STATUS:
        return "linkedin_verified"
    if row.status == LINKEDIN_NOT_FOUND_STATUS:
        return "not_found"
    if row.status == LINKEDIN_FAILED_STATUS:
        return "failed"
    return "skipped"


def compute_class_statistics(results: list[CompanyVerificationResult]) -> dict[str, Any]:
    stats: dict[str, Any] = {}
    for source_class in ("A", "B"):
        rows = [row for row in results if row.source_class == source_class]
        total = len(rows)
        buckets = {_result_bucket(row) for row in rows}
        counts = {
            "linkedin_verified": sum(1 for row in rows if _result_bucket(row) == "linkedin_verified"),
            "not_found": sum(1 for row in rows if _result_bucket(row) == "not_found"),
            "failed": sum(1 for row in rows if _result_bucket(row) == "failed"),
            "skipped": sum(1 for row in rows if _result_bucket(row) == "skipped"),
        }
        stats[source_class] = {
            "total_companies": total,
            **counts,
            "linkedin_verified_pct": round((100.0 * counts["linkedin_verified"] / total), 2) if total else 0.0,
        }
        del buckets
    return stats


def compute_run_statistics(results: list[CompanyVerificationResult]) -> dict[str, Any]:
    total = len(results)
    counts = {
        "total_companies_processed": total,
        "linkedin_verified": sum(1 for row in results if _result_bucket(row) == "linkedin_verified"),
        "not_found": sum(1 for row in results if _result_bucket(row) == "not_found"),
        "failed": sum(1 for row in results if _result_bucket(row) == "failed"),
        "skipped": sum(1 for row in results if _result_bucket(row) == "skipped"),
    }
    if total:
        counts["linkedin_verified_pct"] = round((100.0 * counts["linkedin_verified"] / total), 2)
    else:
        counts["linkedin_verified_pct"] = 0.0
    return counts
