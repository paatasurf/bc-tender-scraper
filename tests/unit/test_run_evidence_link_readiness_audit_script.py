"""DB routing tests for scripts/run_evidence_link_readiness_audit.py — no real DB.

Stage 2A stays Class A (read-only). These tests prove the standard
db_safety.py read-only guard runs before any session is opened, that the
guard's banner is redirected to stderr so stdout stays pure JSON for callers
piping this script's output, and that the script never imports init_db (no
schema writes, no escalation to Class D).
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from db.db_safety import guard_readonly_db


def test_stdout_is_pure_json_banner_on_stderr_guard_before_session(
    monkeypatch, capsys
) -> None:
    """Contract: stdout is exactly the JSON report (safe for `json.loads` /
    `| jq` by callers). The db_safety banner moves to stderr — locally, in
    this script's main() only; db_safety.print_database_banner itself is
    untouched and still prints to whatever sys.stdout is at call time (see
    test_db_safety.py, which asserts the banner on stdout for other callers)."""
    local = "postgresql://u:p@localhost:5432/bc_tenders"
    monkeypatch.setenv("DATABASE_URL", local)

    import scripts.run_evidence_link_readiness_audit as audit_mod

    call_order: list[str] = []
    session = MagicMock()

    def _factory():
        call_order.append("get_session_factory")
        return lambda: session

    real_guard = audit_mod.guard_readonly_db

    def _guard_and_track(*args, **kwargs):
        call_order.append("guard")
        return real_guard(*args, **kwargs)

    with patch.object(audit_mod, "guard_readonly_db", side_effect=_guard_and_track):
        with patch.object(audit_mod, "get_session_factory", side_effect=_factory):
            with patch.object(
                audit_mod, "audit_permit_evidence_links", return_value=MagicMock()
            ):
                with patch.object(
                    audit_mod,
                    "audit_contract_award_evidence_links",
                    return_value=MagicMock(),
                ):
                    with patch.object(
                        audit_mod,
                        "audit_tender_evidence_linkage",
                        return_value=MagicMock(),
                    ):
                        with patch.object(
                            audit_mod, "asdict", side_effect=lambda x: {}
                        ):
                            audit_mod.main()

    out, err = capsys.readouterr()

    # stdout: exactly the JSON report, nothing else.
    parsed = json.loads(out)
    assert parsed == {"permits": {}, "contract_awards": {}, "tenders": {}}
    assert "Target Database" not in out
    assert "Environment:" not in out

    # stderr: the masked banner, no credentials.
    assert "Environment: LOCAL" in err
    assert "Host: localhost" in err
    assert "Mode: READ-ONLY" in err
    assert "u:p" not in err

    # guard runs before the session is created.
    assert call_order == ["guard", "get_session_factory"]
    session.close.assert_called_once()


def test_module_does_not_import_init_db() -> None:
    import scripts.run_evidence_link_readiness_audit as audit_mod

    assert "init_db" not in audit_mod.__dict__


def test_guard_readonly_db_is_class_a_and_never_blocks_production() -> None:
    """Sanity check on the guard itself: Class A is read-only and must not
    refuse a production DATABASE_URL (it only labels the banner)."""
    prod = "postgresql://u:p@acela.proxy.rlwy.net:47306/railway"
    with patch("db.db_safety.apply_script_database_url", return_value=prod):
        result = guard_readonly_db("run_evidence_link_readiness_audit.py")
    assert result == prod
