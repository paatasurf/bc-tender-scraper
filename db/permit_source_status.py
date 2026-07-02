"""Source permit status extraction and backfill (Permit Lifecycle Phase 2b)."""

from __future__ import annotations

from typing import Any, Iterator
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from db.models import Permit
from scraper.config import VANCOUVER_PERMITS_API, SURREY_PERMITS_API
from scraper.utils import clean_text, create_session, polite_api_get

# Exact API / ArcGIS attribute names mapped into permits.source_status_raw.
VANCOUVER_STATUS_SOURCE_FIELD = "permitcategory"
SURREY_STATUS_SOURCE_FIELD = "PermitStatus"

PERMIT_STATUS_BACKFILL_SOURCES: tuple[str, ...] = ("vancouver", "surrey")


def normalize_permit_source_status(raw: Any) -> str:
    """Return raw source status string; unknown/missing → empty (never guess)."""
    if raw is None:
        return ""
    if isinstance(raw, list):
        parts = [clean_text(str(item)) for item in raw if clean_text(str(item))]
        return " / ".join(parts)
    return clean_text(str(raw))


def extract_vancouver_source_status(record: dict[str, Any]) -> str:
    return normalize_permit_source_status(record.get(VANCOUVER_STATUS_SOURCE_FIELD))


def extract_surrey_source_status(attrs: dict[str, Any]) -> str:
    return normalize_permit_source_status(attrs.get(SURREY_STATUS_SOURCE_FIELD))


def count_source_status_state(session: Session, *, source: str) -> dict[str, int]:
    row = session.execute(
        text(
            """
            SELECT
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE source_status_raw <> '') AS source_status_set,
                COUNT(*) FILTER (WHERE source_status_raw = '') AS source_status_empty
            FROM permits
            WHERE source = :source
            """
        ),
        {"source": source},
    ).one()
    return dict(row._mapping)


def collect_vancouver_status_vocabulary() -> list[str]:
    response = polite_api_get(
        create_session(),
        VANCOUVER_PERMITS_API,
        params={
            "limit": 100,
            "group_by": f"{VANCOUVER_STATUS_SOURCE_FIELD} as cat",
            "select": "count(*) as c, cat",
        },
    )
    response.raise_for_status()
    values: list[str] = []
    for row in response.json().get("results", []):
        raw = normalize_permit_source_status(row.get("cat"))
        if raw:
            values.append(raw)
    return sorted(values)


def collect_surrey_status_vocabulary() -> list[str]:
    session = create_session()
    try:
        response = polite_api_get(
            session,
            f"{SURREY_PERMITS_API}/query",
            params={
                "where": "1=1",
                "outFields": SURREY_STATUS_SOURCE_FIELD,
                "returnDistinctValues": "true",
                "returnGeometry": "false",
                "f": "json",
            },
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("error"):
            return []
        values: list[str] = []
        for feature in payload.get("features") or []:
            raw = extract_surrey_source_status(feature.get("attributes") or {})
            if raw:
                values.append(raw)
        return sorted(set(values))
    except Exception:
        return []


def collect_source_status_vocabularies() -> dict[str, list[str]]:
    return {
        "vancouver": collect_vancouver_status_vocabulary(),
        "surrey": collect_surrey_status_vocabulary(),
    }


def iter_vancouver_status_by_external_id() -> Iterator[tuple[str, str]]:
    from scraper.building_permits import iter_vancouver_permits

    for record in iter_vancouver_permits(days=None):
        external_id = clean_text(record.get("external_id"))
        if not external_id:
            continue
        status = extract_vancouver_source_status(record)
        if status:
            yield external_id, status


def iter_surrey_status_by_external_id() -> Iterator[tuple[str, str]]:
    from scraper.surrey_permits import iter_surrey_permits

    for record in iter_surrey_permits(days=None):
        external_id = clean_text(record.get("external_id"))
        if not external_id:
            continue
        status = clean_text(record.get("source_status_raw") or "")
        if status:
            yield external_id, status


def _status_lookup_for_source(source: str) -> dict[str, str]:
    if source == "vancouver":
        return dict(iter_vancouver_status_by_external_id())
    if source == "surrey":
        return dict(iter_surrey_status_by_external_id())
    return {}


def backfill_permit_source_status(
    session: Session,
    *,
    only_empty: bool = True,
    sources: tuple[str, ...] = PERMIT_STATUS_BACKFILL_SOURCES,
) -> dict[str, Any]:
    """Idempotent backfill of source_status_raw from municipal source APIs."""
    summary: dict[str, Any] = {
        "only_empty": only_empty,
        "source_fields": {
            "vancouver": VANCOUVER_STATUS_SOURCE_FIELD,
            "surrey": SURREY_STATUS_SOURCE_FIELD,
        },
        "cities": {},
    }

    for source in sources:
        before = count_source_status_state(session, source=source)
        lookup = _status_lookup_for_source(source)
        updated = 0
        skipped_no_source = 0
        skipped_already_set = 0

        query = select(Permit).where(Permit.source == source)
        if only_empty:
            query = query.where(Permit.source_status_raw == "")

        for permit in session.scalars(query):
            if only_empty and permit.source_status_raw.strip():
                skipped_already_set += 1
                continue
            external_id = clean_text(permit.external_id)
            if not external_id:
                skipped_no_source += 1
                continue
            status = lookup.get(external_id, "")
            if not status:
                skipped_no_source += 1
                continue
            if permit.source_status_raw == status:
                continue
            permit.source_status_raw = status[:100]
            updated += 1

        if updated:
            session.commit()

        after = count_source_status_state(session, source=source)
        summary["cities"][source] = {
            "updated": updated,
            "skipped_no_source": skipped_no_source,
            "skipped_already_set": skipped_already_set,
            "lookup_rows": len(lookup),
            "before_set": before["source_status_set"],
            "before_empty": before["source_status_empty"],
            "after_set": after["source_status_set"],
            "after_empty": after["source_status_empty"],
        }

    summary["vocabulary"] = collect_source_status_vocabularies()
    summary["totals"] = {
        "updated": sum(item["updated"] for item in summary["cities"].values()),
        "after_set": sum(item["after_set"] for item in summary["cities"].values()),
        "after_empty": sum(item["after_empty"] for item in summary["cities"].values()),
    }
    return summary
