"""Import BC OrgBook reference data into orgbook_reference."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from sqlalchemy import delete
from sqlalchemy.orm import Session

from db.models import OrgbookReference
from db.registry_constants import REGISTRY_SOURCE_ORGBOOK
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


def _parse_dba_names(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = _clean_text(value)
    if not text:
        return []
    if text.startswith("["):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return [str(item).strip() for item in parsed if str(item).strip()]
        except json.JSONDecodeError:
            pass
    return [part.strip() for part in text.split("|") if part.strip()]


def _normalize_record_name(legal_name: str, dba_names: list[str]) -> str:
    for candidate in [legal_name, *dba_names]:
        normalized = normalize_vendor_name(candidate)
        if normalized:
            return normalized
    return ""


def _record_from_mapping(row: dict[str, Any]) -> OrgbookReference | None:
    orgbook_id = _clean_text(row.get("orgbook_id") or row.get("topic_id") or row.get("id"))
    if not orgbook_id:
        return None

    legal_name = _clean_text(row.get("legal_name") or row.get("name"))
    dba_names = _parse_dba_names(row.get("dba_names") or row.get("dba_name"))
    normalized_name = _normalize_record_name(legal_name, dba_names)
    if not normalized_name:
        return None

    city = _clean_text(row.get("city"))
    province = _clean_text(row.get("province") or "BC").upper() or "BC"
    business_number = _clean_text(row.get("business_number") or row.get("cra_business_number"))
    registry_id = _clean_text(row.get("registry_id") or row.get("bc_registries_id"))
    entity_type = _clean_text(row.get("entity_type"))
    status = _clean_text(row.get("status") or row.get("registration_status"))

    metadata = {
        key: value
        for key, value in row.items()
        if key
        not in {
            "orgbook_id",
            "topic_id",
            "id",
            "legal_name",
            "name",
            "dba_names",
            "dba_name",
            "city",
            "province",
            "business_number",
            "cra_business_number",
            "registry_id",
            "bc_registries_id",
            "entity_type",
            "status",
            "registration_status",
        }
        and value not in (None, "")
    }

    return OrgbookReference(
        orgbook_id=orgbook_id,
        legal_name=legal_name,
        dba_names=dba_names,
        normalized_name=normalized_name,
        business_number=business_number,
        registry_id=registry_id,
        entity_type=entity_type,
        status=status,
        city=city,
        normalized_city=normalize_city(city),
        province=province,
        metadata_json=metadata,
    )


def import_orgbook_jsonl(session: Session, jsonl_path: str | Path) -> dict[str, Any]:
    """Replace orgbook_reference contents from a JSONL export."""
    path = Path(jsonl_path)
    if not path.is_file():
        raise FileNotFoundError(f"OrgBook JSONL not found: {path}")

    session.execute(delete(OrgbookReference))

    inserted = 0
    skipped = 0
    batch: list[OrgbookReference] = []

    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                skipped += 1
                continue
            if not isinstance(row, dict):
                skipped += 1
                continue
            reference = _record_from_mapping(row)
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
        "source": REGISTRY_SOURCE_ORGBOOK,
        "path": str(path),
        "format": "jsonl",
        "rows_inserted": inserted,
        "rows_skipped": skipped,
    }


def import_orgbook_csv(session: Session, csv_path: str | Path) -> dict[str, Any]:
    """Replace orgbook_reference contents from a CSV export."""
    path = Path(csv_path)
    if not path.is_file():
        raise FileNotFoundError(f"OrgBook CSV not found: {path}")

    session.execute(delete(OrgbookReference))

    inserted = 0
    skipped = 0
    batch: list[OrgbookReference] = []

    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            reference = _record_from_mapping(row)
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
        "source": REGISTRY_SOURCE_ORGBOOK,
        "path": str(path),
        "format": "csv",
        "rows_inserted": inserted,
        "rows_skipped": skipped,
    }


def import_orgbook_reference(session: Session, path: str | Path) -> dict[str, Any]:
    """Import OrgBook reference data from CSV or JSONL."""
    file_path = Path(path)
    suffix = file_path.suffix.lower()
    if suffix == ".jsonl":
        return import_orgbook_jsonl(session, file_path)
    return import_orgbook_csv(session, file_path)
