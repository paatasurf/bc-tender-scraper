from __future__ import annotations

import time

from curl_cffi import requests

from scraper.config import REQUEST_DELAY_SECONDS, USER_AGENT

BCBID_IMPERSONATE = "chrome120"
BCBID_HOST = "bcbid.gov.bc.ca"


def create_bcbid_session() -> requests.Session:
    session = requests.Session(impersonate=BCBID_IMPERSONATE)
    session.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-CA,en;q=0.9",
        }
    )
    return session


def _is_bcbid_url(url: str) -> bool:
    return BCBID_HOST in url


def _request_kwargs(url: str, kwargs: dict) -> dict:
    out = dict(kwargs)
    out.setdefault("timeout", 60)
    if _is_bcbid_url(url):
        out.setdefault("impersonate", BCBID_IMPERSONATE)
    return out


def bcbid_get(session: requests.Session, url: str, **kwargs) -> requests.Response:
    time.sleep(REQUEST_DELAY_SECONDS)
    return session.get(url, **_request_kwargs(url, kwargs))


def bcbid_post(session: requests.Session, url: str, **kwargs) -> requests.Response:
    time.sleep(REQUEST_DELAY_SECONDS)
    return session.post(url, **_request_kwargs(url, kwargs))
