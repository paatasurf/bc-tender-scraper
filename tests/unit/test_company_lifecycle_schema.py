"""Unit tests for company lifecycle schema foundation."""

from __future__ import annotations

from db.company_lifecycle_constants import (
    COMPANY_LIFECYCLE_IMPORT_SKIP_COLUMNS,
    COMPANY_LIFECYCLE_STATUSES,
)
from db.models import Company


def test_company_model_includes_lifecycle_columns():
    column_names = {column.name for column in Company.__table__.columns}
    expected = {
        "lifecycle_status",
        "lifecycle_status_override",
        "last_activity_at",
        "status_changed_at",
        "is_operating",
    }
    assert expected.issubset(column_names)


def test_company_lifecycle_status_vocabulary():
    assert "active" in COMPANY_LIFECYCLE_STATUSES
    assert "quiet" in COMPANY_LIFECYCLE_STATUSES
    assert "dormant" in COMPANY_LIFECYCLE_STATUSES
    assert "no_observable_activity" in COMPANY_LIFECYCLE_STATUSES


def test_company_lifecycle_import_skip_columns():
    assert "lifecycle_status" in COMPANY_LIFECYCLE_IMPORT_SKIP_COLUMNS
    assert "is_operating" in COMPANY_LIFECYCLE_IMPORT_SKIP_COLUMNS
    assert "last_activity_at" in COMPANY_LIFECYCLE_IMPORT_SKIP_COLUMNS
