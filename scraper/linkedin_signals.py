from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from urllib.parse import quote

from scraper.config import LINKEDIN_HASHTAGS, LINKEDIN_SIGNALS_CSV
from scraper.utils import clean_text, save_csv_rows

FIELDNAMES = ["title", "content", "author", "date", "url", "likes_count"]

POST_URL_RE = re.compile(r"https://www\.linkedin\.com/feed/update/urn:li:(?:activity|ugcPost):\d+")
LIKE_COUNT_RE = re.compile(r"(\d[\d,]*)\s+(?:reactions?|likes?)", re.I)
AUTHOR_RE = re.compile(r'"actorName"\s*:\s*"([^"]+)"')
CONTENT_RE = re.compile(r'"commentary"\s*:\s*\{[^}]*"text"\s*:\s*"([^"]+)"')


def _hashtag_search_url(hashtag: str) -> str:
    tag = hashtag.lstrip("#")
    return (
        "https://www.linkedin.com/search/results/content/"
        f"?keywords=%23{quote(tag)}&origin=GLOBAL_SEARCH_HEADER"
    )


def _parse_likes(text: str) -> int:
    match = LIKE_COUNT_RE.search(text)
    if not match:
        return 0
    try:
        return int(match.group(1).replace(",", ""))
    except ValueError:
        return 0


def _extract_from_html(html: str, hashtag: str) -> list[dict[str, str]]:
    """Best-effort extraction from LinkedIn search HTML (public pages may be limited)."""
    posts: list[dict[str, str]] = []
    seen_urls: set[str] = set()

    for url in POST_URL_RE.findall(html):
        if url in seen_urls:
            continue
        seen_urls.add(url)

        window_start = max(0, html.find(url) - 2000)
        window_end = min(len(html), html.find(url) + 4000)
        window = html[window_start:window_end]

        author = ""
        author_match = AUTHOR_RE.search(window)
        if author_match:
            author = clean_text(author_match.group(1))

        content = ""
        content_match = CONTENT_RE.search(window)
        if content_match:
            content = clean_text(content_match.group(1).replace("\\n", " ").replace("\\u0026", "&"))

        likes = _parse_likes(window)
        title = content[:120] + ("..." if len(content) > 120 else "") if content else f"#{hashtag} post"
        if not title:
            title = f"LinkedIn #{hashtag}"

        posts.append(
            {
                "title": title,
                "content": content,
                "author": author,
                "date": datetime.now(tz=timezone.utc).date().isoformat(),
                "url": url,
                "likes_count": str(likes),
            }
        )

    if posts:
        return posts

    # Fallback: parse visible anchor text blocks near update links.
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    for anchor in soup.find_all("a", href=POST_URL_RE):
        url = anchor["href"]
        if url in seen_urls:
            continue
        seen_urls.add(url)
        text = clean_text(anchor.get_text(" ", strip=True))
        if len(text) < 20:
            continue
        posts.append(
            {
                "title": text[:120] + ("..." if len(text) > 120 else ""),
                "content": text,
                "author": "",
                "date": datetime.now(tz=timezone.utc).date().isoformat(),
                "url": url,
                "likes_count": "0",
            }
        )
    return posts


async def _scrape_hashtags(hashtags: tuple[str, ...]) -> list[dict[str, str]]:
    from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig

    browser_config = BrowserConfig(headless=True, verbose=False)
    run_config = CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        page_timeout=60_000,
        verbose=False,
        wait_until="networkidle",
    )

    seen_urls: set[str] = set()
    signals: list[dict[str, str]] = []

    async with AsyncWebCrawler(config=browser_config) as crawler:
        for hashtag in hashtags:
            url = _hashtag_search_url(hashtag)
            print(f"[LinkedIn] Crawling #{hashtag}...")
            try:
                result = await crawler.arun(url, config=run_config)
                if not result.success:
                    print(f"[LinkedIn] Crawl failed for #{hashtag}: {result.error_message}")
                    await asyncio.sleep(3)
                    continue

                html = result.markdown or result.html or ""
                if "sign in" in html.lower() and "join linkedin" in html.lower():
                    print(f"[LinkedIn] Login wall for #{hashtag} — skipping")
                    await asyncio.sleep(3)
                    continue

                items = _extract_from_html(html, hashtag)
                count = 0
                for item in items:
                    if item["url"] in seen_urls:
                        continue
                    seen_urls.add(item["url"])
                    signals.append(item)
                    count += 1
                print(f"[LinkedIn] #{hashtag}: {count} posts ({len(signals)} total)")
            except Exception as exc:
                print(f"[LinkedIn] Error for #{hashtag}: {exc}")
            await asyncio.sleep(3)

    return signals


def scrape_linkedin_signals() -> list[dict[str, str]]:
    try:
        import crawl4ai  # noqa: F401
    except ImportError:
        print("[LinkedIn] Skipping: crawl4ai is not installed.")
        save_csv_rows([], LINKEDIN_SIGNALS_CSV, FIELDNAMES)
        return []

    print("[LinkedIn] Starting BC construction hashtag scrape")
    try:
        signals = asyncio.run(_scrape_hashtags(LINKEDIN_HASHTAGS))
    except Exception as exc:
        print(f"[LinkedIn] Scrape aborted: {exc}")
        signals = []

    save_csv_rows(signals, LINKEDIN_SIGNALS_CSV, FIELDNAMES)
    print(f"[LinkedIn] Saved {len(signals)} signals to {LINKEDIN_SIGNALS_CSV}")
    return signals
