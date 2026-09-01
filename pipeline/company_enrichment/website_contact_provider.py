"""WebsiteContactProvider -- Phase 3A: direct-site contact enrichment for
already-known company domains.

STATUS: NOT PRODUCTION-READY. Implemented and unit-tested in isolation
only. Do not wire into _default_providers(), do not set ENRICHMENT_ENABLED,
do not apply any schema migration on this module's account, without a
separate, explicit authorization for each of those three steps -- none is
implied by this file existing or by its tests passing. Concretely blocked
on, at minimum:
  - Migration 035 (source_url/raw_value/extraction_method/verified_at/
    verified_by/verification_source_url columns + the field_attempt_log
    JSONB column + validation function -- design doc S2.1/S2.2) has not
    been written, reviewed, or applied anywhere, including locally. Adding
    it is explicitly its own future step requiring its own review, never
    a byproduct of a provider-code change.
  - Gate A (fetch+extract admission criteria, design doc S8.1) has not
    been run against the required 50-100-company expanded ground truth.
  - Gate B (discovery accuracy) has not been attempted at all (this phase
    doesn't implement discovery, so it is moot for now, but both gates
    are still required before any production write, design doc S8.3).
  - Playwright's Chromium browser binary is not installed by this
    project's Railway build (Nixpacks, no `playwright install` step
    configured -- see requirements.txt's own note) -- a real invocation
    on Railway would fail at first use today, independent of everything
    else above.

Scope, explicit and deliberate (docs/COMPANY_CONTACT_PROVIDER_PHASE3_DESIGN.md,
docs/COMPANY_CONTACT_DISCOVERY_DECISION.md):

  - Direct-site fetch+extract ONLY. No discovery: this provider never
    searches for a company's domain. It reads whatever is already on
    Company.website / Company.google_website (preferring EnrichmentRequest
    .website if a future caller ever populates it) and, if none is set,
    returns a clean no-match without making any network call at all.
    SearXNG, Google API, and any other domain-discovery mechanism are
    entirely out of scope for this phase -- Gate B (design doc S8.2) is
    unattempted and unresolved, and this provider's own admission requires
    both gates, not just this one (design doc S8.3).
  - Candidate-only, always. This provider NEVER sets verified=True -- it
    has no code path capable of doing so; write_enrichment_facts()
    (orchestrator.py, unchanged) already writes every new fact with
    verified=False and refuses to overwrite a verified=True row. That
    remains the only mechanism that can ever flip verified, entirely
    outside this file.
  - Not wired into _default_providers() by this change -- orchestrator.py
    is untouched. Reachable only by direct instantiation (as the tests do),
    matching the "implement, don't wire in yet" pattern already used for
    this provider throughout this session's review rounds.
  - `ProviderFact.confidence`/`source_url`/`raw_value`/`extraction_method`
    are populated on every fact this provider returns (design doc S2.2's
    "Option A" fields) -- persisting them to company_enrichment_fields
    requires a schema migration that has not been authorized or applied
    anywhere (not even locally); until then, write_enrichment_facts()
    simply doesn't read those three extra fields, and this module does not
    change that.

SSRF protection, robots.txt handling, and the redirect-walk logic below are
a direct port of the already-tested read-only research pilot's
implementation (the script that produced
exports/company_contact_discovery_pilot_v2.json), adapted from a throwaway
script into this provider's `lookup()` contract.
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
import urllib.error
import urllib.request
import urllib.robotparser
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import extruct
import trafilatura
from crawl4ai import AsyncWebCrawler, CrawlerRunConfig
from crawl4ai.async_configs import BrowserConfig
from sqlalchemy.orm import Session

from db.models import Company
from pipeline.company_enrichment.provider import (
    EnrichmentRequest,
    ProviderFact,
    ProviderResult,
)
from pipeline.company_matching import normalize_vendor_name
from pipeline.registry_verification.match_common import company_normalized_name

REQUEST_TIMEOUT_S = 20
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_REDIRECT_HOPS = 5
PROBE_BODY_SNIPPET_BYTES = 4096
USER_AGENT = (
    "bc-tender-scraper-website-contact-provider/0.1 "
    "(+read-only company enrichment; Phase 3A direct-site only, no discovery)"
)

PHONE_RE = re.compile(r"(?:\+?1[\s.\-]?)?\(?\b\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4}\b")
EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")

# SSRF denylist: loopback, private, link-local (incl. cloud metadata), and
# other non-routable/reserved ranges. Checked against every resolved IP for
# the host on every hop -- not just the initial URL.
_BLOCKED_NETWORKS = [
    ipaddress.ip_network(n)
    for n in (
        "127.0.0.0/8",
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "169.254.0.0/16",  # includes 169.254.169.254 cloud metadata
        "0.0.0.0/8",
        "::1/128",
        "fc00::/7",
        "fe80::/10",
    )
]
_BLOCKED_HOSTNAME_LITERALS = {
    "localhost",
    "localhost.localdomain",
    "metadata.google.internal",
}

FREE_EMAIL_PROVIDERS = {
    "gmail.com",
    "hotmail.com",
    "outlook.com",
    "yahoo.com",
    "icloud.com",
    "live.com",
    "aol.com",
    "protonmail.com",
    "msn.com",
}

_CANONICAL_PATH_RE = re.compile(r"^/($|contact|about)", re.IGNORECASE)

# Placeholder/sentinel values a data-entry or import pipeline can leave in a
# "website" field to mean "we don't have one" -- must never be treated as a
# real domain to fetch. Case-insensitive, matched against the whole
# (stripped) value.
_SENTINEL_WEBSITE_VALUES = {
    "n/a",
    "n.a.",
    "na",
    "nan",
    "unknown",
    "-",
    "--",
    "none",
    "null",
    "nil",
    "n/a.",
    "tbd",
    "pending",
    "not available",
    "not applicable",
    "no website",
    "no site",
    "no website found",
}

# A real company domain is a hostname with at least two dot-separated
# labels (e.g. "example.com", "www.example.co.uk") -- neither a bare
# single-word token nor a raw IP literal (rejected separately, below) is
# accepted as a "known domain" for Phase 3A's direct-site-only scope.
_VALID_HOSTNAME_RE = re.compile(
    r"^(?!-)[a-z0-9-]{1,63}(?<!-)(\.(?!-)[a-z0-9-]{1,63}(?<!-))+$",
    re.IGNORECASE,
)


def _looks_like_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host.strip("[]"))
        return True
    except ValueError:
        return False


def _normalize_website_candidate(raw: str | None) -> str | None:
    """Strict validation for a would-be Company.website / google_website /
    EnrichmentRequest.website value, run BEFORE any network call is even
    considered -- structurally, not just by convention: a value that
    fails here never reaches _check_robots()/_redirect_walk(), which is
    what would otherwise perform the first real DNS resolution. Rejects
    known placeholder/sentinel values, whitespace-containing garbage, bare
    IP literals (a company's own domain should be a hostname, not a raw
    IP -- rejecting this here also means a malformed IP-shaped value never
    reaches the SSRF guard's DNS step at all, rather than relying on that
    guard to reject it after already attempting a resolution), and
    anything that doesn't shape-match a real multi-label hostname. Returns
    a bare hostname (scheme/path/port stripped) or None."""
    if raw is None:
        return None
    value = raw.strip()
    if not value:
        return None
    if value.lower() in _SENTINEL_WEBSITE_VALUES:
        return None
    if any(c.isspace() for c in value):
        return None

    parsed = urlparse(value if "://" in value else f"//{value}", scheme="https")
    host = (parsed.netloc or parsed.path).strip("/").split("/")[0]
    if not host:
        return None
    host = host.split(":")[0]  # drop a trailing :port, if any

    if _looks_like_ip_literal(host):
        return None
    if not _VALID_HOSTNAME_RE.match(host):
        return None
    return host


class DnsResolutionError(Exception):
    pass


class SsrfBlockedError(Exception):
    pass


# ---------------------------------------------------------------------------
# SSRF / DNS guard -- applied to the initial URL AND every redirect hop.
# ---------------------------------------------------------------------------


def _resolve_ips(hostname: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror as e:
        raise DnsResolutionError(f"DNS resolution failed for {hostname}: {e}")
    addrs = sorted({str(info[4][0]) for info in infos})
    if not addrs:
        raise DnsResolutionError(f"No addresses resolved for {hostname}")
    return addrs


def _check_ip_ranges(hostname: str, addrs: list[str]) -> None:
    for addr in addrs:
        ip = ipaddress.ip_address(addr)
        for net in _BLOCKED_NETWORKS:
            if ip in net:
                raise SsrfBlockedError(
                    f"{hostname} resolves to {addr}, inside blocked range {net}"
                )


def _ssrf_and_dns_check(url: str) -> list[str]:
    """Scheme allowlist, literal-hostname denylist, DNS resolution (raises
    DnsResolutionError distinctly from SSRF), and IP-range rejection
    (raises SsrfBlockedError). Called before every hop -- initial URL and
    every redirect target -- never just once up front."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise SsrfBlockedError(f"Disallowed scheme: {parsed.scheme!r}")
    if not parsed.hostname:
        raise SsrfBlockedError("URL has no hostname")
    if parsed.hostname.lower() in _BLOCKED_HOSTNAME_LITERALS:
        raise SsrfBlockedError(f"Blocked hostname literal: {parsed.hostname}")
    addrs = _resolve_ips(parsed.hostname)
    _check_ip_ranges(parsed.hostname, addrs)
    return addrs


# ---------------------------------------------------------------------------
# robots.txt -- fetched with an explicit User-Agent, never
# RobotFileParser.read()'s default "Python-urllib/x.y", which several real
# sites 403 as a generic anti-bot rule, which makes Python's fail-closed
# default (disallow_all=True on any non-404/401 fetch error) look like a
# real robots.txt disallow when it was actually just a blocked fetch.
# ---------------------------------------------------------------------------


def _check_robots(domain: str, path: str = "/") -> dict:
    result: dict = {
        "robots_url": None,
        "robots_fetched": False,
        "allowed": True,
        "error": None,
    }
    for scheme in ("https", "http"):
        robots_url = f"{scheme}://{domain}/robots.txt"
        try:
            _ssrf_and_dns_check(robots_url)
        except (SsrfBlockedError, DnsResolutionError) as e:
            result["error"] = f"pre-fetch guard blocked robots.txt fetch: {e}"
            continue
        req = urllib.request.Request(robots_url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as resp:
                body = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                result.update(
                    robots_url=robots_url,
                    robots_fetched=True,
                    allowed=False,
                    error=f"HTTP {e.code} fetching robots.txt (fail-closed per RFC 9309)",
                )
                return result
            if e.code == 404:
                result.update(robots_url=robots_url, robots_fetched=False, allowed=True)
                return result
            result["error"] = f"HTTP {e.code} fetching robots.txt"
            continue
        except Exception as e:  # noqa: BLE001
            result["error"] = str(e)
            continue

        rp = urllib.robotparser.RobotFileParser()
        rp.set_url(robots_url)
        rp.parse(body.splitlines())
        result.update(
            robots_url=robots_url,
            robots_fetched=True,
            allowed=rp.can_fetch(USER_AGENT, path),
        )
        return result
    return result


# ---------------------------------------------------------------------------
# Redirect walk -- every hop (including the first) re-validated for
# scheme/DNS/SSRF before being requested. http fallback is permitted ONLY
# when hop 0's https attempt fails at the connection level -- never for a
# redirect target.
# ---------------------------------------------------------------------------


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        return None  # tells urllib not to auto-follow; caller inspects the 3xx response instead


def _manual_probe(url: str) -> dict:
    opener = urllib.request.build_opener(_NoRedirect)
    req = urllib.request.Request(url, method="GET", headers={"User-Agent": USER_AGENT})
    try:
        with opener.open(req, timeout=REQUEST_TIMEOUT_S) as resp:
            body = resp.read(PROBE_BODY_SNIPPET_BYTES)
            return {
                "status": resp.status,
                "headers": dict(resp.headers),
                "body": body,
                "error": None,
                "connection_level": False,
            }
    except urllib.error.HTTPError as e:
        try:
            body = e.read(PROBE_BODY_SNIPPET_BYTES)
        except Exception:  # noqa: BLE001
            body = b""
        return {
            "status": e.code,
            "headers": dict(e.headers or {}),
            "body": body,
            "error": str(e),
            "connection_level": False,
        }
    except urllib.error.URLError as e:
        reason = e.reason
        is_conn_level = isinstance(
            reason,
            (ConnectionRefusedError, ConnectionResetError, TimeoutError, OSError),
        )
        return {
            "status": None,
            "headers": {},
            "body": b"",
            "error": str(e),
            "connection_level": is_conn_level,
        }
    except TimeoutError as e:
        return {
            "status": None,
            "headers": {},
            "body": b"",
            "error": str(e),
            "connection_level": True,
        }
    except Exception as e:  # noqa: BLE001
        return {
            "status": None,
            "headers": {},
            "body": b"",
            "error": str(e),
            "connection_level": False,
        }


def _redirect_walk(domain: str) -> dict:
    hops: list[dict] = []
    current = f"https://{domain}/"
    tried_http_fallback = False

    for hop_index in range(MAX_REDIRECT_HOPS + 1):
        try:
            _ssrf_and_dns_check(current)
        except DnsResolutionError as e:
            if hop_index == 0:
                return {
                    "outcome": "dns_nxdomain",
                    "hop_index": hop_index,
                    "url": current,
                    "error": str(e),
                    "hops": hops,
                }
            return {
                "outcome": "redirect_blocked",
                "hop_index": hop_index,
                "url": current,
                "error": f"redirect target failed DNS re-validation: {e}",
                "hops": hops,
            }
        except SsrfBlockedError as e:
            outcome = "ssrf_blocked" if hop_index == 0 else "redirect_blocked"
            return {
                "outcome": outcome,
                "hop_index": hop_index,
                "url": current,
                "error": str(e),
                "hops": hops,
            }

        probe = _manual_probe(current)

        if probe["error"] is not None and probe["status"] is None:
            if (
                hop_index == 0
                and current.startswith("https://")
                and probe["connection_level"]
                and not tried_http_fallback
            ):
                tried_http_fallback = True
                current = "http://" + current[len("https://") :]
                hops.append(
                    {
                        "url": current.replace("http://", "https://", 1),
                        "status": None,
                        "note": "https connection-level failure, retrying hop 0 over http only",
                    }
                )
                continue
            return {
                "outcome": "domain_unavailable",
                "hop_index": hop_index,
                "url": current,
                "error": probe["error"],
                "hops": hops,
            }

        hops.append({"url": current, "status": probe["status"]})

        if probe["status"] in (301, 302, 303, 307, 308):
            location = probe["headers"].get("Location") or probe["headers"].get(
                "location"
            )
            if not location:
                return {
                    "outcome": "resolved",
                    "final_url": current,
                    "final_status": probe["status"],
                    "body_snippet": probe["body"].decode("utf-8", "replace"),
                    "hops": hops,
                }
            current = urljoin(current, location)
            continue

        return {
            "outcome": "resolved",
            "final_url": current,
            "final_status": probe["status"],
            "body_snippet": probe["body"].decode("utf-8", "replace"),
            "hops": hops,
        }

    return {"outcome": "too_many_redirects", "hops": hops, "final_url": current}


def _registrable_domain(host: str) -> str:
    parts = host.lower().removeprefix("www.").split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host.lower()


# ---------------------------------------------------------------------------
# Extraction -- extruct (JSON-LD/microdata) + Trafilatura (text) + mailto:/
# tel: fallback. Each extracted value keeps the highest-priority extraction
# method that found it (json_ld > microdata > tel_link/mailto_link >
# trafilatura_text), plus the raw (unnormalized) string as it appeared.
# ---------------------------------------------------------------------------


@dataclass
class _ExtractedValue:
    raw: str
    method: str


_METHOD_PRIORITY = {
    "json_ld": 0,
    "microdata": 1,
    "mailto_link": 2,
    "tel_link": 2,
    "trafilatura_text": 3,
}


def _normalize_phone(raw: str) -> str:
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits


def _record_value(
    store: dict[str, _ExtractedValue], key: str, raw: str, method: str
) -> None:
    existing = store.get(key)
    if existing is None or _METHOD_PRIORITY[method] < _METHOD_PRIORITY[existing.method]:
        store[key] = _ExtractedValue(raw=raw, method=method)


def _walk_jsonld(node, phones: dict, emails: dict, method: str) -> None:
    if isinstance(node, dict):
        for key, val in node.items():
            if key.lower() in ("telephone", "phone") and isinstance(val, str):
                norm = _normalize_phone(val)
                if len(norm) == 10:
                    _record_value(phones, norm, val, method)
            if key.lower() == "email" and isinstance(val, str) and "@" in val:
                _record_value(emails, val.lower(), val, method)
            _walk_jsonld(val, phones, emails, method)
    elif isinstance(node, list):
        for item in node:
            _walk_jsonld(item, phones, emails, method)


_ORG_TYPE_MARKERS = (
    "Organization",
    "LocalBusiness",
    "Corporation",
    "GeneralContractor",
)


def _org_name_from_structured_item(name: object, type_: object) -> str | None:
    """Shared by both the JSON-LD and microdata branches below -- an
    Organization/LocalBusiness/Corporation/GeneralContractor node's own
    `name` in either syntax is equally valid evidence for name
    corroboration (design doc S4/decision doc S6.2); there is no reason
    JSON-LD should get this and microdata should not."""
    if isinstance(type_, list):
        type_ = " ".join(str(t) for t in type_)
    if name and isinstance(type_, str) and any(t in type_ for t in _ORG_TYPE_MARKERS):
        return str(name)
    return None


def _extract_contacts(html: str, base_url: str) -> tuple[bool, str | None, dict, dict]:
    """Returns (structured_data_found, org_name, phones, emails) where
    phones/emails map normalized-value -> _ExtractedValue(raw, method).
    `structured_data_found` is True when EITHER JSON-LD or microdata
    yielded at least one item (not JSON-LD specifically -- despite the
    name this variable had in an earlier revision of this function, its
    only caller never actually branches on JSON-LD vs. microdata, only
    on "was there any structured markup at all"). `org_name` is read from
    whichever syntax's Organization/LocalBusiness node is encountered
    first, JSON-LD or microdata. Never raises -- a parsing failure on
    malformed HTML degrades to "found nothing", not an exception,
    matching provider.py's "no match is a valid outcome, never an error"
    contract."""
    structured_data_found = False
    org_name: str | None = None
    phones: dict[str, _ExtractedValue] = {}
    emails: dict[str, _ExtractedValue] = {}

    try:
        data = extruct.extract(
            html, base_url=base_url, syntaxes=["json-ld", "microdata"]
        )
        for item in data.get("json-ld", []):
            structured_data_found = True
            _walk_jsonld(item, phones, emails, "json_ld")
            if org_name is None:
                org_name = _org_name_from_structured_item(
                    item.get("name"), item.get("@type", "")
                )
        for item in data.get("microdata", []):
            if not item:
                continue
            structured_data_found = True
            properties = item.get("properties", {}) or {}
            _walk_jsonld(properties, phones, emails, "microdata")
            if org_name is None:
                org_name = _org_name_from_structured_item(
                    properties.get("name"), item.get("type", "")
                )
    except (
        Exception
    ):  # noqa: BLE001 -- malformed HTML/markup must never crash extraction
        pass

    try:
        text = (
            trafilatura.extract(
                html, url=base_url, include_comments=False, include_tables=False
            )
            or ""
        )
    except Exception:  # noqa: BLE001
        text = ""

    for m in EMAIL_RE.findall(text):
        _record_value(emails, m.lower(), m, "trafilatura_text")
    for m in PHONE_RE.findall(text):
        norm = _normalize_phone(m)
        if len(norm) == 10:
            _record_value(phones, norm, m, "trafilatura_text")

    try:
        for m in re.findall(r'mailto:([^"\'\s?]+)', html, flags=re.I):
            _record_value(emails, m.lower(), m, "mailto_link")
        for m in re.findall(r'tel:([^"\'\s]+)', html, flags=re.I):
            norm = _normalize_phone(m)
            if len(norm) == 10:
                _record_value(phones, norm, m, "tel_link")
    except Exception:  # noqa: BLE001
        pass

    return structured_data_found, org_name, phones, emails


def _is_canonical_page(url: str) -> bool:
    path = urlparse(url).path or "/"
    return bool(_CANONICAL_PATH_RE.match(path))


def _is_free_mail_domain(email: str) -> bool:
    _, _, host = email.partition("@")
    return _registrable_domain(host) in FREE_EMAIL_PROVIDERS if host else False


def _names_corroborate(org_name: str, expected_company_name_normalized: str) -> bool:
    if not org_name or not expected_company_name_normalized:
        return False
    normalized = normalize_vendor_name(org_name)
    if not normalized:
        return False
    return (
        normalized in expected_company_name_normalized
        or expected_company_name_normalized in normalized
    )


def _compute_confidence(
    *,
    extraction_method: str,
    domain_exact_match: bool,
    page_is_canonical: bool,
    org_name: str | None,
    company_name_normalized: str,
    is_free_mail: bool,
) -> float:
    """Decision doc S6.2 base ceilings, refined per Phase 3 design doc S4's
    extraction_method-specific ceilings and free-email-domain hard cap.
    Cumulative reductions, floored at 0.0 (a value that floors is never
    written -- see lookup() below, which drops any fact at confidence<=0)."""
    ceiling = {
        "json_ld": 0.85,
        "microdata": 0.75,
        "mailto_link": 0.55,
        "tel_link": 0.55,
        "trafilatura_text": 0.45,
    }.get(extraction_method, 0.45)

    confidence = ceiling
    if not domain_exact_match:
        confidence -= 0.15
    if not page_is_canonical:
        confidence -= 0.10
    if extraction_method == "json_ld":
        if not _names_corroborate(org_name or "", company_name_normalized):
            confidence -= 0.20

    if is_free_mail:
        confidence = min(confidence, 0.50)

    return max(0.0, round(confidence, 2))


# ---------------------------------------------------------------------------
# Crawl4AI fetch -- async by nature; bridged into this Protocol's
# synchronous lookup() via asyncio.run(), safe because
# _call_provider_with_timeout() (orchestrator.py) always calls lookup()
# from a plain ThreadPoolExecutor worker thread with no existing running
# event loop.
# ---------------------------------------------------------------------------


async def _fetch_rendered_page(url: str) -> tuple[str, int | None, str]:
    """Returns (html, http_status, rendered_final_url). Raises on failure --
    caller translates any exception into a clean no-match ProviderResult,
    never lets it propagate as an unhandled error (provider.py's own
    "no match is a valid outcome, never an error" contract)."""
    browser_cfg = BrowserConfig(headless=True, verbose=False, user_agent=USER_AGENT)
    run_cfg = CrawlerRunConfig(page_timeout=REQUEST_TIMEOUT_S * 1000, verbose=False)
    async with AsyncWebCrawler(config=browser_cfg) as crawler:
        result = await crawler.arun(url=url, config=run_cfg)
        if not result.success:
            raise RuntimeError(
                str(getattr(result, "error_message", "unknown fetch failure"))
            )
        html = result.html or ""
        if len(html.encode("utf-8", errors="ignore")) > MAX_RESPONSE_BYTES:
            html = html[:MAX_RESPONSE_BYTES]
        rendered_final_url = getattr(result, "url", url) or url
        return html, result.status_code, rendered_final_url


class WebsiteContactProvider:
    """Direct-site-only contact-fact provider (Phase 3A). See module
    docstring for full scope. Implements the EnrichmentProvider Protocol
    (pipeline/company_enrichment/provider.py)."""

    name = "website_contact"
    is_fact_source = True

    def _resolve_domain(
        self, request: EnrichmentRequest, company: Company | None
    ) -> str | None:
        """Phase 3A has no discovery -- the domain must already be known.
        Preference order: EnrichmentRequest.website (in case a future
        caller populates it), then Company.website, then
        Company.google_website. Each candidate is run through
        _normalize_website_candidate() -- a sentinel/malformed value
        (e.g. "N/A", "unknown", "-", "", whitespace, a bare IP literal, a
        single-label non-hostname token) is skipped, not accepted, and
        falls through to the next candidate exactly like an empty one
        already did. Returns a bare hostname or None if nothing usable is
        known -- None here is what makes lookup() return a clean no-match
        without ever calling _check_robots()/_redirect_walk(), so no
        malformed value ever triggers a network call."""
        candidates = [request.website]
        if company is not None:
            candidates.append(company.website)
            candidates.append(company.google_website)
        for candidate in candidates:
            domain = _normalize_website_candidate(candidate)
            if domain:
                return domain
        return None

    def lookup(self, session: Session, request: EnrichmentRequest) -> ProviderResult:
        company = session.get(Company, request.company_id)

        domain = self._resolve_domain(request, company)
        if not domain:
            # No known domain and no discovery in this phase -- a clean,
            # honest no-match, not an error. Never a guess.
            return ProviderResult(provider=self.name, matched=False)

        robots = _check_robots(domain)
        if not robots["allowed"]:
            detail = robots.get("error") or robots.get("robots_url") or "disallowed"
            return ProviderResult(
                provider=self.name, matched=False, error=f"robots_disallowed:{detail}"
            )

        walk = _redirect_walk(domain)
        if walk["outcome"] != "resolved":
            detail = walk.get("error")
            return ProviderResult(
                provider=self.name,
                matched=False,
                error=f"{walk['outcome']}" + (f":{detail}" if detail else ""),
            )

        final_url = walk["final_url"]

        # Defense-in-depth re-validation immediately before handing off to
        # the browser (closes the TOCTOU gap between the manual probe above
        # and the actual render below).
        try:
            _ssrf_and_dns_check(final_url)
        except (SsrfBlockedError, DnsResolutionError) as e:
            return ProviderResult(
                provider=self.name, matched=False, error=f"redirect_blocked:{e}"
            )

        try:
            html, http_status, rendered_final_url = asyncio.run(
                _fetch_rendered_page(final_url)
            )
        except (
            Exception
        ) as e:  # noqa: BLE001 -- any fetch failure is a clean no-match, never an uncaught error
            error_text = str(e)
            tag = "timeout" if "timeout" in error_text.lower() else "fetch_failed"
            return ProviderResult(
                provider=self.name, matched=False, error=f"{tag}:{error_text}"
            )

        # Safety net: the browser must never end up somewhere our manual
        # walk never validated (e.g. a JS-driven redirect).
        try:
            _ssrf_and_dns_check(rendered_final_url)
        except (SsrfBlockedError, DnsResolutionError) as e:
            return ProviderResult(
                provider=self.name, matched=False, error=f"redirect_blocked:{e}"
            )

        _structured_data_found, org_name, phones, emails = _extract_contacts(
            html, rendered_final_url
        )

        final_host = urlparse(rendered_final_url).hostname or ""
        domain_exact_match = _registrable_domain(final_host) == _registrable_domain(
            domain
        )
        page_is_canonical = _is_canonical_page(rendered_final_url)
        company_name_normalized = (
            company_normalized_name(company) if company is not None else ""
        )

        facts: list[ProviderFact] = []
        for norm_value, extracted in phones.items():
            confidence = _compute_confidence(
                extraction_method=extracted.method,
                domain_exact_match=domain_exact_match,
                page_is_canonical=page_is_canonical,
                org_name=org_name,
                company_name_normalized=company_name_normalized,
                is_free_mail=False,
            )
            if confidence <= 0.0:
                continue
            facts.append(
                ProviderFact(
                    field_name="phone",
                    value=norm_value,
                    confidence=confidence,
                    source_url=rendered_final_url,
                    raw_value=extracted.raw,
                    extraction_method=extracted.method,
                )
            )
        for norm_value, extracted in emails.items():
            confidence = _compute_confidence(
                extraction_method=extracted.method,
                domain_exact_match=domain_exact_match,
                page_is_canonical=page_is_canonical,
                org_name=org_name,
                company_name_normalized=company_name_normalized,
                is_free_mail=_is_free_mail_domain(norm_value),
            )
            if confidence <= 0.0:
                continue
            facts.append(
                ProviderFact(
                    field_name="email",
                    value=norm_value,
                    confidence=confidence,
                    source_url=rendered_final_url,
                    raw_value=extracted.raw,
                    extraction_method=extracted.method,
                )
            )

        return ProviderResult(
            provider=self.name, matched=bool(facts), facts=tuple(facts)
        )
