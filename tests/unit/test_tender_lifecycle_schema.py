"""Unit tests for P2-01 tender lifecycle schema foundation."""

from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine, inspect

from db.lifecycle_constants import LIFECYCLE_IMPORT_SKIP_COLUMNS, LIFECYCLE_STATUSES
from db.models import ArchTender, CommercialTender, Tender
from db.tender_lifecycle_columns import TenderLifecycleColumnsMixin
from db.tender_lifecycle_ddl import TENDER_LIFECYCLE_COLUMN_DEFS, TENDER_LIFECYCLE_TABLES
from db.tender_presence import PRESENCE_SKIP_ON_UPDATE


@pytest.mark.parametrize(
    "model",
    [Tender, CommercialTender, ArchTender],
)
def test_tender_models_include_lifecycle_columns(model):
    column_names = {column.name for column in model.__table__.columns}
    mixin_names = {column.name for column in TenderLifecycleColumnsMixin.__table__.columns} if hasattr(
        TenderLifecycleColumnsMixin, "__table__"
    ) else {
        "lifecycle_status",
        "is_open",
        "lifecycle_status_override",
        "lifecycle_override_reason",
        "lifecycle_override_by",
        "closing_at",
        "closed_at",
        "awarded_at",
        "cancelled_at",
        "archived_at",
        "missing_from_source_count",
        "source_status_raw",
        "source_status_normalized",
        "award_id",
        "award_match_confidence",
        "addenda_count",
        "last_addendum_at",
    }
    assert mixin_names.issubset(column_names)


def test_lifecycle_import_skip_columns_protected_from_csv_upsert():
    assert LIFECYCLE_IMPORT_SKIP_COLUMNS.issubset(PRESENCE_SKIP_ON_UPDATE)


def test_lifecycle_status_vocabulary_includes_outcome_unknown():
    assert "outcome_unknown" in LIFECYCLE_STATUSES
    assert "active" in LIFECYCLE_STATUSES
    assert "delisted" in LIFECYCLE_STATUSES


def test_ddl_targets_all_tender_tables():
    assert set(TENDER_LIFECYCLE_TABLES) == {"tenders", "commercial_tenders", "arch_tenders"}
    assert len(TENDER_LIFECYCLE_COLUMN_DEFS) == 17


@pytest.fixture(scope="module")
def migrated_engine():
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        pytest.skip("DATABASE_URL not configured")
    return create_engine(database_url)


def test_lifecycle_columns_exist_after_init_db(migrated_engine):
    inspector = inspect(migrated_engine)
    expected = {
        "lifecycle_status",
        "is_open",
        "lifecycle_status_override",
        "lifecycle_override_reason",
        "lifecycle_override_by",
        "closing_at",
        "closed_at",
        "awarded_at",
        "cancelled_at",
        "archived_at",
        "missing_from_source_count",
        "source_status_raw",
        "source_status_normalized",
        "award_id",
        "award_match_confidence",
        "addenda_count",
        "last_addendum_at",
        "first_seen_at",
        "last_seen_at",
        "updated_at",
    }
    for table in TENDER_LIFECYCLE_TABLES:
        names = {col["name"] for col in inspector.get_columns(table)}
        missing = expected - names
        assert not missing, f"{table} missing columns: {missing}"
