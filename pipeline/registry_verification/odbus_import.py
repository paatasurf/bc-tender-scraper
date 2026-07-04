"""Import Statistics Canada ODB CSV into odbus_reference (batch-versioned)."""

from __future__ import annotations

import csv
import hashlib
import sys
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable

from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from db.market_registry_constants import (
    OBSERVATION_STATUS_ACTIVE,
    OBSERVATION_STATUS_INACTIVE,
    OBSERVATION_STATUS_SUPERSEDED,
    ODBUS_FILTER_ALL,
    ODBUS_FILTER_MODES,
    ODBUS_FILTER_OR_NAICS23,
    ODBUS_FILTER_PRIMARY_NAICS23,
    PRODUCTION_AUTHORIZED_ODBUS_FILTERS,
)
from db.models import OdbusReference
from pipeline.company_matching import normalize_vendor_name
from pipeline.registry_verification.city_normalize import normalize_city

IMPORT_BATCH_SIZE = 5000
MISSING_MARKERS = frozenset({"..", "nan", "none", ""})
ODBUS_EXPORT_SOURCE_OBSERVED_AT = date(2023, 11, 28)

_INACTIVE_STATUS_MARKERS = ("inactive", "dissolved", "cancelled", "closed", "expired")


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in MISSING_MARKERS:
        return ""
    return text


def _clean_naics(value: Any) -> str:
    text = _clean_text(value)
    if not text:
        return ""
    return text.split(".")[0]


def _parse_float(value: Any) -> float | None:
    text = _clean_text(value)
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _naics_prefix(value: Any) -> str:
    return _clean_naics(value)


def row_passes_odbus_filter(row: dict[str, Any], filter_mode: str) -> bool:
    if filter_mode not in ODBUS_FILTER_MODES:
        raise ValueError(f"Unknown ODB filter mode: {filter_mode}")

    if filter_mode == ODBUS_FILTER_ALL:
        return True

    if _clean_text(row.get("prov_terr")).upper() != "BC":
        return False

    source_naics = _naics_prefix(row.get("source_NAICS_primary"))
    derived_naics = _naics_prefix(row.get("derived_NAICS"))

    if filter_mode == ODBUS_FILTER_PRIMARY_NAICS23:
        return source_naics.startswith("23")
    if filter_mode == ODBUS_FILTER_OR_NAICS23:
        return source_naics.startswith("23") or derived_naics.startswith("23")
    return False


def _observation_status_from_source_status(status: str) -> str:
    normalized = (status or "").strip().lower()
    if any(marker in normalized for marker in _INACTIVE_STATUS_MARKERS):
        return OBSERVATION_STATUS_INACTIVE
    return OBSERVATION_STATUS_ACTIVE


def _row_to_reference(row: dict[str, Any]) -> OdbusReference | None:
    idx = _clean_text(row.get("idx"))
    if not idx:
        return None

    business_name = _clean_text(row.get("business_name"))
    alt_name = _clean_text(row.get("alt_business_name"))
    name_for_norm = business_name or alt_name
    normalized_name = normalize_vendor_name(name_for_norm)
    if not normalized_name:
        return None

    city = _clean_text(row.get("city"))
    province = _clean_text(row.get("prov_terr")).upper()
    source_naics = _clean_naics(row.get("source_NAICS_primary"))
    derived_naics = _clean_naics(row.get("derived_NAICS"))
    status = _clean_text(row.get("status"))

    return OdbusReference(
        odbus_idx=idx,
        business_name=business_name,
        alt_business_name=alt_name,
        normalized_name=normalized_name,
        city=city,
        normalized_city=normalize_city(city),
        province=province,
        status=status,
        derived_naics=derived_naics,
        source_naics=source_naics,
        licence_number=_clean_text(row.get("licence_number")),
        business_id_no=_clean_text(row.get("business_id_no")),
        provider=_clean_text(row.get("provider")),
        latitude=_parse_float(row.get("latitude")),
        longitude=_parse_float(row.get("longitude")),
    )


def _reference_to_values(
    reference: OdbusReference,
    *,
    ingest_batch_id: str,
    source_observed_at: date,
    imported_at: datetime,
) -> dict[str, Any]:
    return {
        "odbus_idx": reference.odbus_idx,
        "business_name": reference.business_name,
        "alt_business_name": reference.alt_business_name,
        "normalized_name": reference.normalized_name,
        "city": reference.city,
        "normalized_city": reference.normalized_city,
        "province": reference.province,
        "status": reference.status,
        "derived_naics": reference.derived_naics,
        "source_naics": reference.source_naics,
        "licence_number": reference.licence_number,
        "business_id_no": reference.business_id_no,
        "provider": reference.provider,
        "latitude": reference.latitude,
        "longitude": reference.longitude,
        "ingest_batch_id": ingest_batch_id,
        "source_observed_at": source_observed_at,
        "imported_at": imported_at,
        "observation_status": _observation_status_from_source_status(reference.status),
    }


def assert_production_odbus_apply_allowed(
    *,
    filter_mode: str,
    allow_production: bool,
    database_url: str,
) -> None:
    """Refuse production --apply unless filter is explicitly authorized."""
    from db.db_safety import is_production_database_url

    if not is_production_database_url(database_url) and not allow_production:
        return

    if filter_mode in PRODUCTION_AUTHORIZED_ODBUS_FILTERS:
        return

    print(
        "[odbus_import] Refusing production apply: "
        f"filter {filter_mode!r} is not authorized. "
        f"Production --apply allows only: {sorted(PRODUCTION_AUTHORIZED_ODBUS_FILTERS)}. "
        "Use local DATABASE_URL for or_naics23/all experiments.",
        file=sys.stderr,
    )
    raise SystemExit(1)


def production_apply_authorized(filter_mode: str) -> bool:
    return filter_mode in PRODUCTION_AUTHORIZED_ODBUS_FILTERS


def csv_fingerprint(path: Path) -> dict[str, Any]:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        "path": str(path),
        "sha256": digest,
        "size_bytes": path.stat().st_size,
    }


def plan_odbus_import(
    csv_path: str | Path,
    *,
    filter_mode: str = ODBUS_FILTER_PRIMARY_NAICS23,
) -> dict[str, Any]:
    """Class A plan — parse CSV and count rows without DB writes."""
    path = Path(csv_path)
    if not path.is_file():
        raise FileNotFoundError(f"ODB CSV not found: {path}")
    if filter_mode not in ODBUS_FILTER_MODES:
        raise ValueError(f"Unknown filter mode: {filter_mode}")

    upserted = 0
    skipped = 0
    skip_reasons: dict[str, int] = {}
    sample_upserted: list[dict[str, Any]] = []

    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            if not row_passes_odbus_filter(row, filter_mode):
                continue
            reference = _row_to_reference(row)
            if reference is None:
                skipped += 1
                reason = "missing_idx" if not _clean_text(row.get("idx")) else "unnormalizable_name"
                skip_reasons[reason] = skip_reasons.get(reason, 0) + 1
                continue
            upserted += 1
            if len(sample_upserted) < 10:
                sample_upserted.append(
                    {
                        "odbus_idx": reference.odbus_idx,
                        "business_name": reference.business_name,
                        "city": reference.city,
                        "province": reference.province,
                        "source_naics": reference.source_naics,
                        "derived_naics": reference.derived_naics,
                        "provider": reference.provider,
                    }
                )

    batch_id = str(uuid.uuid4())
    return {
        "source": "odbus",
        "csv_path": str(path),
        "filter_mode": filter_mode,
        "ingest_batch_id_planned": batch_id,
        "source_observed_at": ODBUS_EXPORT_SOURCE_OBSERVED_AT.isoformat(),
        "rows_upserted": upserted,
        "rows_skipped": skipped,
        "skip_reasons": skip_reasons,
        "sample_upserted": sample_upserted,
        "production_apply_authorized": production_apply_authorized(filter_mode),
        "destructive_delete": False,
        "supersede_previous_active_batches": True,
    }


def _upsert_batch(session: Session, values_batch: list[dict[str, Any]]) -> None:
    if not values_batch:
        return
    stmt = pg_insert(OdbusReference).values(values_batch)
    update_columns = {
        key: stmt.excluded[key]
        for key in values_batch[0].keys()
        if key != "odbus_idx"
    }
    session.execute(stmt.on_conflict_do_update(index_elements=["odbus_idx"], set_=update_columns))


def _supersede_stale_active_rows(session: Session, ingest_batch_id: str) -> int:
    result = session.execute(
        update(OdbusReference)
        .where(OdbusReference.ingest_batch_id != ingest_batch_id)
        .where(OdbusReference.observation_status == OBSERVATION_STATUS_ACTIVE)
        .values(observation_status=OBSERVATION_STATUS_SUPERSEDED)
    )
    session.commit()
    return int(result.rowcount or 0)


def import_odbus_csv(
    session: Session,
    csv_path: str | Path,
    *,
    filter_mode: str = ODBUS_FILTER_PRIMARY_NAICS23,
    ingest_batch_id: str | None = None,
    source_observed_at: date | None = None,
) -> dict[str, Any]:
    """Batch-versioned ODB import — upsert rows, supersede prior active batch."""
    path = Path(csv_path)
    if not path.is_file():
        raise FileNotFoundError(f"ODB CSV not found: {path}")
    if filter_mode not in ODBUS_FILTER_MODES:
        raise ValueError(f"Unknown filter mode: {filter_mode}")

    batch_id = ingest_batch_id or str(uuid.uuid4())
    observed_at = source_observed_at or ODBUS_EXPORT_SOURCE_OBSERVED_AT
    imported_at = datetime.now(timezone.utc)

    upserted = 0
    skipped = 0
    values_batch: list[dict[str, Any]] = []

    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            if not row_passes_odbus_filter(row, filter_mode):
                continue
            reference = _row_to_reference(row)
            if reference is None:
                skipped += 1
                continue
            values_batch.append(
                _reference_to_values(
                    reference,
                    ingest_batch_id=batch_id,
                    source_observed_at=observed_at,
                    imported_at=imported_at,
                )
            )
            if len(values_batch) >= IMPORT_BATCH_SIZE:
                _upsert_batch(session, values_batch)
                session.commit()
                upserted += len(values_batch)
                values_batch.clear()

    if values_batch:
        _upsert_batch(session, values_batch)
        session.commit()
        upserted += len(values_batch)

    superseded = _supersede_stale_active_rows(session, batch_id)

    return {
        "source": "odbus",
        "csv_path": str(path),
        "filter_mode": filter_mode,
        "ingest_batch_id": batch_id,
        "source_observed_at": observed_at.isoformat(),
        "rows_upserted": upserted,
        "rows_skipped": skipped,
        "rows_superseded": superseded,
        "destructive_delete": False,
    }


def odbus_reference_before_stats(session: Session) -> dict[str, Any]:
    from sqlalchemy import text

    has_observation_status = bool(
        session.execute(
            text(
                """
                SELECT 1
                FROM information_schema.columns
                WHERE table_name = 'odbus_reference'
                  AND column_name = 'observation_status'
                LIMIT 1
                """
            )
        ).first()
    )

    if has_observation_status:
        stats = session.execute(
            text(
                """
                SELECT
                    COUNT(*) AS row_count,
                    COUNT(*) FILTER (WHERE observation_status = 'active') AS active_count,
                    COUNT(*) FILTER (WHERE observation_status = 'superseded') AS superseded_count,
                    MIN(imported_at) AS min_imported_at,
                    MAX(imported_at) AS max_imported_at
                FROM odbus_reference
                """
            )
        ).one()
        return dict(stats._mapping)

    stats = session.execute(
        text(
            """
            SELECT
                COUNT(*) AS row_count,
                COUNT(*) AS active_count,
                0 AS superseded_count,
                MIN(imported_at) AS min_imported_at,
                MAX(imported_at) AS max_imported_at
            FROM odbus_reference
            """
        )
    ).one()
    payload = dict(stats._mapping)
    payload["migration_022_pending"] = True
    return payload
