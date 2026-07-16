"""DB routing tests for scripts/run_derived_tender_evidence_audit.py — no real DB.

Class A (read-only). These tests prove the standard db_safety.py read-only
guard runs before any session is opened, that the guard's banner is
redirected to stderr so stdout stays pure JSON, and that the script never
imports init_db (no schema writes, no escalation to Class D).
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from db.db_safety import guard_readonly_db


def test_stdout_is_pure_json_banner_on_stderr_guard_before_session(
    monkeypatch, capsys
) -> None:
    local = "postgresql://u:p@localhost:5432/bc_tenders"
    monkeypatch.setenv("DATABASE_URL", local)

    import scripts.run_derived_tender_evidence_audit as script_mod

    call_order: list[str] = []
    session = MagicMock()

    def _factory():
        call_order.append("get_session_factory")
        return lambda: session

    real_guard = script_mod.guard_readonly_db

    def _guard_and_track(*args, **kwargs):
        call_order.append("guard")
        return real_guard(*args, **kwargs)

    fake_report = MagicMock()
    fake_report.path_a = MagicMock()
    fake_report.path_b = MagicMock()
    fake_report.cross_path = MagicMock()
    fake_report.schema_version = 1

    with patch.object(script_mod, "guard_readonly_db", side_effect=_guard_and_track):
        with patch.object(script_mod, "get_session_factory", side_effect=_factory):
            with patch.object(
                script_mod,
                "run_derived_tender_evidence_audit",
                return_value=fake_report,
            ):
                with patch.object(script_mod, "asdict", side_effect=lambda x: {}):
                    script_mod.main()

    out, err = capsys.readouterr()

    parsed = json.loads(out)
    assert parsed == {
        "path_a": {},
        "path_b": {},
        "cross_path": {},
        "schema_version": 1,
    }
    assert "Target Database" not in out
    assert "Environment:" not in out

    assert "Environment: LOCAL" in err
    assert "Host: localhost" in err
    assert "Mode: READ-ONLY" in err
    assert "u:p" not in err

    assert call_order == ["guard", "get_session_factory"]
    session.close.assert_called_once()


def test_module_does_not_import_init_db() -> None:
    import scripts.run_derived_tender_evidence_audit as script_mod

    assert "init_db" not in script_mod.__dict__


def test_guard_readonly_db_is_class_a_and_never_blocks_production() -> None:
    prod = "postgresql://u:p@acela.proxy.rlwy.net:47306/railway"
    with patch("db.db_safety.apply_script_database_url", return_value=prod):
        result = guard_readonly_db("run_derived_tender_evidence_audit.py")
    assert result == prod
