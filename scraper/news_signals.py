from __future__ import annotations

import logging
from email.utils import parsedate_to_datetime
from typing import Callable

import requests
from bs4 import BeautifulSoup

from scraper.config import NEWS_KEYWORDS, NEWS_SIGNALS_CSV, NEWS_SOURCES
from scraper.utils import clean_text, create_session, polite_get, save_csv_rows

logger = logging.getLogger(__name__)

FIELDNAMES = ["title", "text", "publisher", "date", "url"]


def _matches_keywords(title: str, text: str) -> bool:
    blob = f"{title} {text}".lower()
    return any(keyword in blob for keyword in NEWS_KEYWORDS)


def _parse_rss_entry(entry, publisher: str) -> dict[str, str] | None:
    title_tag = entry.find("title")
    title = clean_text(title_tag.get_text() if title_tag else "")
    if not title:
        return None

    link_tag = entry.find("link")
    url = ""
    if link_tag:
        url = link_tag.get("href", "") or link_tag.get_text(strip=True)
    if not url:
        guid = entry.find("guid")
        if guid:
            url = guid.get_text(strip=True)
    if not url:
        return None

    date = ""
    for tag_name in ("pubDate", "published", "updated", "dc:date"):
        date_tag = entry.find(tag_name)
        if date_tag and date_tag.get_text(strip=True):
            raw = date_tag.get_text(strip=True)
            try:
                date = parsedate_to_datetime(raw).date().isoformat()
            except (TypeError, ValueError, IndexError):
                date = raw[:10]
            break

    text = ""
    for tag_name in ("description", "content:encoded", "summary"):
        content_tag = entry.find(tag_name)
        if content_tag and content_tag.get_text():
            text = clean_text(
                BeautifulSoup(content_tag.get_text(), "html.parser").get_text(
                    " ", strip=True
                )
            )
            break

    return {
        "title": title,
        "text": text,
        "publisher": publisher,
        "date": date,
        "url": url,
    }


def _fetch_rss(
    session: requests.Session, publisher: str, feed_url: str
) -> list[dict[str, str]]:
    print(f"[News] Fetching {publisher} ({feed_url})...")
    response = polite_get(session, feed_url)
    response.raise_for_status()
    soup = BeautifulSoup(response.content, "xml")

    items: list[dict[str, str]] = []
    entries = soup.find_all("item")
    if not entries:
        entries = soup.find_all("entry")

    for entry in entries:
        parsed = _parse_rss_entry(entry, publisher)
        if parsed and _matches_keywords(parsed["title"], parsed["text"]):
            items.append(parsed)
    return items


def _phase_slug(publisher: str) -> str:
    return publisher.lower().replace(" ", "_")


def _safe_call_on_phase(on_phase: Callable[[str], None], phase: str) -> None:
    """(M3F-3) Invoke an optional progress callback without ever letting it
    affect the caller -- mirrors pipeline.company_intelligence._safe_call_on_phase()
    and pipeline.arch_company_intelligence._safe_call_on_phase() exactly.
    Never logs the callback's exception text -- only a fixed, phase-named
    warning."""
    try:
        on_phase(phase)
    except Exception:
        logger.warning("[News] on_phase callback failed for phase=%s", phase)


def scrape_news_signals(
    *, on_phase: Callable[[str], None] | None = None
) -> list[dict[str, str]]:
    """``on_phase``, if given, is called once per source in NEWS_SOURCES:
    with a slug of the publisher's name on success, or
    ``f"{slug}_failed"`` from inside the existing per-source
    except-block (a single feed's fetch failure, unchanged pre-existing
    behavior -- print + continue to the next source). Both go through
    _safe_call_on_phase(), so a raising callback can never change this
    function's own steps, order, dedup, saved CSV, or returned signals
    list. Defaults to None, a complete no-op -- existing callers are
    unaffected.
    """
    session = create_session()
    seen_urls: set[str] = set()
    signals: list[dict[str, str]] = []

    print("[News] Starting BC construction news scrape")

    for source in NEWS_SOURCES:
        publisher = source["publisher"]
        feed_url = source["url"]
        phase = _phase_slug(publisher)
        try:
            items = _fetch_rss(session, publisher, feed_url)
        except requests.RequestException as exc:
            print(f"[News] Failed for {publisher}: {exc}")
            if on_phase is not None:
                _safe_call_on_phase(on_phase, f"{phase}_failed")
            continue

        count = 0
        for item in items:
            if item["url"] in seen_urls:
                continue
            seen_urls.add(item["url"])
            signals.append(item)
            count += 1
        print(f"[News] {publisher}: {count} signals ({len(signals)} total)")
        if on_phase is not None:
            _safe_call_on_phase(on_phase, phase)

    save_csv_rows(signals, NEWS_SIGNALS_CSV, FIELDNAMES)
    print(f"[News] Saved {len(signals)} signals to {NEWS_SIGNALS_CSV}")
    return signals
