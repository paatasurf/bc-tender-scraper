"""Unit tests for permit lifecycle schema foundation."""

from __future__ import annotations

from db.models import Permit
from db.permit_lifecycle_constants import (
    PERMIT_LIFECYCLE_IMPORT_SKIP_COLUMNS,
    PERMIT_LIFECYCLE_STATUSES,
)


def test_permit_model_includes_lifecycle_columns():
    column_names = {column.name for column in Permit.__table__.columns}
    expected = {
        "lifecycle_status",
        "lifecycle_status_override",
        "status_changed_at",
        "is_active",
        "source_status_raw",
    }
    assert expected.issubset(column_names)


def test_permit_lifecycle_status_vocabulary():
    assert "active" in PERMIT_LIFECYCLE_STATUSES
    assert "completed" in PERMIT_LIFECYCLE_STATUSES
    assert "cancelled" in PERMIT_LIFECYCLE_STATUSES
    assert "stale" in PERMIT_LIFECYCLE_STATUSES
    assert "unknown" in PERMIT_LIFECYCLE_STATUSES


def test_permit_lifecycle_import_skip_columns():
    assert "lifecycle_status" in PERMIT_LIFECYCLE_IMPORT_SKIP_COLUMNS
    assert "is_active" in PERMIT_LIFECYCLE_IMPORT_SKIP_COLUMNS
