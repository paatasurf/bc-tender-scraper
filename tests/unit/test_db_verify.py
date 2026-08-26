"""Pure unit tests for pipeline/db_verify.py's Stage 2 ``skip_import_check``
parameter. No real DB -- count_table_rows() is monkeypatched to return
controlled totals.
"""

from __future__ import annotations

import pytest

import pipeline.db_verify as db_verify_module
from pipeline.db_verify import DbVerificationError, verify_database_counts


def _patch_counts(monkeypatch: pytest.MonkeyPatch, counts: dict) -> None:
    monkeypatch.setattr(db_verify_module, "count_table_rows", lambda session: counts)


def test_skip_import_check_tolerates_zero_import_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_counts(
        monkeypatch, {"tenders": 5, "commercial_tenders": 2, "arch_tenders": 1}
    )

    result = verify_database_counts(
        session=object(),
        import_counts={"tenders": 0, "commercial_tenders": 1, "arch_tenders": 1},
        skip_import_check=frozenset({"tenders"}),
    )

    assert result["import_batch_tenders"] == 0


def test_skip_import_check_does_not_weaken_total_zero_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Proof: skip_import_check only exempts the "did we import rows
    this run" check -- a genuinely empty table must still raise even for
    a skipped key."""
    _patch_counts(
        monkeypatch, {"tenders": 0, "commercial_tenders": 2, "arch_tenders": 1}
    )

    with pytest.raises(DbVerificationError, match="tenders: database total is 0"):
        verify_database_counts(
            session=object(),
            import_counts={"tenders": 0, "commercial_tenders": 1, "arch_tenders": 1},
            skip_import_check=frozenset({"tenders"}),
        )


def test_skip_import_check_does_not_weaken_decreased_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Proof: a skipped key whose database count genuinely decreased vs.
    previous_counts must still raise -- skip never exempts the
    count-never-decreased integrity check."""
    _patch_counts(
        monkeypatch, {"tenders": 3, "commercial_tenders": 2, "arch_tenders": 1}
    )

    with pytest.raises(DbVerificationError, match="tenders: database count decreased"):
        verify_database_counts(
            session=object(),
            import_counts={"tenders": 0, "commercial_tenders": 1, "arch_tenders": 1},
            previous_counts={"tenders": 5, "commercial_tenders": 2, "arch_tenders": 1},
            skip_import_check=frozenset({"tenders"}),
        )


def test_default_skip_import_check_matches_pre_stage2_behavior(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No caller-provided skip_import_check -- reproduces the exact
    pre-Stage-2 "every table must have gotten rows this run" behavior."""
    _patch_counts(
        monkeypatch, {"tenders": 5, "commercial_tenders": 2, "arch_tenders": 1}
    )

    with pytest.raises(DbVerificationError, match="tenders: import batch reported 0"):
        verify_database_counts(
            session=object(),
            import_counts={"tenders": 0, "commercial_tenders": 1, "arch_tenders": 1},
        )
