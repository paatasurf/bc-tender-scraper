"""Project Intelligence — participant contacts for tenders, permits, and early signals."""

from __future__ import annotations

import re
from typing import Any, Literal

from sqlalchemy import delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from db.constants import BATCH_SIZE
from db.models import Company, EarlySignalEvent, Permit, ProjectContact
from pipeline.company_matching import normalize_vendor_name
from pipeline.company_classification import parse_name
from pipeline.opportunity_discovery import _applicant_search_tokens

ProjectType = Literal["tender", "permit", "early_signal"]
ContactRole = Literal["architect", "gc", "developer"]

EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)
PHONE_RE = re.compile(
    r"(?:\+?1[-.\s]?)?(?:\(\d{3}\)|\d{3})[-.\s]?\d{3}[-.\s]?\d{4}"
)


def _clean(value: str | None) -> str:
    return (value or "").strip()


def _extract_email(text: str) -> str:
    match = EMAIL_RE.search(text)
    return match.group(0) if match else ""


def _extract_phone(text: str) -> str:
    match = PHONE_RE.search(text)
    return match.group(0) if match else ""


def contact_from_party_name(
    raw_name: str,
    *,
    project_id: int,
    project_type: ProjectType,
    role: ContactRole,
    source: str,
) -> dict[str, Any] | None:
    full = _clean(raw_name)
    if not full:
        return None

    parsed = parse_name(full)
    company_name = parsed["dba"] or parsed["legal"] or full
    contact_name = parsed["legal"] if parsed["has_dba"] else ""

    return {
        "project_id": project_id,
        "project_type": project_type,
        "role": role,
        "company_name": company_name[:300],
        "contact_name": contact_name[:300],
        "phone": _extract_phone(full)[:50],
        "email": _extract_email(full)[:320],
        "source": _clean(source)[:100],
    }


def contacts_from_permit(permit: Permit) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    source = _clean(permit.source) or "permit"

    architect = contact_from_party_name(
        permit.applicant,
        project_id=permit.id,
        project_type="permit",
        role="architect",
        source=source,
    )
    if architect:
        rows.append(architect)

    gc = contact_from_party_name(
        permit.contractor,
        project_id=permit.id,
        project_type="permit",
        role="gc",
        source=source,
    )
    if gc:
        rows.append(gc)

    return rows


def contacts_from_early_signal(event: EarlySignalEvent) -> list[dict[str, Any]]:
    row = contact_from_party_name(
        event.applicant,
        project_id=event.id,
        project_type="early_signal",
        role="developer",
        source=_clean(event.source) or "early_signal",
    )
    return [row] if row else []


def _iter_permit_contacts(session: Session) -> list[dict[str, Any]]:
    permits = session.scalars(select(Permit).order_by(Permit.id)).all()
    rows: list[dict[str, Any]] = []
    for permit in permits:
        rows.extend(contacts_from_permit(permit))
    return rows


def _iter_early_signal_contacts(session: Session) -> list[dict[str, Any]]:
    events = session.scalars(select(EarlySignalEvent).order_by(EarlySignalEvent.id)).all()
    rows: list[dict[str, Any]] = []
    for event in events:
        rows.extend(contacts_from_early_signal(event))
    return rows


def _insert_contacts(session: Session, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0

    table = ProjectContact.__table__
    imported = 0
    for start in range(0, len(rows), BATCH_SIZE):
        batch = rows[start : start + BATCH_SIZE]
        session.execute(insert(table).values(batch))
        session.commit()
        imported += len(batch)
    return imported


def rebuild_project_contacts(session: Session) -> dict[str, int]:
    """Rebuild permit and early_signal contacts from current source tables."""
    permit_count = session.scalar(select(func.count()).select_from(Permit)) or 0
    event_count = session.scalar(select(func.count()).select_from(EarlySignalEvent)) or 0

    session.execute(
        delete(ProjectContact).where(ProjectContact.project_type.in_(("permit", "early_signal")))
    )
    session.commit()

    permit_rows = _iter_permit_contacts(session)
    early_rows = _iter_early_signal_contacts(session)
    all_rows = permit_rows + early_rows
    persisted = _insert_contacts(session, all_rows)

    return {
        "permits_scanned": int(permit_count),
        "permit_contacts_built": len(permit_rows),
        "early_signals_scanned": int(event_count),
        "early_signal_contacts_built": len(early_rows),
        "contacts_persisted": persisted,
    }


def sync_permit_project_contacts(session: Session) -> dict[str, int]:
    rows = _iter_permit_contacts(session)
    session.execute(delete(ProjectContact).where(ProjectContact.project_type == "permit"))
    session.commit()
    persisted = _insert_contacts(session, rows)
    return {"contacts_built": len(rows), "contacts_persisted": persisted}


def _company_normalized_name(company: Company) -> str:
    for candidate in (company.name, company.canonical_vendor_name or ""):
        normalized = normalize_vendor_name(candidate)
        if normalized:
            return normalized
    return ""


def _company_permit_ids(
    session: Session,
    company: Company,
    *,
    max_permits: int = 1000,
) -> set[int]:
    normalized = _company_normalized_name(company)
    if not normalized:
        return set()

    query = select(Permit.id, Permit.applicant, Permit.contractor)
    tokens = _applicant_search_tokens(company.name)
    if tokens:
        clauses = []
        for token in tokens:
            pattern = f"%{token}%"
            clauses.append(Permit.applicant.ilike(pattern))
            clauses.append(Permit.contractor.ilike(pattern))
        query = query.where(or_(*clauses))

    ids: set[int] = set()
    for permit_id, applicant, contractor in session.execute(
        query.order_by(Permit.id.desc()).limit(max_permits * 8)
    ).all():
        if (
            normalize_vendor_name(applicant) == normalized
            or normalize_vendor_name(contractor) == normalized
        ):
            ids.add(int(permit_id))
        if len(ids) >= max_permits:
            break
    return ids


def _permit_project_payload(permit: Permit) -> dict[str, str]:
    return {
        "address": _clean(permit.address),
        "permit_type": _clean(permit.permit_type),
        "issue_date": _clean(permit.issue_date),
        "project_value": _clean(permit.project_value),
        "city": _clean(permit.city),
    }


def get_company_project_contacts(
    session: Session,
    company_id: int,
    *,
    role: ContactRole | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Return architect/GC contacts from shared permit projects for a company."""
    company = session.get(Company, company_id)
    if company is None:
        raise ValueError(f"Company {company_id} not found")

    normalized = _company_normalized_name(company)
    permit_ids = _company_permit_ids(session, company)
    if not permit_ids:
        return {
            "company_id": company_id,
            "company_name": company.name,
            "total": 0,
            "limit": limit,
            "offset": offset,
            "data": [],
        }

    roles = (role,) if role else ("architect", "gc")
    contacts = session.scalars(
        select(ProjectContact)
        .where(
            ProjectContact.project_type == "permit",
            ProjectContact.project_id.in_(permit_ids),
            ProjectContact.role.in_(roles),
        )
        .order_by(ProjectContact.id.desc())
    ).all()

    permits_by_id = {
        permit.id: permit
        for permit in session.scalars(select(Permit).where(Permit.id.in_(permit_ids))).all()
    }

    rows: list[dict[str, Any]] = []
    seen: set[tuple[int, str, str]] = set()
    for contact in contacts:
        partner_key = normalize_vendor_name(contact.company_name)
        if not partner_key or partner_key == normalized:
            continue

        dedupe_key = (contact.project_id, contact.role, partner_key)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)

        permit = permits_by_id.get(contact.project_id)
        rows.append(
            {
                "id": contact.id,
                "project_id": contact.project_id,
                "project_type": contact.project_type,
                "role": contact.role,
                "company_name": contact.company_name,
                "contact_name": contact.contact_name,
                "phone": contact.phone,
                "email": contact.email,
                "source": contact.source,
                "project": _permit_project_payload(permit) if permit else None,
            }
        )

    rows.sort(
        key=lambda row: (row.get("project") or {}).get("issue_date", ""),
        reverse=True,
    )
    total = len(rows)
    page = rows[offset : offset + limit]
    return {
        "company_id": company_id,
        "company_name": company.name,
        "total": total,
        "limit": limit,
        "offset": offset,
        "data": page,
    }


def _contact_payload(contact: ProjectContact) -> dict[str, Any]:
    return {
        "id": contact.id,
        "project_id": contact.project_id,
        "project_type": contact.project_type,
        "role": contact.role,
        "company_name": contact.company_name,
        "contact_name": contact.contact_name,
        "phone": contact.phone,
        "email": contact.email,
        "source": contact.source,
    }


def get_project_team_contacts(
    session: Session,
    project_type: ProjectType,
    project_id: int,
) -> dict[str, Any]:
    """Return all project_contacts participants for a single project."""
    contacts = session.scalars(
        select(ProjectContact)
        .where(
            ProjectContact.project_type == project_type,
            ProjectContact.project_id == project_id,
        )
        .order_by(ProjectContact.role, ProjectContact.id)
    ).all()

    role_order = {"architect": 0, "gc": 1, "developer": 2}
    rows = [_contact_payload(contact) for contact in contacts]
    rows.sort(key=lambda row: (role_order.get(row["role"], 9), row["company_name"].lower()))

    return {
        "project_id": project_id,
        "project_type": project_type,
        "total": len(rows),
        "data": rows,
    }
