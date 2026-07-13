"""Enrich early_signal_events with Vancouver development application details."""

from __future__ import annotations

from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from db.models import EarlySignalEvent
from scraper.shapeyourcity_development import (
    build_shapeyourcity_url,
    extract_reference_number_from_url,
    find_best_project_match,
    find_project_by_reference,
    load_development_projects,
    parse_shapeyourcity_detail_page,
    parse_vancouver_detail_page,
    project_to_enrichment,
)
from scraper.utils import create_session, fetch_html

NOT_FOUND_TITLE = "can't be found"


def _needs_enrichment(event: EarlySignalEvent) -> bool:
    return not (event.address or event.applicant or event.project_value)


def _resolve_project(
    projects: list[dict[str, Any]],
    event: EarlySignalEvent,
) -> dict[str, Any] | None:
    reference = extract_reference_number_from_url(event.url_link)
    if reference:
        matched = find_project_by_reference(projects, reference)
        if matched:
            return matched

    return find_best_project_match(
        projects,
        region=event.region,
        property_type=event.property_type,
    )


def _fetch_detail_fields(
    session, project: dict[str, Any], url_link: str
) -> dict[str, str]:
    detail: dict[str, str] = {}
    if url_link and "development-applications.aspx" in url_link.lower():
        try:
            html = fetch_html(session, url_link)
            title = html.lower()
            if NOT_FOUND_TITLE not in title:
                detail = parse_vancouver_detail_page(html)
        except Exception as exc:
            print(
                f"[Vancouver Enrichment] Detail page fetch failed for {url_link}: {exc}"
            )
            detail = {}

    if detail.get("address") and detail.get("applicant"):
        return detail

    permalink = project.get("permalink")
    if not permalink:
        return detail

    try:
        page_url = build_shapeyourcity_url(permalink)
        html = fetch_html(session, page_url)
        page_detail = parse_shapeyourcity_detail_page(html)
    except Exception as exc:
        print(
            f"[Vancouver Enrichment] ShapeYourCity fetch failed for permalink={permalink}: {exc}"
        )
        return detail

    merged = dict(detail)
    for key, value in page_detail.items():
        if value and not merged.get(key):
            merged[key] = value
    return merged


def enrich_early_signal_event(
    event: EarlySignalEvent,
    projects: list[dict[str, Any]],
    *,
    session,
    fetch_details: bool = True,
) -> dict[str, str] | None:
    project = _resolve_project(projects, event)
    if project is None:
        return None

    detail: dict[str, str] = {}
    if fetch_details:
        detail = _fetch_detail_fields(session, project, event.url_link)

    return project_to_enrichment(project, detail=detail)


def enrich_early_signal_events(
    db_session: Session,
    *,
    limit: int | None = None,
    force: bool = False,
    fetch_details: bool = True,
    persist: bool = True,
) -> dict[str, Any]:
    http = create_session()
    projects = load_development_projects(session=http)

    query = select(EarlySignalEvent).order_by(EarlySignalEvent.id.asc())
    if not force:
        query = query.where(
            or_(
                EarlySignalEvent.address == "",
                EarlySignalEvent.applicant == "",
                EarlySignalEvent.project_value == "",
            )
        )
    if limit is not None:
        query = query.limit(limit)

    events = db_session.scalars(query).all()
    enriched = 0
    skipped = 0
    results: list[dict[str, Any]] = []

    for event in events:
        if not force and not _needs_enrichment(event):
            skipped += 1
            continue

        payload = enrich_early_signal_event(
            event,
            projects,
            session=http,
            fetch_details=fetch_details,
        )
        if payload is None:
            skipped += 1
            results.append(
                {
                    "id": event.id,
                    "external_id": event.external_id,
                    "property_type": event.property_type,
                    "region": event.region,
                    "status": "no_match",
                }
            )
            continue

        if payload.get("url_link"):
            event.url_link = payload["url_link"]
        event.address = payload.get("address") or ""
        event.applicant = payload.get("applicant") or ""
        event.project_value = payload.get("project_value") or ""
        enriched += 1
        results.append(
            {
                "id": event.id,
                "external_id": event.external_id,
                "property_type": event.property_type,
                "region": event.region,
                "status": "enriched",
                **payload,
            }
        )

    if enriched and persist:
        db_session.commit()
    elif enriched:
        db_session.rollback()

    return {
        "projects_indexed": len(projects),
        "candidates": len(events),
        "enriched": enriched,
        "skipped": skipped,
        "results": results,
    }


def run_early_signal_enrichment(
    *,
    limit: int | None = None,
    force: bool = False,
    fetch_details: bool = True,
    persist: bool = True,
) -> dict[str, Any]:
    from db.connection import get_session, init_db

    init_db()
    db_session = get_session()
    try:
        if not persist:
            return enrich_early_signal_events(
                db_session,
                limit=limit,
                force=force,
                fetch_details=fetch_details,
                persist=False,
            )
        return enrich_early_signal_events(
            db_session,
            limit=limit,
            force=force,
            fetch_details=fetch_details,
            persist=True,
        )
    finally:
        db_session.close()
