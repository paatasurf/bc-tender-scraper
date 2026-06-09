from __future__ import annotations

import csv
import json
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Iterable
from urllib.parse import urlencode

import requests
from bs4 import BeautifulSoup

from scraper.config import (
    CATEGORY_KEYWORDS,
    OPEN_DATA_DELAY_SECONDS,
    REQUEST_DELAY_SECONDS,
    USER_AGENT,
)
from scraper.models import Tender


def create_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-CA,en;q=0.9",
        }
    )
    return session


def polite_get(session: requests.Session, url: str, **kwargs) -> requests.Response:
    time.sleep(REQUEST_DELAY_SECONDS)
    return session.get(url, timeout=60, **kwargs)


def fetch_html(session: requests.Session, url: str, *, params: dict[str, str] | None = None) -> str:
    response = polite_get(session, url, params=params)
    if response.status_code == 200 and "just a moment" not in response.text[:2000].lower():
        return response.text

    if shutil.which("curl"):
        target = f"{url}?{urlencode(params)}" if params else url
        result = subprocess.run(
            ["curl", "-sL", "-A", USER_AGENT, target],
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
        if result.returncode == 0 and result.stdout and "just a moment" not in result.stdout[:2000].lower():
            return result.stdout

    response.raise_for_status()
    return response.text


def polite_post(session: requests.Session, url: str, **kwargs) -> requests.Response:
    time.sleep(REQUEST_DELAY_SECONDS)
    return session.post(url, timeout=60, **kwargs)


def polite_api_get(session: requests.Session, url: str, **kwargs) -> requests.Response:
    time.sleep(OPEN_DATA_DELAY_SECONDS)
    return session.get(url, timeout=60, **kwargs)


def save_csv_rows(rows: Iterable[dict[str, object]], csv_path: str, fieldnames: list[str]) -> None:
    with open(csv_path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", value).strip()


def matches_target_category(*parts: str) -> bool:
    blob = " ".join(clean_text(part) for part in parts if part).lower()
    return any(keyword in blob for keyword in CATEGORY_KEYWORDS)


def is_browser_check(soup: BeautifulSoup) -> bool:
    title = soup.title.get_text(strip=True).lower() if soup.title else ""
    return "browser check" in title


def load_netscape_cookies(session: requests.Session, cookie_path: Path) -> None:
    if not cookie_path.exists():
        return

    for line in cookie_path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) != 7:
            continue
        domain, _flag, path, secure, _expires, name, value = parts
        session.cookies.set(
            name,
            value,
            domain=domain.lstrip("."),
            path=path,
        )


def extract_label_value_map(soup: BeautifulSoup) -> dict[str, str]:
    values: dict[str, str] = {}

    for dl in soup.find_all("dl"):
        for dt, dd in zip(dl.find_all("dt"), dl.find_all("dd")):
            key = clean_text(dt.get_text(" ", strip=True))
            val = clean_text(dd.get_text(" ", strip=True))
            if key and val:
                values[key] = val

    for field in soup.select(".field"):
        label = field.find(class_=lambda c: c and "label" in c)
        value = field.find(class_=lambda c: c and ("readonly" in c or "item" in c))
        if label and value:
            key = clean_text(label.get_text(" ", strip=True).split("\n")[0])
            val = clean_text(value.get_text(" ", strip=True))
            if key and val and key not in values:
                values[key] = val

    return values


def save_tenders(tenders: Iterable[Tender], csv_path: str, json_path: str) -> None:
    rows = [tender.to_dict() for tender in tenders]
    fieldnames = [
        "title",
        "organization",
        "category",
        "posted_date",
        "closing_date",
        "estimated_value",
        "location",
        "tender_id",
        "url",
        "source",
    ]

    with open(csv_path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    with open(json_path, "w", encoding="utf-8") as handle:
        json.dump(rows, handle, indent=2, ensure_ascii=False)
