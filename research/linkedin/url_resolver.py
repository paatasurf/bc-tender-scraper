"""Production-grade LinkedIn company URL resolution — multi-stage pipeline, no slug guessing."""

from __future__ import annotations

import asyncio
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from research.linkedin.company_cache import load_cached_company
from research.linkedin.paths import REPO_ROOT, SMOKE_TEST_JSON
from research.linkedin.source_pool import PoolCompany, build_source_pool
from research.linkedin.url_cache import get_cache_entry, load_url_cache, save_url_cache, set_cache_entry

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pipeline.company_matching import normalize_vendor_name  # noqa: E402

ENRICHMENT_CONFIDENCE_THRESHOLD = 90
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

LOGIN_MARKERS = ("/login", "/checkpoint/", "/authwall", "/uas/login")
REJECT_URL_PARTS = (
    "/company/unavailable",
    "/in/",
    "/school/",
    "/showcase/",
    "/learning/",
    "/jobs/view",
)
UNIVERSITY_TITLE_MARKERS = (
    "university",
    "college",
    "institute of technology",
    "polytechnic",
)
NUMBERED_BC_RE = re.compile(r"^\s*\d{5,}\s*bc\b", re.I)
LINKEDIN_COMPANY_RE = re.compile(
    r"https?://(?:[a-z]{2}\.)?linkedin\.com/company/[a-zA-Z0-9\-_%\.]+/?",
    re.I,
)
BC_MARKERS = (
    "british columbia",
    " bc",
    "vancouver",
    "victoria",
    "kelowna",
    "kamloops",
    "burnaby",
    "surrey",
    "richmond",
    "nanaimo",
    "prince george",
    "abbotsford",
)
CONSTRUCTION_MARKERS = (
    "construction",
    "contractor",
    "electrical",
    "mechanical",
    "roofing",
    "concrete",
    "civil",
    "hvac",
    "plumbing",
    "engineering",
    "building",
    "facilities",
)


@dataclass
class CompanyTarget:
    company_name: str
    normalized_name_key: str
    website: str | None = None
    city: str | None = None
    trade: str | None = None
    provenance_sources: list[str] = field(default_factory=list)

    @classmethod
    def from_pool_row(cls, row: PoolCompany) -> CompanyTarget:
        return cls(
            company_name=row.company_name,
            normalized_name_key=row.normalized_name_key,
            website=row.source_website,
            city=row.source_city,
            trade=row.source_trade_hint or row.source_industry,
            provenance_sources=sorted(row.provenance_sources),
        )

    @property
    def is_association_member(self) -> bool:
        return any(s.startswith("association_") for s in self.provenance_sources)


@dataclass
class UrlResolution:
    company_name: str
    normalized_name_key: str
    linkedin_url: str | None
    canonical_linkedin_url: str | None
    url_confidence: int
    match_method: str
    match_reason: str
    verification_method: str
    verification_status: str
    verification_date: str | None = None
    resolution_stage: str = ""
    rejection_reason: str | None = None
    resolved_at: str | None = None
    search_skipped: bool = False
    enrichable: bool = False
    false_match: bool = False
    provenance_sources: list[str] = field(default_factory=list)
    website: str | None = None
    city: str | None = None
    trade: str | None = None

    def __post_init__(self) -> None:
        if self.linkedin_url and not self.canonical_linkedin_url:
            self.canonical_linkedin_url = self.linkedin_url
        self.enrichable = (
            self.url_confidence >= ENRICHMENT_CONFIDENCE_THRESHOLD and bool(self.linkedin_url)
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["confidence"] = self.url_confidence
        data["enrichable"] = self.enrichable
        return data


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_company_url(raw: str | None) -> str | None:
    if not raw:
        return None
    raw = raw.strip().split("?")[0].split("#")[0]
    if not raw.startswith("http"):
        raw = "https://" + raw.lstrip("/")
    parsed = urlparse(raw)
    if "linkedin.com" not in (parsed.netloc or "").lower():
        return None
    match = re.search(r"/company/([^/?#]+)", parsed.path, re.I)
    if not match:
        return None
    slug = unquote(match.group(1)).strip("/")
    if not slug or slug.lower() == "unavailable":
        return None
    return f"https://www.linkedin.com/company/{slug}/"


def is_rejected_url_pattern(url: str | None) -> str | None:
    if not url:
        return "empty_url"
    lower = url.lower()
    for part in REJECT_URL_PARTS:
        if part in lower:
            return part.strip("/")
    if "/company/" not in lower:
        return "not_company_page"
    return None


def is_numbered_bc_shell(name: str) -> bool:
    return bool(NUMBERED_BC_RE.match(name or ""))


def is_unrealistic_target(target: CompanyTarget) -> str | None:
    if is_numbered_bc_shell(target.company_name):
        return "numbered_bc_ltd"
    if len(normalize_vendor_name(target.company_name)) < 3:
        return "name_too_short"
    return None


def _name_tokens(name: str) -> list[str]:
    cleaned = re.sub(r"\b\d{5,}\s*bc\s*ltd\.?\b", "", name, flags=re.I)
    cleaned = re.sub(
        r"\b(incorporated|inc|ltd|limited|corp|corporation|llc|lp|co|company|the|group)\b\.?",
        " ",
        cleaned,
        flags=re.I,
    )
    tokens = re.findall(r"[a-z0-9]{3,}", cleaned.lower())
    stop = {"and", "for", "with", "services", "service", "canada", "british", "columbia"}
    return [t for t in tokens if t not in stop]


def _domain_from_website(website: str | None) -> str | None:
    if not website:
        return None
    website = website.strip()
    if not website.startswith("http"):
        website = "https://" + website
    host = urlparse(website).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return host or None


def _domain_root(domain: str | None) -> str | None:
    if not domain:
        return None
    parts = domain.lower().split(".")
    return parts[0] if parts else None


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _score_candidate(
    *,
    url: str,
    title: str,
    snippet: str,
    target: CompanyTarget,
    stage_boost: int = 0,
) -> tuple[int, str]:
    slug = url.rstrip("/").split("/")[-1].lower()
    blob = f"{title} {snippet} {slug}".lower()
    tokens = _name_tokens(target.company_name)
    if not tokens:
        return 0, "no_name_tokens"

    matched = [t for t in tokens if t in blob or t in slug.replace("-", " ").replace("_", " ")]
    if not matched:
        return 0, "name_mismatch"

    score = 35 + min(25, len(matched) * 10)
    reasons = [f"name_tokens={matched}"]

    domain = _domain_from_website(target.website)
    root = _domain_root(domain)
    if root and (root in blob or root in slug.replace("-", "")):
        score += 22
        reasons.append(f"domain={domain}")

    city = (target.city or "").lower().strip()
    if city and city not in ("..", ".", "") and city in blob:
        score += 12
        reasons.append(f"city={city}")

    if any(m in blob for m in BC_MARKERS):
        score += 8
        reasons.append("province=BC")

    trade = (target.trade or "").lower()
    if trade:
        trade_words = [w for w in re.split(r"[\s,/\-]+", trade) if len(w) > 4]
        if any(w in blob for w in trade_words):
            score += 6
            reasons.append("trade_match")

    if target.is_association_member:
        score += 5
        reasons.append("association_member")

    if any(m in blob for m in CONSTRUCTION_MARKERS):
        score += 4
        reasons.append("industry_construction")

    slug_ratio = _similarity("-".join(tokens), slug.replace("_", "-"))
    score += int(slug_ratio * 12)
    reasons.append(f"name_sim={slug_ratio:.2f}")

    score += stage_boost
    return min(100, score), "; ".join(reasons)


def _duckduckgo_search(query: str, *, max_results: int = 8) -> list[dict[str, str]]:
    try:
        response = requests.post(
            "https://html.duckduckgo.com/html/",
            data={"q": query, "b": ""},
            headers={"User-Agent": USER_AGENT, "Referer": "https://html.duckduckgo.com/"},
            timeout=30,
        )
        response.raise_for_status()
    except requests.RequestException:
        return []

    soup = BeautifulSoup(response.text, "html.parser")
    hits: list[dict[str, str]] = []
    for block in soup.select(".result"):
        link = block.select_one("a.result__a")
        snippet_el = block.select_one(".result__snippet")
        if not link:
            continue
        href = link.get("href") or ""
        if "uddg=" in href:
            parsed = urlparse(href)
            href = unquote(parse_qs(parsed.query).get("uddg", [href])[0])
        url = normalize_company_url(href)
        if not url:
            continue
        hits.append(
            {
                "url": url,
                "title": link.get_text(" ", strip=True),
                "snippet": snippet_el.get_text(" ", strip=True) if snippet_el else "",
            }
        )
        if len(hits) >= max_results:
            break
    return hits


def _extract_urls_from_html(html: str, *, section: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    roots: list[Any] = []
    if section == "header":
        roots = soup.find_all(["header", "nav"])
    elif section == "footer":
        roots = soup.find_all("footer")
    elif section == "social":
        roots = soup.select('[class*="social"], [id*="social"], a[href*="linkedin.com"]')
    else:
        roots = [soup]

    if not roots:
        roots = [soup]

    found: list[str] = []
    seen: set[str] = set()
    for root in roots:
        for tag in root.find_all("a", href=True):
            href = tag.get("href") or ""
            if "linkedin.com/company/" not in href.lower():
                continue
            url = normalize_company_url(href)
            if url and url not in seen:
                seen.add(url)
                found.append(url)
    return found


def extract_linkedin_from_website_structured(
    website: str | None,
) -> list[tuple[str, str, int]]:
    """Return (url, section, stage_boost) from official website."""
    if not website:
        return []
    website = website.strip()
    if not website.startswith("http"):
        website = "https://" + website

    page_specs = [
        ("", "body", 0),
        ("contact", "contact", 8),
        ("contact-us", "contact", 8),
        ("about", "body", 4),
        ("about-us", "body", 4),
    ]
    section_boost = {"header": 10, "footer": 8, "contact": 12, "social": 14, "body": 6}

    results: list[tuple[str, str, int]] = []
    seen: set[str] = set()

    for suffix, page_kind, _ in page_specs:
        page_url = website if not suffix else urljoin(website.rstrip("/") + "/", suffix)
        try:
            response = requests.get(
                page_url,
                headers={"User-Agent": USER_AGENT},
                timeout=20,
                allow_redirects=True,
            )
            if response.status_code >= 400:
                continue
            html = response.text
            sections = ["header", "footer", "social", "body"] if page_kind == "contact" else ["header", "footer", "social"]
            for section in sections:
                for url in _extract_urls_from_html(html, section=section):
                    if url in seen:
                        continue
                    seen.add(url)
                    boost = section_boost.get(section, 0) + (4 if page_kind == "contact" else 0)
                    results.append((url, f"website_{section}_{page_kind or 'home'}", boost))
        except requests.RequestException:
            continue
    return results


def _load_stage1_verified(key: str, company_name: str, cache: dict[str, Any]) -> dict[str, Any] | None:
    cached = get_cache_entry(key, cache=cache)
    if cached and cached.get("linkedin_url") and int(cached.get("url_confidence") or 0) >= ENRICHMENT_CONFIDENCE_THRESHOLD:
        return {
            "url": cached["linkedin_url"],
            "confidence": int(cached["url_confidence"]),
            "method": "local_cache",
            "reason": cached.get("match_reason") or "Previously verified in url_cache.json",
            "verification_method": "local_cache",
            "stage": "stage1_cache",
        }

    scrape = load_cached_company(company_name)
    if scrape:
        record = scrape.get("record") or {}
        if record.get("scrape_status") == "ok":
            url = normalize_company_url(record.get("linkedin_company_url"))
            if url:
                return {
                    "url": url,
                    "confidence": 100,
                    "method": "scrape_cache_verified",
                    "reason": "Prior authenticated scrape succeeded",
                    "verification_method": "scrape_cache",
                    "stage": "stage1_scrape_cache",
                }

    if SMOKE_TEST_JSON.is_file():
        payload = json.loads(SMOKE_TEST_JSON.read_text(encoding="utf-8"))
        for row in payload.get("results") or []:
            if not row.get("success"):
                continue
            if normalize_vendor_name(row.get("company_name") or "") != key:
                continue
            url = normalize_company_url(
                (row.get("verification") or {}).get("final_url") or row.get("linkedin_url")
            )
            if url:
                return {
                    "url": url,
                    "confidence": 100,
                    "method": "smoke_test_verified",
                    "reason": "Passed authenticated smoke test",
                    "verification_method": "smoke_test",
                    "stage": "stage1_smoke_test",
                }
    return None


def _is_university_mismatch(title: str, tokens: list[str]) -> bool:
    lower = title.lower()
    if not any(m in lower for m in UNIVERSITY_TITLE_MARKERS):
        return False
    return not any(t in lower for t in tokens)


async def verify_url_authenticated(page: Any, url: str, target: CompanyTarget) -> dict[str, Any]:
    from research.linkedin.session import is_login_url

    result: dict[str, Any] = {
        "url": url,
        "ok": False,
        "reason": None,
        "final_url": None,
        "page_title": None,
        "page_website": None,
    }
    reject = is_rejected_url_pattern(url)
    if reject:
        result["reason"] = reject
        return result
    try:
        response = await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(1200)
        result["final_url"] = page.url
        result["page_title"] = await page.title()

        if is_login_url(page.url):
            result["reason"] = "login_redirect"
            return result
        if "/company/unavailable" in page.url.lower():
            result["reason"] = "unavailable"
            return result
        if response and response.status == 404:
            result["reason"] = "404"
            return result

        title_lower = (result["page_title"] or "").lower()
        tokens = _name_tokens(target.company_name)
        if _is_university_mismatch(title_lower, tokens):
            result["reason"] = "university_page"
            return result

        matched = [t for t in tokens if t in title_lower]
        if not matched and tokens:
            result["reason"] = "wrong_company_title_mismatch"
            return result

        page_website = await page.evaluate(
            """() => {
                const link = document.querySelector('a[href^="http"]:not([href*="linkedin"])');
                if (link) return link.href;
                const meta = document.querySelector('meta[property="og:url"]');
                return meta ? meta.content : null;
            }"""
        )
        result["page_website"] = page_website

        expected_domain = _domain_from_website(target.website)
        if expected_domain and page_website:
            page_domain = _domain_from_website(str(page_website))
            if page_domain and _domain_root(expected_domain) and _domain_root(page_domain):
                if _domain_root(expected_domain) != _domain_root(page_domain):
                    sim = _similarity(_domain_root(expected_domain) or "", _domain_root(page_domain) or "")
                    if sim < 0.5:
                        result["reason"] = "mismatched_website"
                        return result

        canonical = normalize_company_url(result["final_url"] or url)
        result["canonical_url"] = canonical
        result["ok"] = True
        result["reason"] = f"authenticated_ok tokens={matched}"
    except Exception as exc:
        result["reason"] = f"verify_error:{type(exc).__name__}"
    return result


def _build_resolution(
    target: CompanyTarget,
    *,
    hit: dict[str, Any],
    verification_status: str = "verified",
    search_skipped: bool = False,
    false_match: bool = False,
) -> UrlResolution:
    url = normalize_company_url(hit.get("url") or hit.get("linkedin_url"))
    now = _utc_now()
    return UrlResolution(
        company_name=target.company_name,
        normalized_name_key=target.normalized_name_key,
        linkedin_url=url,
        canonical_linkedin_url=url,
        url_confidence=int(hit.get("confidence") or hit.get("url_confidence") or 0),
        match_method=str(hit.get("method") or hit.get("match_method") or "unknown"),
        match_reason=str(hit.get("reason") or hit.get("match_reason") or ""),
        verification_method=str(hit.get("verification_method") or "authenticated_browser"),
        verification_status=verification_status,
        verification_date=now,
        resolution_stage=str(hit.get("stage") or ""),
        rejection_reason=hit.get("rejection_reason"),
        resolved_at=now,
        search_skipped=search_skipped,
        false_match=false_match,
        provenance_sources=target.provenance_sources,
        website=target.website,
        city=target.city,
        trade=target.trade,
    )


async def _try_candidates(
    target: CompanyTarget,
    candidates: list[tuple[str, str, int, str, str]],
    *,
    page: Any,
    stage: str,
    url_registry: dict[str, str],
) -> UrlResolution | None:
    for method, url, base_score, reason, verification_method in candidates:
        if is_rejected_url_pattern(url):
            continue
        if url in url_registry and url_registry[url] != target.normalized_name_key:
            continue

        auth = await verify_url_authenticated(page, url, target)
        if not auth.get("ok"):
            continue

        canonical = normalize_company_url(auth.get("canonical_url") or auth.get("final_url") or url)
        if not canonical:
            continue

        adj = min(100, base_score + 5)
        hit = {
            "url": canonical,
            "confidence": adj,
            "method": method,
            "reason": f"{reason}; {auth.get('reason')}",
            "verification_method": verification_method,
            "stage": stage,
        }
        resolution = _build_resolution(target, hit=hit)
        if resolution.enrichable:
            url_registry[canonical] = target.normalized_name_key
            return resolution
    return None


async def _resolve_one_async(
    target: CompanyTarget,
    *,
    page: Any,
    cache: dict[str, Any],
    url_registry: dict[str, str],
) -> UrlResolution:
    skip = is_unrealistic_target(target)
    if skip:
        res = _build_resolution(
            target,
            hit={
                "url": None,
                "confidence": 0,
                "method": "skipped",
                "reason": skip,
                "verification_method": "rules",
                "stage": "skipped",
                "rejection_reason": skip,
            },
            verification_status="rejected",
        )
        set_cache_entry(target.normalized_name_key, res.to_dict(), cache=cache)
        return res

    stage1 = _load_stage1_verified(target.normalized_name_key, target.company_name, cache)
    if stage1:
        res = _build_resolution(target, hit=stage1, search_skipped=True)
        if res.linkedin_url:
            url_registry[res.linkedin_url] = target.normalized_name_key
        set_cache_entry(target.normalized_name_key, res.to_dict(), cache=cache)
        return res

    # Stage 2 — official website
    website_candidates: list[tuple[str, str, int, str, str]] = []
    for url, section, boost in extract_linkedin_from_website_structured(target.website):
        score, reason = _score_candidate(
            url=url,
            title=target.company_name,
            snippet=section,
            target=target,
            stage_boost=boost,
        )
        if score >= 50:
            website_candidates.append(
                (f"website_{section}", url, max(92, score), f"{section}; {reason}", "website_link")
            )
    website_candidates.sort(key=lambda x: x[2], reverse=True)
    hit = await _try_candidates(
        target,
        website_candidates[:3],
        page=page,
        stage="stage2_website",
        url_registry=url_registry,
    )
    if hit:
        set_cache_entry(target.normalized_name_key, hit.to_dict(), cache=cache)
        return hit

    # Stage 3 — search with city + BC
    city = (target.city or "").strip()
    if city and city not in ("..", ".", ""):
        query = f'site:linkedin.com/company "{target.company_name}" "{city}" BC'
        search_hits = _duckduckgo_search(query)
        stage3: list[tuple[str, str, int, str, str]] = []
        for row in search_hits:
            score, reason = _score_candidate(
                url=row["url"],
                title=row["title"],
                snippet=row["snippet"],
                target=target,
                stage_boost=10,
            )
            if score >= 45:
                stage3.append(("search_city_bc", row["url"], min(96, score + 6), reason, "search_city_bc"))
        stage3.sort(key=lambda x: x[2], reverse=True)
        hit = await _try_candidates(
            target,
            stage3[:3],
            page=page,
            stage="stage3_search_city_bc",
            url_registry=url_registry,
        )
        if hit:
            set_cache_entry(target.normalized_name_key, hit.to_dict(), cache=cache)
            return hit

    # Stage 4 — general search
    query = f'site:linkedin.com/company "{target.company_name}"'
    search_hits = _duckduckgo_search(query)
    stage4: list[tuple[str, str, int, str, str]] = []
    for row in search_hits:
        score, reason = _score_candidate(
            url=row["url"],
            title=row["title"],
            snippet=row["snippet"],
            target=target,
            stage_boost=4,
        )
        if score >= 40:
            stage4.append(("search_general", row["url"], min(92, score + 2), reason, "search_general"))
    stage4.sort(key=lambda x: x[2], reverse=True)
    hit = await _try_candidates(
        target,
        stage4[:3],
        page=page,
        stage="stage4_search_general",
        url_registry=url_registry,
    )
    if hit:
        set_cache_entry(target.normalized_name_key, hit.to_dict(), cache=cache)
        return hit

    # Stage 5 — fuzzy ranking across all search hits combined
    all_hits = _duckduckgo_search(f'site:linkedin.com/company "{target.company_name}" BC')
    all_hits.extend(_duckduckgo_search(f'site:linkedin.com/company "{target.company_name}"'))
    stage5: list[tuple[str, str, int, str, str]] = []
    seen_urls: set[str] = set()
    for row in all_hits:
        url = row["url"]
        if url in seen_urls:
            continue
        seen_urls.add(url)
        score, reason = _score_candidate(
            url=url,
            title=row["title"],
            snippet=row["snippet"],
            target=target,
            stage_boost=0,
        )
        if score >= 35:
            stage5.append(("fuzzy_ranked", url, score, reason, "fuzzy_match"))
    stage5.sort(key=lambda x: x[2], reverse=True)
    hit = await _try_candidates(
        target,
        stage5[:5],
        page=page,
        stage="stage5_fuzzy",
        url_registry=url_registry,
    )
    if hit:
        set_cache_entry(target.normalized_name_key, hit.to_dict(), cache=cache)
        return hit

    unresolved = _build_resolution(
        target,
        hit={
            "url": None,
            "confidence": 0,
            "method": "unresolved",
            "reason": "No candidate passed multi-stage verification",
            "verification_method": "authenticated_browser",
            "stage": "unresolved",
            "rejection_reason": "no_verified_candidate",
        },
        verification_status="unresolved",
    )
    set_cache_entry(target.normalized_name_key, unresolved.to_dict(), cache=cache)
    return unresolved


async def resolve_batch_async(
    targets: list[CompanyTarget],
    *,
    refresh: bool = False,
    verify_live: bool = True,
) -> list[UrlResolution]:
    from research.linkedin.scraper.persistent_browser import persistent_browser_context
    from research.linkedin.session import profile_is_initialized

    cache = load_url_cache()
    results: list[UrlResolution] = []
    url_registry: dict[str, str] = {}

    for _key, entry in (cache.get("entries") or {}).items():
        url = entry.get("canonical_linkedin_url") or entry.get("linkedin_url")
        if url and int(entry.get("url_confidence") or 0) >= ENRICHMENT_CONFIDENCE_THRESHOLD:
            url_registry[str(url)] = str(_key)

    use_auth = verify_live and profile_is_initialized()
    if not use_auth:
        raise RuntimeError(
            "Authenticated browser profile required for production-grade URL verification. "
            "Run: python research/linkedin/scripts/login_profile.py"
        )

    async with persistent_browser_context(headless=True) as page:
        for index, target in enumerate(targets):
            if not refresh:
                cached = get_cache_entry(target.normalized_name_key, cache=cache)
                if cached is not None:
                    res = _build_resolution(
                        target,
                        hit={
                            "url": cached.get("canonical_linkedin_url") or cached.get("linkedin_url"),
                            "confidence": cached.get("url_confidence") or cached.get("confidence") or 0,
                            "method": cached.get("match_method") or "local_cache",
                            "reason": cached.get("match_reason") or "cached",
                            "verification_method": cached.get("verification_method") or "local_cache",
                            "stage": cached.get("resolution_stage") or "stage1_cache",
                        },
                        search_skipped=True,
                    )
                    results.append(res)
                    continue

            resolution = await _resolve_one_async(
                target,
                page=page,
                cache=cache,
                url_registry=url_registry,
            )
            results.append(resolution)
            save_url_cache(cache)
            if index < len(targets) - 1:
                await asyncio.sleep(1.0)

    return results


def priority_key(row: PoolCompany) -> tuple[int, str]:
    score = 0
    sources = row.provenance_sources
    if any(s.startswith("association_") for s in sources):
        score += 1000
    if "enterprise_seed" in sources:
        score += 500
    if row.source_website:
        score += 100
    if "market_registry_baseline" in sources:
        score += 50
    if "odbus_bc_naics23" in sources:
        score += 10
    return (-score, row.normalized_name_key)


def build_resolution_targets(*, bc_construction_only: bool = True) -> list[CompanyTarget]:
    pool = build_source_pool(bc_construction_only=bc_construction_only)
    rows = sorted(pool.values(), key=priority_key)
    targets: list[CompanyTarget] = []
    for row in rows:
        target = CompanyTarget.from_pool_row(row)
        if is_unrealistic_target(target):
            continue
        targets.append(target)
    return targets


def build_association_targets(*, limit: int | None = None) -> list[CompanyTarget]:
    pool = build_source_pool(bc_construction_only=True)
    rows = [
        row
        for row in pool.values()
        if any(s.startswith("association_") for s in row.provenance_sources)
    ]
    rows = sorted(rows, key=priority_key)
    targets: list[CompanyTarget] = []
    for row in rows:
        target = CompanyTarget.from_pool_row(row)
        if is_unrealistic_target(target):
            continue
        targets.append(target)
        if limit and len(targets) >= limit:
            break
    return targets


def get_enrichable_url(normalized_name_key: str) -> UrlResolution | None:
    entry = get_cache_entry(normalized_name_key)
    if not entry:
        return None
    url = entry.get("canonical_linkedin_url") or entry.get("linkedin_url")
    if not url:
        return None
    confidence = int(entry.get("url_confidence") or entry.get("confidence") or 0)
    if confidence < ENRICHMENT_CONFIDENCE_THRESHOLD:
        return None
    return UrlResolution(
        company_name=entry.get("company_name") or normalized_name_key,
        normalized_name_key=normalized_name_key,
        linkedin_url=url,
        canonical_linkedin_url=url,
        url_confidence=confidence,
        match_method=str(entry.get("match_method") or "local_cache"),
        match_reason=str(entry.get("match_reason") or ""),
        verification_method=str(entry.get("verification_method") or "local_cache"),
        verification_status=str(entry.get("verification_status") or "cached"),
        verification_date=entry.get("verification_date"),
        resolution_stage=str(entry.get("resolution_stage") or "stage1_cache"),
        resolved_at=entry.get("resolved_at"),
        search_skipped=True,
        enrichable=True,
    )


def compute_benchmark_statistics(
    results: list[UrlResolution],
    *,
    pool_size: int,
    cache_hits: int,
) -> dict[str, Any]:
    with_url = [r for r in results if r.linkedin_url]
    verified = [r for r in results if r.enrichable]
    false_matches = [r for r in results if r.false_match]
    confidences = [r.url_confidence for r in with_url if r.url_confidence > 0]
    avg_conf = round(sum(confidences) / len(confidences), 1) if confidences else 0.0
    enrichable_rate = round(100 * len(verified) / max(1, len(results)), 1)
    estimated_pool_coverage = round(100 * len(verified) / max(1, pool_size), 1)

    stage_counts: dict[str, int] = {}
    for r in verified:
        stage_counts[r.resolution_stage] = stage_counts.get(r.resolution_stage, 0) + 1

    return {
        "benchmark_size": len(results),
        "association_pool_size": pool_size,
        "urls_resolved": len(with_url),
        "verified_enrichable": len(verified),
        "verified_rate_pct": enrichable_rate,
        "false_matches": len(false_matches),
        "unresolved": len([r for r in results if r.verification_status == "unresolved"]),
        "rejected": len([r for r in results if r.verification_status == "rejected"]),
        "average_confidence": avg_conf,
        "cache_hit_rate_pct": round(100 * cache_hits / max(1, len(results)), 1),
        "estimated_full_pool_coverage_pct": estimated_pool_coverage,
        "confidence_threshold": ENRICHMENT_CONFIDENCE_THRESHOLD,
        "resolution_stages": stage_counts,
        "target_accuracy_pct": 95.0,
        "meets_target": enrichable_rate >= 95.0,
    }
