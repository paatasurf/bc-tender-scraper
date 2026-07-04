"""Unit tests for market_registry load planning (no DB)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from db.market_registry_constants import (
    CONFIDENCE_A,
    CONFIDENCE_B,
    FEED_CORE_REGISTRY,
    FEED_FORCED_REGISTRY,
    MARKET_SOURCE_ENTERPRISE_SEED,
    MARKET_SOURCE_ODB_PRIMARY,
    PROMOTION_CORE,
)
from pipeline.market_registry.load import (
    _odbus_to_row,
    _seed_record_to_row,
    plan_enterprise_seed_rows,
    plan_odbus_mirror_rows,
)

ROOT = Path(__file__).resolve().parents[2]
SEED_PATH = ROOT / "specs" / "008-canonical-company-registry" / "data" / "enterprise_registry_seed_baseline_no_db.json"


def test_seed_record_mapping_fields() -> None:
    row = _seed_record_to_row(
        {
            "seed_id": "ES-0001",
            "canonical_company_name": "WSP Canada Inc.",
            "province": "BC",
            "primary_city": "Vancouver",
            "inclusion_rules": ["major_public_contract_awards"],
            "market_segment": "enterprise",
        },
        ingest_batch_id="batch-1",
        source_observed_at=__import__("datetime").date(2026, 7, 4),
        company_id_lookup={"ES-0001": 548728},
        ingested_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
    )
    assert row is not None
    assert row["source"] == MARKET_SOURCE_ENTERPRISE_SEED
    assert row["source_record_id"] == "ES-0001"
    assert row["feed_kind"] == FEED_FORCED_REGISTRY
    assert row["promotion_status"] == PROMOTION_CORE
    assert row["source_confidence"] == CONFIDENCE_A
    assert row["registry_identifiers"]["seed_id"] == "ES-0001"
    assert row["tenderscope_company_id"] == 548728


def test_odbus_row_mapping_fields() -> None:
    ref = MagicMock()
    ref.odbus_idx = "abc123"
    ref.business_name = "JETSTREAM PLUMBING INC"
    ref.alt_business_name = ""
    ref.normalized_name = "jetstream plumbing"
    ref.city = "Nanaimo"
    ref.normalized_city = "nanaimo"
    ref.province = "BC"
    ref.business_id_no = "BN123"
    ref.licence_number = "129203"
    ref.provider = "City of Nanaimo"
    ref.source_naics = "23"
    ref.derived_naics = "23"
    ref.status = "active"
    ref.source_observed_at = __import__("datetime").date(2023, 11, 28)

    row = _odbus_to_row(
        ref,
        ingest_batch_id="batch-1",
        ingested_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
    )
    assert row["source"] == MARKET_SOURCE_ODB_PRIMARY
    assert row["feed_kind"] == FEED_CORE_REGISTRY
    assert row["source_confidence"] == CONFIDENCE_B
    assert row["registry_identifiers"]["odbus_idx"] == "abc123"
    assert row["source_observed_at"].isoformat() == "2023-11-28"


@pytest.mark.skipif(not SEED_PATH.is_file(), reason="seed fixture missing")
def test_plan_enterprise_seed_row_count() -> None:
    rows, meta = plan_enterprise_seed_rows(SEED_PATH)
    assert meta["record_count_file"] == 156
    assert len(rows) == 156
    assert meta["rows_skipped"] == 0


def test_plan_odbus_mirror_from_session() -> None:
    ref = MagicMock()
    ref.odbus_idx = "x1"
    ref.business_name = "Test Co"
    ref.alt_business_name = ""
    ref.normalized_name = "test co"
    ref.city = "Vancouver"
    ref.normalized_city = "vancouver"
    ref.province = "BC"
    ref.business_id_no = ""
    ref.licence_number = ""
    ref.provider = "City of Vancouver"
    ref.source_naics = "23"
    ref.derived_naics = "23"
    ref.status = "active"
    ref.source_observed_at = __import__("datetime").date(2023, 11, 28)

    session = MagicMock()
    session.scalars.return_value.all.return_value = [ref]
    rows, meta = plan_odbus_mirror_rows(session)
    assert len(rows) == 1
    assert meta["rows_planned"] == 1
