"""Permit upsert helpers for multi-city imports."""

from __future__ import annotations

from sqlalchemy import delete, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from db.constants import BATCH_SIZE
from db.models import Permit
from db.permit_lifecycle_constants import PERMIT_LIFECYCLE_IMPORT_SKIP_COLUMNS
from pipeline.company_resolution import CompanyResolver
from pipeline.permit_company_resolution import resolve_permit_company_from_row

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
    "source": 50,
    "city": 100,
    "external_id": 100,
    "source_status_raw": 100,
}

_PERMIT_TABLE = Permit.__table__
# official_source_id is written only by a dedicated, digest-pinned
# identity-bridge writer (pipeline.permit_official_source_id_bridge) --
# excluding it here means the generic scraper upsert can never set, blank,
# or overwrite it on insert or conflict-update, regardless of what (if
# anything) a row dict contains for that key.
_SKIP_ON_UPDATE = {
    "id",
    "scraped_at",
    "official_source_id",
} | PERMIT_LIFECYCLE_IMPORT_SKIP_COLUMNS
_IMPORTABLE_COLUMNS = {col.name for col in _PERMIT_TABLE.columns} - _SKIP_ON_UPDATE


def _clamp_permit_row(row: dict[str, str]) -> dict[str, str]:
    clamped = dict(row)
    for field, limit in PERMIT_VARCHAR_LIMITS.items():
        value = clamped.get(field) or ""
        if len(value) > limit:
            clamped[field] = value[:limit]
    return clamped


def _permit_fingerprint(
    row: dict[str, str],
    *,
    source: str,
) -> tuple[str, str, str, str]:
    return (
        source,
        (row.get("address") or "").strip(),
        (row.get("project_value") or "").strip(),
        (row.get("applicant") or "").strip(),
    )


def _dedupe_permit_rows(
    rows: list[dict[str, str]],
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Return (keyed_rows, blank_external_id_rows) after in-batch dedupe."""
    keyed: dict[tuple[str, str], dict[str, str]] = {}
    blank: dict[tuple[str, str, str, str], dict[str, str]] = {}
    for row in rows:
        external_id = row.get("external_id") or ""
        source = row.get("source") or ""
        if external_id:
            keyed[(source, external_id)] = row
            continue
        fingerprint = _permit_fingerprint(row, source=source)
        if not fingerprint[1] or not fingerprint[2]:
            continue
        blank[fingerprint] = row
    return list(keyed.values()), list(blank.values())


def _importable_row_values(row: dict[str, str], *, source: str) -> dict[str, object]:
    values = {key: row.get(key) for key in _IMPORTABLE_COLUMNS}
    for key, value in values.items():
        # A row dict missing a key (rather than holding an explicit value) must
        # not surface as NULL on a NOT NULL text column -- Core-level insert()
        # bypasses the ORM's Python-side "" default, so fill it in here.
        if value is None and not _PERMIT_TABLE.columns[key].nullable:
            values[key] = ""
    values.setdefault("source", source)
    return values


def _fingerprint_match_filters(source: str, fingerprint: tuple[str, str, str, str]):
    _, address, project_value, applicant = fingerprint
    return (
        Permit.source == source,
        Permit.address == address,
        Permit.project_value == project_value,
        Permit.applicant == applicant,
    )


def _keyed_permit_exists_for_fingerprint(
    session: Session,
    row: dict[str, str],
    *,
    source: str,
) -> bool:
    fingerprint = _permit_fingerprint(row, source=source)
    existing = session.scalar(
        select(Permit.id)
        .where(*_fingerprint_match_filters(source, fingerprint))
        .where(Permit.external_id != "")
        .limit(1)
    )
    return existing is not None


def _upsert_blank_fingerprint_permit(
    session: Session,
    row: dict[str, str],
    *,
    source: str,
) -> bool:
    """Insert or update a permit row that lacks external_id (one row per fingerprint)."""
    if row.get("external_id"):
        return False
    if _keyed_permit_exists_for_fingerprint(session, row, source=source):
        return False

    fingerprint = _permit_fingerprint(row, source=source)
    existing_id = session.scalar(
        select(Permit.id)
        .where(*_fingerprint_match_filters(source, fingerprint))
        .where(Permit.external_id == "")
        .limit(1)
    )
    values = _importable_row_values(row, source=source)
    values["external_id"] = ""

    if existing_id is not None:
        session.execute(update(Permit).where(Permit.id == existing_id).values(**values))
        return True

    session.execute(insert(_PERMIT_TABLE).values(values))
    return True


def _promote_blank_permit_if_exists(
    session: Session,
    row: dict[str, str],
    *,
    source: str,
) -> bool:
    """Upgrade a blank-external_id duplicate to the keyed row instead of inserting anew."""
    external_id = row.get("external_id") or ""
    if not external_id:
        return False

    fingerprint = _permit_fingerprint(row, source=source)
    if not fingerprint[1] or not fingerprint[2]:
        return False

    existing_id = session.scalar(
        select(Permit.id)
        .where(*_fingerprint_match_filters(source, fingerprint))
        .where(Permit.external_id == "")
        .limit(1)
    )
    if existing_id is None:
        return False

    values = _importable_row_values(row, source=source)
    keyed_id = session.scalar(
        select(Permit.id)
        .where(Permit.source == source, Permit.external_id == external_id)
        .limit(1)
    )
    if keyed_id is not None:
        # A prior keyed import and a legacy blank-id row describe the same
        # permit. Updating the blank row to this key would violate the partial
        # unique index, so retain the keyed record and remove only its blank
        # duplicate.
        session.execute(update(Permit).where(Permit.id == keyed_id).values(**values))
        session.execute(delete(Permit).where(Permit.id == existing_id))
        return True

    session.execute(update(Permit).where(Permit.id == existing_id).values(**values))
    return True


def _resolution_row_for_source(row: dict[str, str], *, source: str) -> dict[str, str]:
    """Row used only for company resolution -- never the row that gets written
    to Permit or dual-written to KG. For Surrey, applicant is swapped to the
    safely-normalized organization (never the raw ApplicantOrganization
    string, which commonly has a mailing address appended) so Company
    Discovery never sees the raw value. Vancouver/Burnaby resolve against
    the row unchanged."""
    if source != "surrey":
        return row
    resolution_row = dict(row)
    resolution_row["applicant"] = row.get("normalized_applicant") or ""
    return resolution_row


def _attach_company_ids(
    session: Session,
    rows: list[dict[str, str]],
    *,
    source: str,
) -> None:
    # Shared across the whole batch: CompanyResolver caches the full companies
    # table on first use, so building a fresh one per row makes resolution
    # O(rows x companies) instead of O(rows + companies).
    resolver = CompanyResolver(session)
    for row in rows:
        result = resolve_permit_company_from_row(
            session,
            _resolution_row_for_source(row, source=source),
            source=source,
            create_if_missing=source != "surrey",
            resolver=resolver,
        )
        if result.company_id is None:
            continue
        row["company_id"] = result.company_id
        row["canonical_merge_confidence"] = result.confidence
        row["canonical_merge_method"] = result.method


def upsert_city_permits(
    session: Session,
    rows: list[dict[str, str]],
    *,
    source: str,
    full_refresh: bool,
) -> int:
    clamped = [_clamp_permit_row(row) for row in rows]
    keyed_rows, blank_rows = _dedupe_permit_rows(clamped)
    if not keyed_rows and not blank_rows:
        return 0

    if full_refresh:
        session.execute(delete(Permit).where(Permit.source == source))
        session.commit()

    all_rows = keyed_rows + blank_rows
    _attach_company_ids(session, all_rows, source=source)

    imported = 0

    for row in blank_rows:
        if _upsert_blank_fingerprint_permit(session, row, source=source):
            imported += 1
        session.commit()

    keyed_for_batch: list[dict[str, str]] = []
    for row in keyed_rows:
        if _promote_blank_permit_if_exists(session, row, source=source):
            imported += 1
            session.commit()
            continue
        keyed_for_batch.append(row)

    for start in range(0, len(keyed_for_batch), BATCH_SIZE):
        batch = [
            _importable_row_values(row, source=source)
            for row in keyed_for_batch[start : start + BATCH_SIZE]
        ]
        stmt = insert(_PERMIT_TABLE).values(batch)
        if full_refresh:
            session.execute(stmt)
        else:
            update_cols = {
                col.name: stmt.excluded[col.name]
                for col in _PERMIT_TABLE.columns
                if col.name not in _SKIP_ON_UPDATE
            }
            stmt = stmt.on_conflict_do_update(
                index_elements=["source", "external_id"],
                index_where=text("external_id <> ''"),
                set_=update_cols,
            )
            session.execute(stmt)
        session.commit()
        imported += len(batch)

    _dual_write_permit_observations_safe(session, all_rows, source=source)
    return imported


def _dual_write_permit_observations_safe(
    session: Session,
    rows: list[dict[str, str]],
    *,
    source: str,
) -> None:
    """Best-effort KG Observation dual-write — never affects permit import outcome."""
    try:
        from pipeline.kg.adapters.permit import dual_write_permit_observations

        dual_write_permit_observations(session, rows, source=source)
    except Exception:
        import logging

        logging.getLogger(__name__).exception(
            "KG permit observation dual-write failed for source=%s (permit import unaffected)",
            source,
        )
