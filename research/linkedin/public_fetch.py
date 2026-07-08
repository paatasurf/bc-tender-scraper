"""Fetch public LinkedIn company pages without authenticated session (best-effort)."""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import unquote

import requests
from bs4 import BeautifulSoup

from research.linkedin.scraper.adapter import LinkedInCompanyRecord

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)


def _extract_json_ld(soup: BeautifulSoup) -> dict[str, Any]:
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(data, dict) and data.get("@type") in ("Organization", "Corporation"):
            return data
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict) and item.get("@type") in ("Organization", "Corporation"):
                    return item
    return {}


def _meta(soup: BeautifulSoup, prop: str) -> str | None:
    tag = soup.find("meta", property=prop) or soup.find("meta", attrs={"name": prop})
    if tag and tag.get("content"):
        return str(tag["content"]).strip()
    return None


def _parse_followers(text: str | None) -> int | None:
    if not text:
        return None
    match = re.search(r"([\d,]+)\s+followers?\s+on\s+LinkedIn", text, re.I)
    if match:
        return int(match.group(1).replace(",", ""))
    return None


def _parse_company_size(text: str | None) -> str | None:
    if not text:
        return None
    match = re.search(
        r"([\d,]+\s*-\s*[\d,]+|\d+\+?)\s+employees",
        text,
        re.I,
    )
    if match:
        return match.group(0)
    return None


def _parse_founded(text: str | None) -> str | None:
    if not text:
        return None
    match = re.search(r"\b(?:Founded\s+)?((?:19|20)\d{2})\b", text)
    if match:
        return match.group(1)
    return None


def _parse_specialties(text: str | None) -> str | None:
    if not text:
        return None
    parts = text.split("|")
    if len(parts) >= 3:
        tail = parts[-1].strip()
        if tail and "follower" not in tail.lower():
            return tail[:500]
    return None


def _parse_company_name(title: str | None, og_title: str | None) -> str | None:
    for raw in (og_title, title):
        if not raw:
            continue
        name = re.sub(r"\s*\|\s*LinkedIn.*$", "", raw, flags=re.I).strip()
        name = re.sub(r"^LinkedIn:\s*", "", name, flags=re.I).strip()
        if name and name.lower() not in ("linkedin", "sign in", "join linkedin"):
            return name
    return None


def fetch_public_company_page(
    url: str,
    *,
    session: requests.Session | None = None,
    timeout: int = 25,
) -> LinkedInCompanyRecord:
    session = session or requests.Session()
    session.headers.setdefault("User-Agent", USER_AGENT)
    session.headers.setdefault("Accept-Language", "en-US,en;q=0.9")
    try:
        response = session.get(url, timeout=timeout, allow_redirects=True)
        if response.status_code == 999:
            return LinkedInCompanyRecord(
                linkedin_company_url=url,
                scrape_status="error",
                scrape_error="LinkedIn bot detection (HTTP 999)",
                scraped_at=datetime.now(timezone.utc).isoformat(),
            )
        if response.status_code >= 400:
            return LinkedInCompanyRecord(
                linkedin_company_url=url,
                scrape_status="error",
                scrape_error=f"HTTP {response.status_code}",
                scraped_at=datetime.now(timezone.utc).isoformat(),
            )
        soup = BeautifulSoup(response.text, "html.parser")
        json_ld = _extract_json_ld(soup)
        og_title = _meta(soup, "og:title")
        og_desc = _meta(soup, "og:description")
        title = soup.title.string if soup.title else None
        company_name = (
            json_ld.get("name")
            or _parse_company_name(title, og_title)
        )
        website = json_ld.get("url") or json_ld.get("sameAs")
        if isinstance(website, list):
            website = next((u for u in website if "linkedin.com" not in (u or "")), website[0] if website else None)
        description = json_ld.get("description") or og_desc
        followers = _parse_followers(description or og_desc)
        company_size = _parse_company_size(description or og_desc)
        founded = _parse_founded(description or og_desc)
        specialties = _parse_specialties(description or og_desc)
        industry = None
        if isinstance(json_ld.get("industry"), str):
            industry = json_ld["industry"]
        headquarters = None
        addr = json_ld.get("address")
        if isinstance(addr, dict):
            parts = [addr.get("addressLocality"), addr.get("addressRegion"), addr.get("addressCountry")]
            headquarters = ", ".join(p for p in parts if p)
        location = headquarters
        if not company_name and not description:
            return LinkedInCompanyRecord(
                linkedin_company_url=url,
                scrape_status="error",
                scrape_error="Login wall or empty public page",
                scraped_at=datetime.now(timezone.utc).isoformat(),
            )
        return LinkedInCompanyRecord(
            company_name=str(company_name) if company_name else None,
            linkedin_company_url=url,
            website=str(website) if website else None,
            industry=industry,
            headquarters=headquarters,
            company_size=company_size,
            specialties=specialties,
            founded=founded,
            description=description,
            location=location,
            scrape_status="ok",
            scraped_at=datetime.now(timezone.utc).isoformat(),
            source_fields={
                "fetch_mode": "public_unauthenticated",
                "page_title": title,
                "followers": followers,
            },
        )
    except requests.RequestException as exc:
        return LinkedInCompanyRecord(
            linkedin_company_url=url,
            scrape_status="error",
            scrape_error=str(exc)[:500],
            scraped_at=datetime.now(timezone.utc).isoformat(),
        )


def batch_fetch_public_pages(
    urls: list[str],
    *,
    delay_seconds: float = 1.5,
    progress_every: int = 25,
) -> list[LinkedInCompanyRecord]:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    records: list[LinkedInCompanyRecord] = []
    total = len(urls)
    for index, url in enumerate(urls, start=1):
        records.append(fetch_public_company_page(url, session=session))
        if index % progress_every == 0 or index == total:
            ok = sum(1 for r in records if r.scrape_status == "ok")
            print(f"[public-fetch] {index}/{total} ok={ok} err={index - ok}", flush=True)
        if index < total and delay_seconds > 0:
            time.sleep(delay_seconds)
    return records
