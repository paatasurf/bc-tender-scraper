"""
Validate Phase 4A pending-permit gap against Vancouver Open Data.

The production `permits` table does not yet store permitnumbercreateddate;
this script queries the Vancouver issued-building-permits API directly and
optionally compares issue_date coverage in the local/production DB.
"""
from __future__ import annotations

import json
import sys
from datetime import date, timedelta

import requests

from db.connection import get_session, init_db
from sqlalchemy import text

API = (
    "https://opendata.vancouver.ca/api/explore/v2.1/catalog/datasets/"
    "issued-building-permits/records"
)
PAGE_SIZE = 100
LOOKBACK_DAYS = 90
MIN_VALUE = 250_000


def _fetch(where: str, *, limit: int = PAGE_SIZE, offset: int = 0, order_by: str = "-permitnumbercreateddate") -> dict:
    resp = requests.get(
        API,
        params={"where": where, "limit": limit, "offset": offset, "order_by": order_by},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()


def _parse_date(raw: str | None) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw)[:10])
    except ValueError:
        return None


def query_vancouver_gap(*, since: date, today: date) -> dict:
    since_str = since.isoformat()

    # A) Application filed recently, no issue date in dataset
    pending_no_issue = _fetch(
        f"permitnumbercreateddate >= '{since_str}' AND issuedate IS NULL"
    )
    # B) Application filed recently, issue date still in the future (in-flight)
    pending_future_issue = _fetch(
        f"permitnumbercreateddate >= '{since_str}' AND issuedate > '{today.isoformat()}'"
    )
    # C) Application filed recently, not yet issued as of today (null OR future issue)
    pending_either = _fetch(
        f"permitnumbercreateddate >= '{since_str}' AND "
        f"(issuedate IS NULL OR issuedate > '{today.isoformat()}')"
    )
    # D) High-value subset of (C)
    pending_high_value = _fetch(
        f"permitnumbercreateddate >= '{since_str}' AND "
        f"(issuedate IS NULL OR issuedate > '{today.isoformat()}') AND "
        f"projectvalue >= {MIN_VALUE}"
    )
    # E) Recently filed with issue date already set (for lag analysis)
    issued_with_lag = _fetch(
        f"permitnumbercreateddate >= '{since_str}' AND issuedate >= '{since_str}'",
        limit=100,
    )
    # Paginate up to 500 for lag stats
    all_lag_rows = list(issued_with_lag.get("results", []))
    total_lag = issued_with_lag.get("total_count", 0)
    offset = len(all_lag_rows)
    while offset < min(total_lag, 500):
        page = _fetch(
            f"permitnumbercreateddate >= '{since_str}' AND issuedate >= '{since_str}'",
            limit=100,
            offset=offset,
        )
        batch = page.get("results", [])
        if not batch:
            break
        all_lag_rows.extend(batch)
        offset += len(batch)

    lags: list[int] = []
    for row in all_lag_rows:
        created = _parse_date(row.get("permitnumbercreateddate"))
        issued = _parse_date(row.get("issuedate"))
        if created and issued:
            lags.append((issued - created).days)

    lag_stats = {}
    if lags:
        lags.sort()
        lag_stats = {
            "count": len(lags),
            "min_days": lags[0],
            "median_days": lags[len(lags) // 2],
            "p90_days": lags[int(len(lags) * 0.9)],
            "max_days": lags[-1],
        }

    return {
        "since": since_str,
        "today": today.isoformat(),
        "counts": {
            "pending_no_issue_date": pending_no_issue.get("total_count", 0),
            "pending_future_issue_date": pending_future_issue.get("total_count", 0),
            "pending_either": pending_either.get("total_count", 0),
            "pending_high_value_ge_250k": pending_high_value.get("total_count", 0),
            "issued_with_both_dates": total_lag,
        },
        "lag_stats_application_to_issue": lag_stats,
        "sample_pending_high_value": pending_high_value.get("results", [])[:10],
        "sample_pending_no_issue": pending_no_issue.get("results", [])[:10],
        "sample_recent_with_lag": sorted(
            all_lag_rows,
            key=lambda r: float(r.get("projectvalue") or 0),
            reverse=True,
        )[:10],
        "findings": {
            "dataset_includes_only_issued_permits": (
                pending_either.get("total_count", 0) == 0
                and pending_no_issue.get("total_count", 0) == 0
            ),
            "phase_4a_implication": (
                "Ingest permitnumbercreateddate to gain retrospective lead time on newly "
                "published permits (alert when first seen in dataset, using application date "
                "as the event timestamp). True in-flight pending permits are NOT exposed "
                "by this API until issuance."
            ),
        },
    }


def query_production_db() -> dict:
    init_db()
    session = get_session()
    try:
        cols = session.execute(
            text(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_name = 'permits'
                ORDER BY ordinal_position
                """
            )
        ).fetchall()
        col_names = [r[0] for r in cols]

        totals = session.execute(
            text(
                """
                SELECT
                    COUNT(*) AS total,
                    COUNT(*) FILTER (WHERE source = 'vancouver') AS vancouver,
                    COUNT(*) FILTER (WHERE issue_date = '' OR issue_date IS NULL) AS empty_issue_date,
                    COUNT(*) FILTER (WHERE external_id <> '') AS has_external_id
                FROM permits
                """
            )
        ).one()

        recent_empty = session.execute(
            text(
                """
                SELECT id, address, permit_type, project_value, applicant, issue_date, external_id
                FROM permits
                WHERE source = 'vancouver'
                  AND (issue_date = '' OR issue_date IS NULL)
                ORDER BY id DESC
                LIMIT 10
                """
            )
        ).fetchall()

        return {
            "columns": col_names,
            "has_application_date_column": "application_date" in col_names,
            "has_permitnumbercreateddate_column": "permitnumbercreateddate" in col_names,
            "totals": dict(totals._mapping),
            "sample_vancouver_empty_issue_date": [dict(r._mapping) for r in recent_empty],
            "note": (
                "Production DB cannot filter by permitnumbercreateddate until Phase 4A "
                "schema migration; gap validation uses Vancouver Open Data API."
            ),
        }
    finally:
        session.close()


def main() -> None:
    today = date.today()
    since = today - timedelta(days=LOOKBACK_DAYS)

    print("=== Production DB snapshot ===")
    db = query_production_db()
    print(json.dumps(db, indent=2, default=str))

    print("\n=== Vancouver Open Data pending-permit gap ===")
    gap = query_vancouver_gap(since=since, today=today)
    print(json.dumps(gap, indent=2, default=str))


if __name__ == "__main__":
    main()
