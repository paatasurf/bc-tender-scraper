"""Project Intelligence — participant contacts for tenders, permits, and early signals."""

from __future__ import annotations

import re
from typing import Any, Literal

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from db.constants import BATCH_SIZE
from db.models import EarlySignalEvent, Permit, ProjectContact
from pipeline.company_classification import parse_name

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
