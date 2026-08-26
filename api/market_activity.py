"""Read-only event-time market activity analytics."""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select

from db.models import Company, CompanyApplicantAlias, ContractAward, Permit


def parse_event_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def parse_value(value: Any) -> float:
    try:
        return float(str(value or 0).replace(",", "").replace("$", "").strip())
    except (TypeError, ValueError):
        return 0.0


def _scope_cities(scope: str) -> set[str] | None:
    value = scope.strip()
    if value.casefold() in {"bc", "b.c.", "british columbia"}:
        return None
    if value.casefold() in {"metro vancouver", "metro vancouver region"}:
        return {"Burnaby", "Vancouver", "Surrey", "Richmond", "Coquitlam", "New Westminster", "Delta", "Langley", "North Vancouver", "West Vancouver", "Maple Ridge", "Port Coquitlam", "Port Moody", "White Rock"}
    return {part.strip().title() for part in value.split(",") if part.strip()}


def collect_events(session: Any, scope: str) -> tuple[list[dict[str, Any]], int]:
    cities = _scope_cities(scope)
    permit_query = select(Permit).where(Permit.company_id.isnot(None), Permit.is_active.is_(True))
    award_query = select(ContractAward).where(ContractAward.company_id.isnot(None))
    if cities is not None:
        permit_query = permit_query.where(Permit.city.in_(cities))
        award_query = award_query.where(ContractAward.winner_city.in_(cities))
    else:
        award_query = award_query.where(
            ContractAward.winner_province.ilike("BC")
            | ContractAward.winner_province.ilike("British Columbia")
        )
    events: list[dict[str, Any]] = []
    invalid = 0
    for row in session.scalars(permit_query).all():
        event_date = parse_event_date(row.issue_date) or parse_event_date(row.application_date)
        if event_date is None:
            invalid += 1
            continue
        events.append({"kind": "permit", "id": row.id, "raw_company_id": int(row.company_id), "event_date": event_date, "city": row.city, "value": parse_value(row.project_value), "confidence": row.canonical_merge_confidence})
    for row in session.scalars(award_query).all():
        event_date = parse_event_date(row.award_date)
        if event_date is None:
            invalid += 1
            continue
        events.append({"kind": "award", "id": row.id, "raw_company_id": int(row.company_id), "event_date": event_date, "city": row.winner_city, "value": float(row.award_value or 0), "confidence": row.match_confidence})
    return events, invalid


def canonical_map(session: Any, raw_ids: set[int]) -> tuple[dict[int, int], dict[int, Company]]:
    companies = {row.id: row for row in session.scalars(select(Company).where(Company.id.in_(list(raw_ids)))).all()} if raw_ids else {}
    aliases = session.scalars(select(CompanyApplicantAlias).where(CompanyApplicantAlias.alias_company_id.in_(list(raw_ids)))).all() if raw_ids else []
    alias_map = {int(row.alias_company_id): int(row.canonical_company_id) for row in aliases}
    mapping: dict[int, int] = {}
    for raw_id in raw_ids:
        current, seen = raw_id, set()
        while current not in seen:
            seen.add(current)
            row = companies.get(current) or session.get(Company, current)
            next_id = alias_map.get(current) or (int(row.canonical_company_id) if row and row.canonical_company_id else None)
            if next_id is None:
                mapping[raw_id] = current if row else None  # type: ignore[assignment]
                break
            current = next_id
    return mapping, companies


def fastest_growing(session: Any, scope: str, lookback_days: int, limit: int, minimum_activity: int) -> dict[str, Any]:
    today = date.today()
    current_start = today - timedelta(days=lookback_days)
    previous_start = current_start - timedelta(days=lookback_days)
    events, invalid = collect_events(session, scope)
    raw_ids = {e["raw_company_id"] for e in events}
    mapping, companies = canonical_map(session, raw_ids)
    groups: dict[int, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: {"current": [], "previous": []})
    unresolved = 0
    for event in events:
        cid = mapping.get(event["raw_company_id"])
        if cid is None:
            unresolved += 1
            continue
        if current_start <= event["event_date"] <= today:
            groups[cid]["current"].append(event)
        elif previous_start <= event["event_date"] < current_start:
            groups[cid]["previous"].append(event)
    candidates = []
    for cid, windows in groups.items():
        current, previous = windows["current"], windows["previous"]
        if len(current) < minimum_activity:
            continue
        company = companies.get(cid) or session.get(Company, cid)
        if company is None:
            continue
        current_value = sum(e["value"] for e in current); previous_value = sum(e["value"] for e in previous)
        delta = len(current) - len(previous)
        relative = delta / max(len(previous), 1)
        latest = max(e["event_date"] for e in current)
        recency = max(0.0, 1.0 - (today - latest).days / max(1, lookback_days))
        candidates.append({"company_id": cid, "company_name": (company.display_name or company.name).strip(), "current_activity_count": len(current), "previous_activity_count": len(previous), "absolute_delta": delta, "relative_growth": round(relative, 4), "permits_current": sum(e["kind"] == "permit" for e in current), "permits_previous": sum(e["kind"] == "permit" for e in previous), "awards_current": sum(e["kind"] == "award" for e in current), "awards_previous": sum(e["kind"] == "award" for e in previous), "known_value_current": round(current_value, 2), "known_value_previous": round(previous_value, 2), "latest_activity_date": latest.isoformat(), "evidence_confidence": "HIGH" if all(e["confidence"] is not None for e in current) else "MEDIUM", "evidence": ["permits.issue_date/application_date", "contract_awards.award_date"], "_recency": recency, "_value_delta": max(0.0, current_value - previous_value)})
    max_delta = max((max(0, row["absolute_delta"]) for row in candidates), default=1)
    max_value = max((row["_value_delta"] for row in candidates), default=0.0)
    for row in candidates:
        volume = 45 * max(0, row["absolute_delta"]) / max_delta
        growth = 25 * min(max(row["relative_growth"], 0.0), 3.0) / 3.0
        recency = 20 * row.pop("_recency")
        economy = 10 * (math.log1p(row.pop("_value_delta")) / math.log1p(max_value) if max_value > 0 else 0)
        row["growth_score"] = round(volume + growth + recency + economy, 2)
    candidates.sort(key=lambda row: (-row["growth_score"], -row["absolute_delta"], -row["current_activity_count"], row["company_id"]))
    return {"analysis": "Fastest-growing Recent Company Activity", "geography": scope, "lookback_days": lookback_days, "minimum_activity": minimum_activity, "candidate_companies": len(candidates), "raw_company_ids": len(raw_ids), "unresolved_activity_records": unresolved, "invalid_event_dates_excluded": invalid, "current_window": {"start": current_start.isoformat(), "end": today.isoformat()}, "previous_window": {"start": previous_start.isoformat(), "end": (current_start - timedelta(days=1)).isoformat()}, "scoring": {"absolute_activity_increase": 45, "relative_increase_capped": 25, "recency": 20, "economic_significance": 10, "deterministic": True}, "data_gaps": ["Tenders and early signals excluded because reliable company attribution is unavailable."], "data": candidates[:limit]}


def geographic_footprint(session: Any, company_id: int, lookback_days: int, limit: int) -> dict[str, Any]:
    events, invalid = collect_events(session, "BC")
    mapping, companies = canonical_map(session, {e["raw_company_id"] for e in events} | {company_id})
    canonical_id = mapping.get(company_id, company_id)
    selected = [e for e in events if mapping.get(e["raw_company_id"]) == canonical_id and e["event_date"] >= date.today() - timedelta(days=lookback_days)]
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in selected:
        grouped[event["city"] or "Unknown"].append(event)
    total = len(selected)
    company = companies.get(canonical_id) or session.get(Company, canonical_id)
    data = []
    for geography, rows in grouped.items():
        data.append({"geography": geography, "total_activity": len(rows), "permits": sum(e["kind"] == "permit" for e in rows), "awards": sum(e["kind"] == "award" for e in rows), "known_value": round(sum(e["value"] for e in rows), 2), "latest_activity": max(e["event_date"] for e in rows).isoformat(), "share_of_company_activity": round(len(rows) / total, 4) if total else 0, "evidence_confidence": "HIGH" if all(e["confidence"] is not None for e in rows) else "MEDIUM", "evidence": ["permits.city", "contract_awards.winner_city"]})
    data.sort(key=lambda row: (-row["total_activity"], -row["known_value"], row["geography"]))
    return {"analysis": "Company Geographic Footprint", "company_id": canonical_id, "company_name": (company.display_name or company.name).strip() if company else None, "lookback_days": lookback_days, "raw_company_id": company_id, "canonical_company_id": canonical_id, "activity_records_considered": total, "invalid_event_dates_excluded": invalid, "data_gaps": ["Tenders and early signals excluded because reliable company attribution is unavailable."], "data": data[:limit]}
