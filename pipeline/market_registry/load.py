"""Load Enterprise Seed and ODB primary observations into market_registry."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from db.market_registry_constants import (
    CONFIDENCE_A,
    CONFIDENCE_B,
    FEED_CORE_REGISTRY,
    FEED_FORCED_REGISTRY,
    MARKET_SOURCE_ENTERPRISE_SEED,
    MARKET_SOURCE_ODB_PRIMARY,
    NAME_TYPE_LEGAL,
    NAME_TYPE_UNKNOWN,
    OBSERVATION_STATUS_ACTIVE,
    OBSERVATION_STATUS_SUPERSEDED,
    PROMOTION_CORE,
)
from db.models import MarketRegistry, OdbusReference
from pipeline.company_matching import normalize_vendor_name
from pipeline.registry_verification.city_normalize import normalize_city
from pipeline.registry_verification.odbus_import import ODBUS_EXPORT_SOURCE_OBSERVED_AT

IMPORT_BATCH_SIZE = 5000

DEFAULT_SEED_PATH = (
    Path(__file__).resolve().parents[2]
    / "specs"
    / "008-canonical-company-registry"
    / "data"
    / "enterprise_registry_seed_baseline_no_db.json"
)
DEFAULT_COMPANY_ID_LOOKUP_PATH = (
    Path(__file__).resolve().parents[2]
    / "specs"
    / "008-canonical-company-registry"
    / "data"
    / "enterprise_registry_seed.json"
)


def json_fingerprint(path: Path) -> dict[str, Any]:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        "path": str(path),
        "sha256": digest,
        "size_bytes": path.stat().st_size,
    }


def _parse_seed_generated_at(payload: dict[str, Any]) -> date:
    raw = str(payload.get("generated_at") or "").strip()
    if not raw:
        return datetime.now(timezone.utc).date()
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
    except ValueError:
        return datetime.now(timezone.utc).date()


def load_company_id_lookup(path: Path | None = None) -> dict[str, int]:
    """Map seed_id → tenderscope_company_id from DB-enriched seed file."""
    lookup_path = path or DEFAULT_COMPANY_ID_LOOKUP_PATH
    if not lookup_path.is_file():
        return {}
    payload = json.loads(lookup_path.read_text(encoding="utf-8"))
    out: dict[str, int] = {}
    for record in payload.get("records") or []:
        seed_id = str(record.get("seed_id") or "").strip()
        company_id = record.get("tenderscope_company_id")
        if seed_id and isinstance(company_id, int) and company_id > 0:
            out[seed_id] = company_id
    return out


def _seed_record_to_row(
    record: dict[str, Any],
    *,
    ingest_batch_id: str,
    source_observed_at: date,
    company_id_lookup: dict[str, int],
    ingested_at: datetime,
) -> dict[str, Any] | None:
    seed_id = str(record.get("seed_id") or "").strip()
    original_name = str(record.get("canonical_company_name") or "").strip()
    if not seed_id or not original_name:
        return None

    normalized_name = normalize_vendor_name(original_name)
    if not normalized_name:
        normalized_name = f"seed_{seed_id.lower().replace('-', '_')}"

    city = str(record.get("primary_city") or "").strip()
    inclusion_rules = list(record.get("inclusion_rules") or [])
    company_id = record.get("tenderscope_company_id")
    if not isinstance(company_id, int) or company_id <= 0:
        company_id = company_id_lookup.get(seed_id)

    return {
        "source": MARKET_SOURCE_ENTERPRISE_SEED,
        "source_record_id": seed_id,
        "feed_kind": FEED_FORCED_REGISTRY,
        "promotion_status": PROMOTION_CORE,
        "source_confidence": CONFIDENCE_A,
        "original_name": original_name[:500],
        "normalized_name": normalized_name[:300],
        "name_type": NAME_TYPE_LEGAL,
        "city": city[:100],
        "normalized_city": normalize_city(city)[:100],
        "province": str(record.get("province") or "BC").strip()[:10] or "BC",
        "business_number": "",
        "licence_identifier": "",
        "website": str(record.get("website") or "").strip()[:500],
        "registry_identifiers": {
            "seed_id": seed_id,
            "inclusion_rules": inclusion_rules,
        },
        "source_metadata": {
            "market_segment": record.get("market_segment"),
            "inclusion_rationale": record.get("inclusion_rationale"),
            "sources": record.get("sources") or [],
            "award_summary": record.get("award_summary"),
        },
        "tenderscope_company_id": company_id,
        "odbus_idx": None,
        "seed_id": seed_id[:20],
        "ingest_batch_id": ingest_batch_id,
        "source_observed_at": source_observed_at,
        "observation_status": OBSERVATION_STATUS_ACTIVE,
        "ingested_at": ingested_at,
        "updated_at": ingested_at,
    }


def _odbus_to_row(reference: OdbusReference, *, ingest_batch_id: str, ingested_at: datetime) -> dict[str, Any]:
    original_name = (reference.business_name or reference.alt_business_name or "").strip()
    return {
        "source": MARKET_SOURCE_ODB_PRIMARY,
        "source_record_id": reference.odbus_idx,
        "feed_kind": FEED_CORE_REGISTRY,
        "promotion_status": PROMOTION_CORE,
        "source_confidence": CONFIDENCE_B,
        "original_name": original_name[:500],
        "normalized_name": (reference.normalized_name or "")[:300],
        "name_type": NAME_TYPE_UNKNOWN,
        "city": (reference.city or "")[:100],
        "normalized_city": (reference.normalized_city or "")[:100],
        "province": (reference.province or "BC")[:10] or "BC",
        "business_number": (reference.business_id_no or "")[:30],
        "licence_identifier": (reference.licence_number or "")[:100],
        "website": "",
        "registry_identifiers": {
            "odbus_idx": reference.odbus_idx,
            "provider": reference.provider or "",
            "source_naics": reference.source_naics or "",
        },
        "source_metadata": {
            "derived_naics": reference.derived_naics or "",
            "status": reference.status or "",
            "alt_business_name": reference.alt_business_name or "",
        },
        "tenderscope_company_id": None,
        "odbus_idx": reference.odbus_idx,
        "seed_id": "",
        "ingest_batch_id": ingest_batch_id,
        "source_observed_at": reference.source_observed_at or ODBUS_EXPORT_SOURCE_OBSERVED_AT,
        "observation_status": OBSERVATION_STATUS_ACTIVE,
        "ingested_at": ingested_at,
        "updated_at": ingested_at,
    }


def _public_row(row: dict[str, Any]) -> dict[str, Any]:
    """Shape for dry-run samples (as rows would appear in market_registry)."""
    keys = (
        "source",
        "source_record_id",
        "feed_kind",
        "promotion_status",
        "source_confidence",
        "original_name",
        "normalized_name",
        "city",
        "province",
        "licence_identifier",
        "registry_identifiers",
        "tenderscope_company_id",
        "odbus_idx",
        "seed_id",
        "source_observed_at",
        "ingest_batch_id",
    )
    return {key: row.get(key) for key in keys}


def plan_enterprise_seed_rows(
    seed_path: Path,
    *,
    company_id_lookup_path: Path | None = None,
    ingest_batch_id: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not seed_path.is_file():
        raise FileNotFoundError(f"Enterprise seed file not found: {seed_path}")

    payload = json.loads(seed_path.read_text(encoding="utf-8"))
    source_observed_at = _parse_seed_generated_at(payload)
    company_id_lookup = load_company_id_lookup(company_id_lookup_path)
    batch_id = ingest_batch_id or str(uuid.uuid4())
    ingested_at = datetime.now(timezone.utc)

    rows: list[dict[str, Any]] = []
    skipped = 0
    linked = 0
    for record in payload.get("records") or []:
        row = _seed_record_to_row(
            record,
            ingest_batch_id=batch_id,
            source_observed_at=source_observed_at,
            company_id_lookup=company_id_lookup,
            ingested_at=ingested_at,
        )
        if row is None:
            skipped += 1
            continue
        if row.get("tenderscope_company_id"):
            linked += 1
        rows.append(row)

    meta = {
        "source_file": json_fingerprint(seed_path),
        "record_count_file": int(payload.get("record_count") or len(payload.get("records") or [])),
        "rows_planned": len(rows),
        "rows_skipped": skipped,
        "tenderscope_company_id_linked": linked,
        "source_observed_at": source_observed_at.isoformat(),
    }
    return rows, meta


def plan_odbus_mirror_rows(
    session: Session,
    *,
    ingest_batch_id: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    batch_id = ingest_batch_id or str(uuid.uuid4())
    ingested_at = datetime.now(timezone.utc)

    references = session.scalars(
        select(OdbusReference)
        .where(OdbusReference.observation_status == OBSERVATION_STATUS_ACTIVE)
        .order_by(OdbusReference.odbus_idx)
    ).all()

    rows = [_odbus_to_row(ref, ingest_batch_id=batch_id, ingested_at=ingested_at) for ref in references]
    meta = {
        "odbus_reference_active_count": len(references),
        "rows_planned": len(rows),
        "source_observed_at": ODBUS_EXPORT_SOURCE_OBSERVED_AT.isoformat(),
    }
    return rows, meta


def market_registry_before_stats(session: Session) -> dict[str, Any]:
    table_exists = bool(
        session.execute(
            text(
                """
                SELECT 1 FROM information_schema.tables
                WHERE table_name = 'market_registry'
                LIMIT 1
                """
            )
        ).first()
    )
    if not table_exists:
        return {
            "row_count": 0,
            "active_count": 0,
            "superseded_count": 0,
            "by_source": {},
            "migration_022_pending": True,
        }

    stats = session.execute(
        text(
            """
            SELECT
                COUNT(*) AS row_count,
                COUNT(*) FILTER (WHERE observation_status = 'active') AS active_count,
                COUNT(*) FILTER (WHERE observation_status = 'superseded') AS superseded_count
            FROM market_registry
            """
        )
    ).one()
    by_source = {
        str(row.source): int(row.cnt)
        for row in session.execute(
            text(
                """
                SELECT source, COUNT(*) AS cnt
                FROM market_registry
                WHERE observation_status = 'active'
                GROUP BY source
                ORDER BY source
                """
            )
        )
    }
    payload = dict(stats._mapping)
    payload["by_source"] = by_source
    payload["migration_022_pending"] = False
    return payload


def plan_market_registry_load(
    session: Session,
    *,
    seed_path: Path,
    company_id_lookup_path: Path | None = None,
) -> dict[str, Any]:
    batch_id = str(uuid.uuid4())
    seed_rows, seed_meta = plan_enterprise_seed_rows(
        seed_path,
        company_id_lookup_path=company_id_lookup_path,
        ingest_batch_id=batch_id,
    )
    odb_rows, odb_meta = plan_odbus_mirror_rows(session, ingest_batch_id=batch_id)
    before = market_registry_before_stats(session)

    total = len(seed_rows) + len(odb_rows)
    return {
        "operation": "market_registry_load",
        "class": "C",
        "ingest_batch_id_planned": batch_id,
        "destructive_delete": False,
        "supersede_previous_active_batches": True,
        "rows_by_source": {
            MARKET_SOURCE_ENTERPRISE_SEED: len(seed_rows),
            MARKET_SOURCE_ODB_PRIMARY: len(odb_rows),
        },
        "rows_total_planned": total,
        "enterprise_seed": seed_meta,
        "odb_primary": odb_meta,
        "production_before": {"market_registry": before},
        "rows_superseded_estimated": int(before.get("active_count") or 0),
        "sample_enterprise_seed": [_public_row(r) for r in seed_rows[:10]],
        "sample_odb_primary": [_public_row(r) for r in odb_rows[:10]],
    }


def _upsert_batch(session: Session, values_batch: list[dict[str, Any]]) -> None:
    if not values_batch:
        return
    stmt = pg_insert(MarketRegistry).values(values_batch)
    update_columns = {
        key: stmt.excluded[key]
        for key in values_batch[0].keys()
        if key not in {"id"}
    }
    session.execute(
        stmt.on_conflict_do_update(
            index_elements=["source", "source_record_id"],
            index_where=text("observation_status = 'active'"),
            set_=update_columns,
        )
    )


def _supersede_stale_active_rows(session: Session, ingest_batch_id: str) -> int:
    result = session.execute(
        update(MarketRegistry)
        .where(MarketRegistry.ingest_batch_id != ingest_batch_id)
        .where(MarketRegistry.observation_status == OBSERVATION_STATUS_ACTIVE)
        .values(observation_status=OBSERVATION_STATUS_SUPERSEDED)
    )
    session.commit()
    return int(result.rowcount or 0)


def apply_market_registry_load(
    session: Session,
    *,
    seed_path: Path,
    company_id_lookup_path: Path | None = None,
    ingest_batch_id: str | None = None,
) -> dict[str, Any]:
    plan = plan_market_registry_load(
        session,
        seed_path=seed_path,
        company_id_lookup_path=company_id_lookup_path,
    )
    batch_id = ingest_batch_id or plan["ingest_batch_id_planned"]
    seed_rows, _ = plan_enterprise_seed_rows(
        seed_path,
        company_id_lookup_path=company_id_lookup_path,
        ingest_batch_id=batch_id,
    )
    odb_rows, _ = plan_odbus_mirror_rows(session, ingest_batch_id=batch_id)
    all_rows = seed_rows + odb_rows

    upserted = 0
    batch: list[dict[str, Any]] = []
    for row in all_rows:
        batch.append(row)
        if len(batch) >= IMPORT_BATCH_SIZE:
            _upsert_batch(session, batch)
            session.commit()
            upserted += len(batch)
            batch.clear()

    if batch:
        _upsert_batch(session, batch)
        session.commit()
        upserted += len(batch)

    superseded = _supersede_stale_active_rows(session, batch_id)
    after = market_registry_before_stats(session)

    return {
        "ingest_batch_id": batch_id,
        "rows_upserted": upserted,
        "rows_by_source": plan["rows_by_source"],
        "rows_superseded": superseded,
        "destructive_delete": False,
        "production_after": {"market_registry": after},
    }
