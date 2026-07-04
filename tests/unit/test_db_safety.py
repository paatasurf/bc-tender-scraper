"""Unit tests for db/db_safety.py — no real database connections."""

from __future__ import annotations

import io
import sys
from unittest.mock import patch

import pytest

from db.db_safety import (
    PRODUCTION_CONFIRMATION,
    guard_destructive_db,
    guard_readonly_db,
    is_production_database_url,
    is_production_host,
    print_database_banner,
)


@pytest.mark.parametrize(
    "host,expected",
    [
        ("acela.proxy.rlwy.net", True),
        ("foo.proxy.rlwy.net", True),
        ("postgres.railway.internal", True),
        ("something.rlwy.net", True),
        ("localhost", False),
        ("127.0.0.1", False),
        ("postgres", False),
    ],
)
def test_production_host_detection(host: str, expected: bool) -> None:
    assert is_production_host(host) is expected


def test_production_url_detection() -> None:
    url = "postgresql://u:p@acela.proxy.rlwy.net:47306/railway"
    assert is_production_database_url(url) is True
    local = "postgresql://u:p@localhost:5432/bc_tenders"
    assert is_production_database_url(local) is False


def test_readonly_banner_on_simulated_production(capsys) -> None:
    url = "postgresql://u:p@acela.proxy.rlwy.net:47306/railway"
    print_database_banner(script_name="test_script.py", url=url, mode="READ-ONLY")
    out = capsys.readouterr().out
    assert "Environment: PRODUCTION" in out
    assert "Mode: READ-ONLY" in out
    assert "acela.proxy.rlwy.net" in out


def test_destructive_refuses_simulated_production_url() -> None:
    url = "postgresql://u:p@acela.proxy.rlwy.net:47306/railway"
    with patch.dict("os.environ", {"DATABASE_URL": url}, clear=False):
        with patch("db.db_safety.load_app_env"):
            with patch("db.db_safety.resolve_script_database_url", return_value=url):
                with pytest.raises(SystemExit) as exc:
                    guard_destructive_db(script_name="merge.py", allow_production=False)
                assert exc.value.code == 1


def test_destructive_allows_local(capsys) -> None:
    url = "postgresql://u:p@localhost:5432/bc_tenders"
    with patch("db.db_safety.load_app_env"):
        with patch("db.db_safety.resolve_script_database_url", return_value=url):
            with patch("db.db_safety.apply_script_database_url", return_value=url):
                result = guard_destructive_db(script_name="merge.py", allow_production=False)
    assert result == url
    out = capsys.readouterr().out
    assert "Environment: LOCAL" in out
    assert "Mode: DESTRUCTIVE" in out


def test_readonly_never_blocks_production(capsys) -> None:
    url = "postgresql://u:p@acela.proxy.rlwy.net:47306/railway"
    with patch("db.db_safety.apply_script_database_url", return_value=url):
        result = guard_readonly_db("probe.py")
    assert result == url
    assert "Environment: PRODUCTION" in capsys.readouterr().out


def test_allow_production_requires_confirmation_phrase(capsys) -> None:
    prod = "postgresql://u:p@acela.proxy.rlwy.net:47306/railway"
    local = "postgresql://u:p@localhost:5432/bc_tenders"
    with patch("db.db_safety.load_app_env"):
        with patch("db.db_safety.resolve_script_database_url", side_effect=[local, prod]):
            with patch("db.db_safety.apply_script_database_url", return_value=prod):
                with patch("db.db_safety._stdin_is_interactive_tty", return_value=True):
                    with patch("db.db_safety._input_is_unmocked_builtin", return_value=True):
                        with patch("builtins.input", return_value="wrong phrase"):
                            with pytest.raises(SystemExit):
                                guard_destructive_db(
                                    script_name="merge.py",
                                    allow_production=True,
                                )
    err = capsys.readouterr().err
    assert "Confirmation phrase did not match" in err


def test_allow_production_accepts_confirmation(capsys, tmp_path) -> None:
    prod = "postgresql://u:p@acela.proxy.rlwy.net:47306/railway"
    local = "postgresql://u:p@localhost:5432/bc_tenders"
    log_path = tmp_path / "destructive_operations.log"
    with patch("db.db_safety.DESTRUCTIVE_LOG_PATH", log_path):
        with patch("db.db_safety.load_app_env"):
            with patch("db.db_safety.resolve_script_database_url", side_effect=[local, prod]):
                with patch("db.db_safety.apply_script_database_url", return_value=prod):
                    with patch("db.db_safety._stdin_is_interactive_tty", return_value=True):
                        with patch("db.db_safety._input_is_unmocked_builtin", return_value=True):
                            with patch("builtins.input", return_value=PRODUCTION_CONFIRMATION):
                                result = guard_destructive_db(
                                    script_name="merge.py",
                                    allow_production=True,
                                )
    assert result == prod
    assert log_path.read_text(encoding="utf-8").startswith("")
    assert "merge.py" in log_path.read_text(encoding="utf-8")


def test_production_write_refuses_mocked_isatty_with_piped_input(capsys) -> None:
    """Agent-style bypass: mock isatty + StringIO with correct phrase must fail."""
    prod = "postgresql://u:p@acela.proxy.rlwy.net:47306/railway"
    local = "postgresql://u:p@localhost:5432/bc_tenders"
    with patch("db.db_safety.load_app_env"):
        with patch("db.db_safety.resolve_script_database_url", side_effect=[local, prod]):
            with patch("db.db_safety.apply_script_database_url", return_value=prod):
                with patch("sys.stdin", io.StringIO(PRODUCTION_CONFIRMATION + "\n")):
                    with patch("sys.stdin.isatty", return_value=True):
                        with pytest.raises(SystemExit) as exc:
                            guard_destructive_db(
                                script_name="merge.py",
                                allow_production=True,
                            )
                        assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "requires a real terminal (TTY)" in err


def test_production_write_refuses_mocked_input_function(capsys) -> None:
    """unittest.mock.patch on builtins.input must fail even if TTY checks pass."""
    prod = "postgresql://u:p@acela.proxy.rlwy.net:47306/railway"
    local = "postgresql://u:p@localhost:5432/bc_tenders"
    with patch("db.db_safety.load_app_env"):
        with patch("db.db_safety.resolve_script_database_url", side_effect=[local, prod]):
            with patch("db.db_safety.apply_script_database_url", return_value=prod):
                with patch("db.db_safety._stdin_is_interactive_tty", return_value=True):
                    with patch("builtins.input", return_value=PRODUCTION_CONFIRMATION):
                        with pytest.raises(SystemExit) as exc:
                            guard_destructive_db(
                                script_name="merge.py",
                                allow_production=True,
                            )
                        assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "mocked or patched input()" in err


def test_stdin_is_interactive_tty_rejects_stringio_with_mocked_isatty() -> None:
    from db.db_safety import _stdin_is_interactive_tty

    with patch("sys.stdin", io.StringIO("x")):
        with patch("sys.stdin.isatty", return_value=True):
            assert _stdin_is_interactive_tty() is False
