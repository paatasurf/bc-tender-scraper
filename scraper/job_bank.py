from __future__ import annotations

import re
from typing import Iterator
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from scraper.config import (
    JOB_BANK_BASE_URL,
    JOB_BANK_JOBS_CSV,
    JOB_BANK_LOCATION,
    JOB_BANK_SEARCH_PATH,
    JOB_BANK_SEARCH_TERM,
)
from scraper.utils import clean_text, create_session, polite_get, save_csv_rows

FIELDNAMES = ["job_title", "company", "location", "salary", "date", "url"]
BC_LOCATION_PATTERN = re.compile(r"\(BC\)|British Columbia", re.I)


def _is_bc_location(location: str) -> bool:
    return bool(BC_LOCATION_PATTERN.search(location))


def _clean_label(text: str, label: str) -> str:
    return clean_text(re.sub(rf"^{re.escape(label)}\s*", "", text, flags=re.I))


def _parse_article(article) -> dict[str, str] | None:
    link = article.select_one("a.resultJobItem")
    title_el = article.select_one("span.noctitle")
    if not link or not title_el:
        return None

    href = link.get("href", "").split(";")[0]
    if not href:
        return None

    location_el = article.select_one("li.location")
    location = _clean_label(location_el.get_text(" ", strip=True), "Location") if location_el else ""
    if not _is_bc_location(location):
        return None

    salary_el = article.select_one("li.salary")
    salary = _clean_label(salary_el.get_text(" ", strip=True), "Salary") if salary_el else ""

    date_el = article.select_one("li.date")
    company_el = article.select_one("li.business")

    return {
        "job_title": clean_text(title_el.get_text(" ", strip=True)),
        "company": clean_text(company_el.get_text(" ", strip=True) if company_el else ""),
        "location": location,
        "salary": salary,
        "date": clean_text(date_el.get_text(" ", strip=True) if date_el else ""),
        "url": urljoin(JOB_BANK_BASE_URL, href),
    }


def _iter_search_pages(session: requests.Session) -> Iterator[list]:
    page = 1
    while True:
        print(f"[Job Bank] Fetching page {page}...")
        response = polite_get(
            session,
            urljoin(JOB_BANK_BASE_URL, JOB_BANK_SEARCH_PATH),
            params={
                "searchstring": JOB_BANK_SEARCH_TERM,
                "locationstring": JOB_BANK_LOCATION,
                "page": page,
            },
        )
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")
        articles = soup.select("article.action-buttons")
        if not articles:
            break

        yield articles
        page += 1


def scrape_job_bank_jobs(session: requests.Session | None = None) -> list[dict[str, str]]:
    session = session or create_session()
    seen_urls: set[str] = set()
    jobs: list[dict[str, str]] = []

    print(
        f"[Job Bank] Searching '{JOB_BANK_SEARCH_TERM}' in {JOB_BANK_LOCATION} "
        f"at {JOB_BANK_BASE_URL}"
    )

    for articles in _iter_search_pages(session):
        page_matches = 0
        for article in articles:
            job = _parse_article(article)
            if not job or not job["url"] or job["url"] in seen_urls:
                continue
            seen_urls.add(job["url"])
            jobs.append(job)
            page_matches += 1

        print(f"[Job Bank] Page added {page_matches} BC jobs (total: {len(jobs)})")

    save_csv_rows(jobs, JOB_BANK_JOBS_CSV, FIELDNAMES)
    print(f"[Job Bank] Saved {len(jobs)} jobs to {JOB_BANK_JOBS_CSV}")
    return jobs
