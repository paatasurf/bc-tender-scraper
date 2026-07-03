"""Import Statistics Canada ODB CSV into odbus_reference."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from sqlalchemy import delete
from sqlalchemy.orm import Session

from db.models import OdbusReference
from pipeline.company_matching import normalize_vendor_name
from pipeline.registry_verification.city_normalize import normalize_city

IMPORT_BATCH_SIZE = 5000
MISSING_MARKERS = frozenset({"..", "nan", "none", ""})


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

    return OdbusReference(
        odbus_idx=idx,
        business_name=business_name,
        alt_business_name=alt_name,
        normalized_name=normalized_name,
        city=city,
        normalized_city=normalize_city(city),
        province=province,
        status=_clean_text(row.get("status")),
        derived_naics=derived_naics,
        source_naics=source_naics,
        licence_number=_clean_text(row.get("licence_number")),
        business_id_no=_clean_text(row.get("business_id_no")),
        provider=_clean_text(row.get("provider")),
        latitude=_parse_float(row.get("latitude")),
        longitude=_parse_float(row.get("longitude")),
    )


def import_odbus_csv(session: Session, csv_path: str | Path) -> dict[str, Any]:
    """Replace odbus_reference contents from an ODB CSV export."""
    path = Path(csv_path)
    if not path.is_file():
        raise FileNotFoundError(f"ODB CSV not found: {path}")

    session.execute(delete(OdbusReference))

    inserted = 0
    skipped = 0
    batch: list[OdbusReference] = []

    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            reference = _row_to_reference(row)
            if reference is None:
                skipped += 1
                continue
            batch.append(reference)
            if len(batch) >= IMPORT_BATCH_SIZE:
                session.add_all(batch)
                session.commit()
                inserted += len(batch)
                batch.clear()

    if batch:
        session.add_all(batch)
        session.commit()
        inserted += len(batch)

    return {
        "source": "odbus",
        "csv_path": str(path),
        "rows_inserted": inserted,
        "rows_skipped": skipped,
    }
