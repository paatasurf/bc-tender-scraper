from __future__ import annotations

import urllib.error
import urllib.parse
import urllib.request

from bs4 import BeautifulSoup

from config.env import get_env
from scraper.utils import is_browser_check

_AUTH_MARKERS = (
    "browser check",
    "access denied",
    "sign in",
    "log in",
    "login",
    "bceid",
    "session expired",
    "session has expired",
    "not authorized",
    "unauthorized",
)


def is_bcbid_auth_failure(soup: BeautifulSoup | None = None, *, html: str = "") -> bool:
    """True when BC Bid returned a login, browser-check, or access-denied page."""
    if soup is not None and is_browser_check(soup):
        return True

    title = ""
    body = html
    if soup is not None:
        title = soup.title.get_text(strip=True).lower() if soup.title else ""
        body = soup.get_text(" ", strip=True).lower()

    blob = f"{title} {body.lower()}".strip()
    return any(marker in blob for marker in _AUTH_MARKERS)


def bcbid_auth_failure_reason(soup: BeautifulSoup | None = None, *, html: str = "") -> str:
    if soup is not None and is_browser_check(soup):
        return "browser check page"
    title = soup.title.get_text(strip=True) if soup and soup.title else ""
    if title and any(marker in title.lower() for marker in _AUTH_MARKERS):
        return f"page title: {title}"
    for marker in _AUTH_MARKERS:
        if marker in html.lower():
            return f"page contains '{marker}'"
    return "BC Bid listing grid missing (likely expired session or blocked request)"


def log_bcbid_session_expired(reason: str) -> None:
    print(
        "[BC Bid] SESSION EXPIRED — cookies are invalid or expired. "
        f"Reason: {reason}. Re-export Netscape cookies from bcbid.gov.bc.ca and update "
        "BCBID_COOKIES_CONTENT on Railway."
    )


def notify_bcbid_session_expired(reason: str) -> None:
    token = get_env("TELEGRAM_BOT_TOKEN")
    chat_id = get_env("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("[BC Bid] Telegram alert skipped (TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set)")
        return

    message = (
        "BC Bid scraper: session expired or access denied.\n"
        f"Reason: {reason}\n"
        "Action: re-export Netscape cookies from a logged-in bcbid.gov.bc.ca session "
        "and update BCBID_COOKIES_CONTENT on Railway."
    )
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = urllib.parse.urlencode({"chat_id": chat_id, "text": message}).encode()
    request = urllib.request.Request(url, data=payload, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            if response.status != 200:
                print(f"[BC Bid] Telegram alert failed: HTTP {response.status}")
    except urllib.error.URLError as exc:
        print(f"[BC Bid] Telegram alert failed: {exc}")


def handle_bcbid_auth_failure(soup: BeautifulSoup | None = None, *, html: str = "") -> None:
    reason = bcbid_auth_failure_reason(soup, html=html)
    log_bcbid_session_expired(reason)
    notify_bcbid_session_expired(reason)


class BcbidSessionExpiredError(RuntimeError):
    """Raised when BC Bid cookies no longer grant access to the opportunities listing."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason
