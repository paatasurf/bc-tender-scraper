from __future__ import annotations

import importlib.util
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "run_data_coverage_audit.py"


def _load_script():
    spec = importlib.util.spec_from_file_location(
        "run_data_coverage_audit_test", SCRIPT
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_script_has_no_apply_or_allow_production_flags() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert '"--apply"' not in source
    assert '"--allow-production"' not in source
    assert "guard_destructive_db" not in source
    assert "guard_readonly_db_from_args" in source


def test_read_only_statement_precedes_audit_call() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert source.index("SET TRANSACTION ISOLATION LEVEL") < source.index(
        "report = audit_data_coverage"
    )
    assert "trans.rollback()" in source


@pytest.mark.parametrize("value", ["2026-07-20", "not-a-date"])
def test_as_of_rejects_naive_or_invalid_values(value: str) -> None:
    module = _load_script()
    with pytest.raises(module.DataCoverageAuditError):
        module._resolve_as_of(value)


def test_as_of_normalizes_to_utc() -> None:
    module = _load_script()
    parsed = module._resolve_as_of("2026-07-20T12:00:00-07:00")
    assert parsed == datetime(2026, 7, 20, 19, 0, tzinfo=timezone.utc)
