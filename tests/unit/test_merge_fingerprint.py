"""Tests for strengthened dataset fingerprint."""

from __future__ import annotations

import hashlib
import json
from unittest.mock import MagicMock

from db.merge_dry_run_provenance import (
    compute_dataset_fingerprint,
    get_schema_migration_version,
)


def test_schema_migration_version_is_stable() -> None:
    v1 = get_schema_migration_version()
    v2 = get_schema_migration_version()
    assert v1 == v2
    assert len(v1) == 16


def test_fingerprint_changes_when_identity_differs() -> None:
    session_a = MagicMock()
    session_b = MagicMock()

    def make_execute(rows, counts):
        def execute(sql, *_args, **_kwargs):
            stmt = str(sql)
            result = MagicMock()
            if "COUNT(*)" in stmt:
                table = stmt.split("FROM")[1].strip().split()[0]
                result.scalar_one.return_value = counts.get(table, 0)
            elif "MAX(" in stmt:
                result.scalar_one.return_value = None
            else:
                result.all.return_value = rows
            return result

        return execute

    session_a.execute.side_effect = make_execute(
        [(1, "dba_name"), (2, "")],
        {
            "companies": 2,
            "permits": 10,
            "company_applicant_aliases": 0,
            "company_canonical_merge_runs": 0,
        },
    )
    session_b.execute.side_effect = make_execute(
        [(1, "dba_name"), (2, "legal_applicant")],
        {
            "companies": 2,
            "permits": 10,
            "company_applicant_aliases": 0,
            "company_canonical_merge_runs": 0,
        },
    )

    fp_a = compute_dataset_fingerprint(session_a)
    fp_b = compute_dataset_fingerprint(session_b)
    assert fp_a != fp_b


def test_fingerprint_payload_includes_schema_version() -> None:
    from db.merge_dry_run_provenance import _build_fingerprint_payload

    session = MagicMock()

    def execute(sql, *_args, **_kwargs):
        stmt = str(sql)
        result = MagicMock()
        if "COUNT(*)" in stmt:
            result.scalar_one.return_value = 0
        elif "MAX(" in stmt:
            result.scalar_one.return_value = None
        else:
            result.all.return_value = []
        return result

    session.execute.side_effect = execute
    payload = _build_fingerprint_payload(session)
    assert "schema_migration_version" in payload
    assert "identity_checksum" in payload
    assert "max_updated_at" in payload
    assert payload["schema_migration_version"] == get_schema_migration_version()
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    assert compute_dataset_fingerprint(session) == hashlib.sha256(canonical.encode()).hexdigest()[:16]
