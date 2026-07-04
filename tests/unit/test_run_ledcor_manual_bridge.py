"""DB routing tests for scripts/run_ledcor_manual_bridge.py — no real DB."""

from __future__ import annotations

import json
import sys
from unittest.mock import MagicMock, patch

import pytest

from db.classification import SafetyClass
from db.connection import clear_engine_cache, get_engine
from db.db_safety import PRODUCTION_CONFIRMATION


def test_run_ledcor_apply_uses_guard_production_url_not_init_db(tmp_path, monkeypatch) -> None:
    """After Class C confirmation, get_session must bind to guard-resolved production URL."""
    prod = "postgresql://u:p@acela.proxy.rlwy.net:47306/railway"
    local = "postgresql://u:p@localhost:5432/bc_tenders"
    monkeypatch.setenv("DATABASE_URL", local)
    monkeypatch.setenv("DATABASE_URL_PRODUCTION", prod)

    artifact = tmp_path / "ledcor_manual_bridge_dryrun_class_c.json"
    artifact.write_text(
        json.dumps({"plan": {}, "dry_run_provenance": {"dataset_fingerprint": "testfp"}}),
        encoding="utf-8",
    )

    import scripts.run_ledcor_manual_bridge as ledcor_mod

    monkeypatch.setattr(ledcor_mod, "DEFAULT_DRY_RUN_ARTIFACT", artifact)
    monkeypatch.setattr(sys, "argv", ["run_ledcor_manual_bridge.py", "--apply", "--allow-production"])

    call_order: list[str] = []
    session = MagicMock()

    def _get_session_after_guard() -> MagicMock:
        call_order.append("get_session")
        host = get_engine().url.host or ""
        assert "rlwy.net" in host, f"expected production host after guard, got {host!r}"
        return session

    plan = MagicMock()
    plan.validation_errors = []
    apply_result = {
        "merge_run_id": 42,
        "status": "applied",
        "fk_remap": {"updated": 1},
        "aggregate_recompute": {},
    }

    log_path = tmp_path / "destructive_operations.log"
    with patch("db.db_safety.DESTRUCTIVE_LOG_PATH", log_path):
        with patch("db.connection.init_db") as init_db_mock:
            with patch.object(ledcor_mod, "get_session", side_effect=_get_session_after_guard) as get_session_mock:
                with patch.object(ledcor_mod, "verify_dry_run_artifact"):
                    with patch.object(ledcor_mod, "build_ledcor_manual_bridge_plan", return_value=plan):
                        with patch.object(
                            ledcor_mod,
                            "apply_ledcor_manual_bridge_plan",
                            return_value=apply_result,
                        ):
                            with patch("db.db_safety._stdin_is_interactive_tty", return_value=True):
                                with patch("db.db_safety._input_is_unmocked_builtin", return_value=True):
                                    with patch("builtins.input", return_value=PRODUCTION_CONFIRMATION):
                                        real_guard = ledcor_mod.guard_destructive_db_from_args

                                        def _guard_and_track(*args, **kwargs):
                                            call_order.append("guard")
                                            return real_guard(*args, **kwargs)

                                        with patch.object(
                                            ledcor_mod,
                                            "guard_destructive_db_from_args",
                                            side_effect=_guard_and_track,
                                        ):
                                            ledcor_mod.main()

    assert call_order == ["guard", "get_session"]
    init_db_mock.assert_not_called()
    get_session_mock.assert_called_once()
    clear_engine_cache()


def test_run_ledcor_dry_run_use_production_routes_before_session(tmp_path, monkeypatch) -> None:
    """--dry-run --use-production must apply production URL before get_session (no init_db)."""
    prod = "postgresql://u:p@acela.proxy.rlwy.net:47306/railway"
    local = "postgresql://u:p@localhost:5432/bc_tenders"
    monkeypatch.setenv("DATABASE_URL", local)
    monkeypatch.setenv("DATABASE_URL_PRODUCTION", prod)

    import scripts.run_ledcor_manual_bridge as ledcor_mod

    monkeypatch.setattr(
        ledcor_mod,
        "DEFAULT_DRY_RUN_ARTIFACT",
        tmp_path / "ledcor_manual_bridge_dryrun_class_c.json",
    )
    monkeypatch.setattr(sys, "argv", ["run_ledcor_manual_bridge.py", "--dry-run", "--use-production"])

    call_order: list[str] = []
    session = MagicMock()

    def _get_session_after_guard() -> MagicMock:
        call_order.append("get_session")
        host = get_engine().url.host or ""
        assert "rlwy.net" in host
        return session

    report = {"operation": "ledcor_manual_bridge", "dry_run_provenance": {}}

    with patch("db.connection.init_db") as init_db_mock:
        with patch.object(ledcor_mod, "get_session", side_effect=_get_session_after_guard):
            with patch.object(ledcor_mod, "_build_dry_run_report", return_value=report):
                with patch.object(ledcor_mod, "attach_dry_run_provenance", return_value=report):
                    real_guard = ledcor_mod.guard_readonly_db_from_args

                    def _guard_and_track(*args, **kwargs):
                        call_order.append("guard")
                        return real_guard(*args, **kwargs)

                    with patch.object(
                        ledcor_mod,
                        "guard_readonly_db_from_args",
                        side_effect=_guard_and_track,
                    ):
                        ledcor_mod.main()

    assert call_order == ["guard", "get_session"]
    init_db_mock.assert_not_called()
    clear_engine_cache()


def test_run_ledcor_module_does_not_import_init_db() -> None:
    import scripts.run_ledcor_manual_bridge as ledcor_mod

    assert "init_db" not in ledcor_mod.__dict__
