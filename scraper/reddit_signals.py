from __future__ import annotations

from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

from scraper.config import REDDIT_KEYWORDS, REDDIT_SIGNALS_CSV, REDDIT_SUBREDDITS
from scraper.utils import clean_text, create_session, polite_get, save_csv_rows

PULLPUSH_URL = "https://api.pullpush.io/reddit/search/submission/"
FIELDNAMES = ["title", "text", "upvotes", "date", "url"]


def _matches_keywords(title: str, text: str) -> bool:
    blob = f"{title} {text}".lower()
    return any(keyword in blob for keyword in REDDIT_KEYWORDS)


def _format_post(item: dict) -> dict[str, str]:
    created = item.get("created_utc")
    if created:
        date = datetime.fromtimestamp(created, tz=timezone.utc).date().isoformat()
    else:
        date = ""

    permalink = item.get("permalink") or ""
    url = item.get("url") or ""
    if permalink and not permalink.startswith("http"):
        url = f"https://www.reddit.com{permalink}"
    elif not url:
        url = permalink

    return {
        "title": clean_text(item.get("title")),
        "text": clean_text(item.get("selftext")),
        "upvotes": str(item.get("score", 0)),
        "date": date,
        "url": url,
    }


def _fetch_pullpush(session: requests.Session, subreddit: str, keyword: str) -> list[dict]:
    print(f"[Reddit] Searching r/{subreddit} for '{keyword}'...")
    response = polite_get(
        session,
        PULLPUSH_URL,
        params={"subreddit": subreddit, "q": keyword, "size": 100},
    )
    response.raise_for_status()
    return response.json().get("data", [])


def _fetch_rss_search(session: requests.Session, subreddit: str) -> list[dict]:
    query = " OR ".join(REDDIT_KEYWORDS)
    print(f"[Reddit] Fetching RSS search for r/{subreddit}...")
    response = polite_get(
        session,
        f"https://www.reddit.com/r/{subreddit}/search.rss",
        params={"q": query, "restrict_sr": "on", "sort": "new", "t": "year"},
    )
    response.raise_for_status()

    soup = BeautifulSoup(response.content, "xml")
    posts: list[dict] = []
    for entry in soup.find_all("entry"):
        title = clean_text(entry.find("title").get_text() if entry.find("title") else "")
        link_tag = entry.find("link")
        url = link_tag.get("href", "") if link_tag else ""
        updated = entry.find("updated")
        date = ""
        if updated:
            date = updated.get_text(strip=True)[:10]
        content_tag = entry.find("content")
        text = ""
        if content_tag and content_tag.get_text():
            text = clean_text(BeautifulSoup(content_tag.get_text(), "html.parser").get_text(" ", strip=True))

        posts.append(
            {
                "title": title,
                "selftext": text,
                "score": 0,
                "created_utc": None,
                "permalink": url.replace("https://www.reddit.com", ""),
                "url": url,
            }
        )
    return posts


def scrape_reddit_signals() -> list[dict[str, str]]:
    session = create_session()
    seen_urls: set[str] = set()
    signals: list[dict[str, str]] = []

    print("[Reddit] Starting BC construction signal scrape")

    for subreddit in REDDIT_SUBREDDITS:
        collected: list[dict] = []
        for keyword in REDDIT_KEYWORDS:
            try:
                collected.extend(_fetch_pullpush(session, subreddit, keyword))
            except requests.RequestException as exc:
                print(f"[Reddit] Pullpush failed for r/{subreddit} ({keyword}): {exc}")

        try:
            collected.extend(_fetch_rss_search(session, subreddit))
        except requests.RequestException as exc:
            print(f"[Reddit] RSS failed for r/{subreddit}: {exc}")

        for item in collected:
            post = _format_post(item)
            if not post["url"] or post["url"] in seen_urls:
                continue
            if not _matches_keywords(post["title"], post["text"]):
                continue
            seen_urls.add(post["url"])
            signals.append(post)

        print(f"[Reddit] r/{subreddit}: {len(signals)} total signals so far")

    save_csv_rows(signals, REDDIT_SIGNALS_CSV, FIELDNAMES)
    print(f"[Reddit] Saved {len(signals)} signals to {REDDIT_SIGNALS_CSV}")
    return signals
