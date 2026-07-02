"""Unit tests for permit import helpers."""

from __future__ import annotations

from db.permit_import import _clamp_permit_row, _dedupe_permit_rows


def test_dedupe_permit_rows_keeps_last_duplicate():
    rows = [
        {"source": "surrey", "external_id": "A", "address": "1 Main"},
        {"source": "surrey", "external_id": "A", "address": "1 Main Updated"},
        {"source": "surrey", "external_id": "B", "address": "2 Main"},
    ]
    deduped = _dedupe_permit_rows(rows)
    assert len(deduped) == 2
    by_id = {row["external_id"]: row for row in deduped}
    assert by_id["A"]["address"] == "1 Main Updated"


def test_clamp_permit_row_truncates_varchar_fields():
    row = {
        "source": "vancouver",
        "external_id": "BP-1",
        "address": "x" * 350,
        "contractor": "y" * 400,
        "description": "z" * 500,
    }
    clamped = _clamp_permit_row(row)
    assert len(clamped["address"]) == 300
    assert len(clamped["contractor"]) == 300
    assert len(clamped["description"]) == 500
