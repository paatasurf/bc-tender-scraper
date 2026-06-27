"""Shape Your City development application index and field parsing."""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import quote

from bs4 import BeautifulSoup

from scraper.config import (
    SHAPEYOURCITY_PROJECTFINDER_URL,
    VANCOUVER_DEVELOPMENT_APPLICATIONS_URL,
)
from scraper.utils import clean_text, create_session, fetch_html

REFERENCE_NUMBER_RE = re.compile(r"\((DP-\d{4}-\d+|DE\d+)\)", re.I)
RN_QUERY_RE = re.compile(r"[?&]RN=([^&]+)", re.I)
APPLICANT_PATTERNS = (
    re.compile(r"([^.]{3,200}?) has applied to the City of Vancouver", re.I),
    re.compile(r"submitted by ([^.]{3,200}?)(?: to the City| for)", re.I),
    re.compile(r"on behalf of ([^.]{3,120}?)(?: has|,|\.)", re.I),
)
PROJECT_VALUE_RE = re.compile(r"\$\s?[\d,]+(?:\.\d+)?")
PROPERTY_TYPE_TOKENS: dict[str, tuple[str, ...]] = {
    "one-family dwelling": ("one-family", "single family", "single detached", "one family"),
    "two-family dwelling": ("two-family", "duplex", "two family"),
    "multiple dwelling": ("multiple dwelling", "multiplex", "townhouse", "townhome"),
    "mixed-use": ("mixed-use", "mixed use"),
    "commercial development": ("commercial",),
    "residential/commercial": ("residential/commercial", "mixed-use", "mixed use"),
    "addition": ("addition",),
    "rezoning": ("rezoning", "cd-1"),
    "social service": ("social service", "child care", "day care"),
}


def build_development_application_url(reference_number: str) -> str:
    rn = clean_text(reference_number)
    if not rn:
        return ""
    return f"{VANCOUVER_DEVELOPMENT_APPLICATIONS_URL}?RN={quote(rn)}"


def build_shapeyourcity_url(permalink: str) -> str:
    slug = clean_text(permalink).strip("/")
    if not slug:
        return ""
    return f"https://www.shapeyourcity.ca/{slug}"


def extract_reference_number(text: str | None) -> str:
    match = REFERENCE_NUMBER_RE.search(text or "")
    return match.group(1).upper() if match else ""


def extract_reference_number_from_url(url: str | None) -> str:
    match = RN_QUERY_RE.search(url or "")
    return clean_text(match.group(1)) if match else ""


def extract_address_from_project_name(name: str | None) -> str:
    text = clean_text(name)
    if not text:
        return ""
    match = REFERENCE_NUMBER_RE.search(text)
    if match:
        text = text[: match.start()].strip(" -")
    text = re.sub(r"\s*development application\s*$", "", text, flags=re.I).strip()
    return text


def _plain_text(html: str | None) -> str:
    if not html:
        return ""
    return clean_text(BeautifulSoup(html, "html.parser").get_text(" ", strip=True))


def extract_applicant_from_text(text: str | None) -> str:
    plain = _plain_text(text)
    if not plain:
        return ""
    for pattern in APPLICANT_PATTERNS:
        match = pattern.search(plain)
        if match:
            candidate = clean_text(match.group(1))
            if candidate.lower().startswith("for more information"):
                continue
            if len(candidate) > 200:
                candidate = candidate[:200].rsplit(" ", 1)[0]
            return candidate
    return ""


def extract_project_value(text: str | None) -> str:
    plain = _plain_text(text)
    match = PROJECT_VALUE_RE.search(plain)
    if not match:
        return ""
    return match.group(0).replace(" ", "")


def property_type_tokens(property_type: str | None) -> list[str]:
    lower = clean_text(property_type).lower()
    tokens: list[str] = []
    for key, values in PROPERTY_TYPE_TOKENS.items():
        if key in lower:
            tokens.extend(values)
    if not tokens and lower:
        tokens.append(lower.split()[0])
    return tokens


def score_project_match(
    *,
    region: str,
    property_type: str,
    project: dict[str, Any],
) -> int:
    score = 0
    region_lower = clean_text(region).lower()
    tags = [clean_text(tag).lower() for tag in project.get("projectTagList") or []]
    if region_lower and region_lower in tags:
        score += 10

    name = clean_text(project.get("name")).lower()
    description = _plain_text(project.get("description")).lower()
    for token in property_type_tokens(property_type):
        if token in name or token in description:
            score += 5
    return score


def find_project_by_reference(projects: list[dict[str, Any]], reference_number: str) -> dict[str, Any] | None:
    rn = clean_text(reference_number).lower()
    if not rn:
        return None
    for project in projects:
        haystack = clean_text(project.get("name")).lower()
        if rn in haystack:
            return project
    return None


def find_best_project_match(
    projects: list[dict[str, Any]],
    *,
    region: str,
    property_type: str,
    min_score: int = 12,
) -> dict[str, Any] | None:
    best: dict[str, Any] | None = None
    best_score = min_score - 1
    for project in projects:
        score = score_project_match(region=region, property_type=property_type, project=project)
        if score > best_score:
            best = project
            best_score = score
    return best


def extract_projects_from_html(html: str) -> list[dict[str, Any]]:
    marker = '"projects":['
    start = html.find(marker)
    if start < 0:
        return []
    start += len('"projects":')
    projects, _end = json.JSONDecoder().raw_decode(html, start)
    return projects if isinstance(projects, list) else []


def load_development_projects(*, session=None) -> list[dict[str, Any]]:
    http = session or create_session()
    html = fetch_html(http, SHAPEYOURCITY_PROJECTFINDER_URL)
    return extract_projects_from_html(html)


def parse_vancouver_detail_page(html: str) -> dict[str, str]:
    soup = BeautifulSoup(html, "html.parser")
    fields: dict[str, str] = {}
    for row in soup.select("tr"):
        cells = [clean_text(cell.get_text(" ", strip=True)) for cell in row.select("th,td")]
        if len(cells) >= 2 and cells[0]:
            label = cells[0].lower()
            value = cells[1]
            if "address" in label:
                fields["address"] = value
            elif "applicant" in label or "developer" in label or "owner" in label:
                fields["applicant"] = value
            elif "value" in label or "cost" in label:
                fields["project_value"] = value
    for dt in soup.select("dt"):
        label = clean_text(dt.get_text(" ", strip=True)).lower()
        dd = dt.find_next_sibling("dd")
        if not dd:
            continue
        value = clean_text(dd.get_text(" ", strip=True))
        if "address" in label:
            fields["address"] = value
        elif "applicant" in label or "developer" in label or "owner" in label:
            fields["applicant"] = value
        elif "value" in label or "cost" in label:
            fields["project_value"] = value
    return fields


def parse_shapeyourcity_detail_page(html: str) -> dict[str, str]:
    soup = BeautifulSoup(html, "html.parser")
    title = clean_text(soup.find("title").get_text(" ", strip=True)) if soup.find("title") else ""
    title = re.sub(r"\s*\|\s*Shape Your City.*$", "", title, flags=re.I).strip()
    fields = {
        "address": extract_address_from_project_name(title),
        "applicant": "",
        "project_value": "",
    }
    main = soup.find("main") or soup
    description_html = ""
    for paragraph in main.select("p"):
        description_html += str(paragraph)
    fields["applicant"] = extract_applicant_from_text(description_html)
    fields["project_value"] = extract_project_value(description_html)
    return fields


def project_to_enrichment(project: dict[str, Any], *, detail: dict[str, str] | None = None) -> dict[str, str]:
    name = clean_text(project.get("name"))
    description = project.get("description") or ""
    reference = extract_reference_number(name)
    detail = detail or {}
    address = detail.get("address") or extract_address_from_project_name(name)
    applicant = detail.get("applicant") or extract_applicant_from_text(description)
    project_value = detail.get("project_value") or extract_project_value(description)
    url_link = build_development_application_url(reference) if reference else ""
    if not url_link:
        url_link = build_shapeyourcity_url(clean_text(project.get("permalink")))
    return {
        "url_link": url_link,
        "address": address,
        "applicant": applicant,
        "project_value": project_value,
    }
