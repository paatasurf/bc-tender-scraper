"""Surrey-specific, identity-aware permit import path (PR-EN1F-1).

Deliberately separate from ``db.permit_import.upsert_city_permits``, which
remains completely untouched by this module and keeps serving Vancouver/
Burnaby unmodified.

A Surrey scraper row's ``external_id`` field holds the source's full
current-format official ``PermitNumber`` (that is already what
``scraper/surrey_permits.py`` puts there today). Matching order, never
fuzzy:

1. Exact match on ``(source='surrey', official_source_id=official number)``
   -- if found, UPDATE that row. Its stored ``external_id`` is NEVER
   touched (it may still hold the shorter legacy key).
2. Exact match on ``(source='surrey', external_id=official number[:16])``
   for a row whose ``official_source_id`` is still blank -- a not-yet-
   bridged legacy row. If found, UPDATE it (fields + ``official_source_id``
   is set to the full number), still never touching ``external_id``.
3. No match at all -- INSERT a new row with
   ``external_id = official_source_id = official number``, so a future
   import of the same permit is found via step 1 and never duplicated.

``applicant`` is preserved: a blank/NULL incoming applicant never
overwrites an already-populated one; a non-blank incoming value always
replaces the stored one (raw, unnormalized).

``company_id``/``Company`` are never touched -- no resolution call exists
in this module at all. Lifecycle/scoring columns are never in the
UPDATE/INSERT column set.

Any ambiguity (more than one legacy-prefix match) or an update that does
not affect exactly one row raises immediately -- fail closed, never
guessed, never silently skipped.

This module never calls ``session.commit()`` or ``session.rollback()``
itself, at any point -- not per row, not per batch. Transaction ownership
belongs entirely to the caller (a future Class-C runner, not shipped in
this PR). On any exception -- unique conflict, ambiguity, unexpected
rowcount, or anything else -- this module simply propagates it; nothing
here has been committed, so the caller's own rollback of the whole
transaction is what makes the failure atomic. No row written by this
module is ever safe to keep without that caller-owned commit.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from db.models import Permit
from scraper.utils import clean_text

LEGACY_EXTERNAL_ID_LENGTH = 16
# Same identity-format contract as pipeline.permit_official_source_id_bridge:
# the legacy production key is DD-DDDDDD-DDD-DD; the current official
# PermitNumber is that prefix plus a single non-alphanumeric separator and
# a short letter/letter-digit suffix.
_LEGACY_ID_RE = re.compile(r"^\d{2}-\d{6}-\d{3}-\d{2}$")
_CURRENT_ID_RE = re.compile(
    r"^\d{2}-\d{6}-\d{3}-\d{2}[^A-Za-z0-9-]"
    r"(?:[A-Za-z]{2}|[A-Za-z]\d(?:[A-Za-z]{2})?)$"
)

WRITABLE_FIELDS = (
    "address",
    "permit_type",
    "project_value",
    "architect",
    "issue_date",
    "application_date",
    "description",
    "contractor",
    "local_area",
)
PERMIT_VARCHAR_LIMITS: dict[str, int] = {
    "address": 300,
    "permit_type": 100,
    "project_value": 50,
    "applicant": 300,
    "architect": 300,
    "issue_date": 20,
    "application_date": 20,
    "contractor": 300,
    "local_area": 100,
    "external_id": 100,
}

__all__ = [
    "LEGACY_EXTERNAL_ID_LENGTH",
    "SurreyIdentityImportError",
    "SurreyImportRowResult",
    "legacy_key",
    "upsert_surrey_permit_identity_aware",
    "upsert_surrey_permits_identity_aware",
]


class SurreyIdentityImportError(ValueError):
    """Raised when a row cannot be safely imported -- ambiguity or an
    unexpected rowcount. Fail-closed: never guessed, never silently
    skipped."""


def legacy_key(official_permit_number: str) -> str | None:
    value = clean_text(official_permit_number)
    if not _CURRENT_ID_RE.fullmatch(value):
        return None
    prefix = value[:LEGACY_EXTERNAL_ID_LENGTH]
    if not _LEGACY_ID_RE.fullmatch(prefix):
        return None
    return prefix


def _clamp(value: str, field: str) -> str:
    limit = PERMIT_VARCHAR_LIMITS.get(field)
    if limit is not None and len(value) > limit:
        return value[:limit]
    return value


def _row_values(row: dict[str, Any]) -> dict[str, str]:
    return {
        field: _clamp(clean_text(row.get(field)), field) for field in WRITABLE_FIELDS
    }


@dataclass(frozen=True)
class SurreyImportRowResult:
    outcome: str  # "updated" | "inserted"
    permit_id: int


def _apply_update(
    session: Session,
    *,
    permit_id: int,
    values: dict[str, str],
    incoming_applicant: str,
    set_official_source_id: str | None,
) -> SurreyImportRowResult:
    set_values: dict[str, Any] = dict(values)
    if incoming_applicant:
        set_values["applicant"] = incoming_applicant
    if set_official_source_id is not None:
        set_values["official_source_id"] = set_official_source_id

    result = session.execute(
        update(Permit)
        .where(Permit.id == permit_id, Permit.source == "surrey")
        .values(**set_values)
        .execution_options(synchronize_session=False)
    )
    if int(result.rowcount or 0) != 1:
        raise SurreyIdentityImportError(
            "identity-aware update did not affect exactly one row"
        )
    return SurreyImportRowResult(outcome="updated", permit_id=permit_id)


def upsert_surrey_permit_identity_aware(
    session: Session,
    row: dict[str, Any],
) -> SurreyImportRowResult:
    """Import exactly one Surrey permit row, identity-aware. Caller owns
    the transaction (commit/rollback) -- this function never commits."""
    official_permit_number = _clamp(clean_text(row.get("external_id")), "external_id")
    if not official_permit_number:
        raise SurreyIdentityImportError(
            "row is missing external_id (official PermitNumber)"
        )

    incoming_applicant = clean_text(row.get("applicant"))
    values = _row_values(row)

    # Tier 1: exact match on official_source_id. The partial unique index
    # on (source, official_source_id) guarantees at most one match.
    existing_id = session.scalar(
        select(Permit.id)
        .where(
            Permit.source == "surrey",
            Permit.official_source_id == official_permit_number,
        )
        .limit(1)
    )
    if existing_id is not None:
        return _apply_update(
            session,
            permit_id=existing_id,
            values=values,
            incoming_applicant=incoming_applicant,
            set_official_source_id=None,
        )

    # Tier 2: exact match on the legacy external_id prefix, only among
    # rows not yet identity-bridged (official_source_id still blank).
    key = legacy_key(official_permit_number)
    if key is not None:
        matches = session.scalars(
            select(Permit.id).where(
                Permit.source == "surrey",
                Permit.external_id == key,
                or_(
                    Permit.official_source_id.is_(None),
                    Permit.official_source_id == "",
                ),
            )
        ).all()
        if len(matches) > 1:
            raise SurreyIdentityImportError(
                "ambiguous legacy match: more than one unbridged row shares "
                "this external_id prefix"
            )
        if len(matches) == 1:
            return _apply_update(
                session,
                permit_id=matches[0],
                values=values,
                incoming_applicant=incoming_applicant,
                set_official_source_id=official_permit_number,
            )

    # Tier 3: no match anywhere -- insert a new, self-identifying row.
    permit = Permit(
        source="surrey",
        city=clean_text(row.get("city")) or "Surrey",
        external_id=official_permit_number,
        official_source_id=official_permit_number,
        applicant=incoming_applicant,
        **values,
    )
    session.add(permit)
    session.flush()
    return SurreyImportRowResult(outcome="inserted", permit_id=int(permit.id))


def upsert_surrey_permits_identity_aware(
    session: Session,
    rows: list[dict[str, Any]],
) -> dict[str, int]:
    """Import a batch of Surrey rows. Never commits, never rolls back --
    transaction ownership belongs entirely to the caller. On any
    exception (unique conflict, ambiguity, unexpected rowcount, or
    anything else) this simply propagates it; since nothing in this
    module ever commits, every row processed so far in this call --
    including any successful updates or inserts -- remains part of the
    caller's single uncommitted transaction, and it is the caller's sole
    responsibility to roll the whole thing back. Nothing this function
    writes is ever durable on its own."""
    updated = 0
    inserted = 0
    for row in rows:
        result = upsert_surrey_permit_identity_aware(session, row)
        if result.outcome == "updated":
            updated += 1
        else:
            inserted += 1
    return {"updated": updated, "inserted": inserted}
