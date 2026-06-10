from __future__ import annotations

from email.utils import parsedate_to_datetime

import requests
from bs4 import BeautifulSoup

from scraper.config import NEWS_KEYWORDS, NEWS_SIGNALS_CSV, NEWS_SOURCES
from scraper.utils import clean_text, create_session, polite_get, save_csv_rows

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
                BeautifulSoup(content_tag.get_text(), "html.parser").get_text(" ", strip=True)
            )
            break

    return {
        "title": title,
        "text": text,
        "publisher": publisher,
        "date": date,
        "url": url,
    }


def _fetch_rss(session: requests.Session, publisher: str, feed_url: str) -> list[dict[str, str]]:
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


def scrape_news_signals() -> list[dict[str, str]]:
    session = create_session()
    seen_urls: set[str] = set()
    signals: list[dict[str, str]] = []

    print("[News] Starting BC construction news scrape")

    for source in NEWS_SOURCES:
        publisher = source["publisher"]
        feed_url = source["url"]
        try:
            items = _fetch_rss(session, publisher, feed_url)
        except requests.RequestException as exc:
            print(f"[News] Failed for {publisher}: {exc}")
            continue

        count = 0
        for item in items:
            if item["url"] in seen_urls:
                continue
            seen_urls.add(item["url"])
            signals.append(item)
            count += 1
        print(f"[News] {publisher}: {count} signals ({len(signals)} total)")

    save_csv_rows(signals, NEWS_SIGNALS_CSV, FIELDNAMES)
    print(f"[News] Saved {len(signals)} signals to {NEWS_SIGNALS_CSV}")
    return signals
