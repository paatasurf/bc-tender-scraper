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


def test_allow_production_requires_confirmation_phrase() -> None:
    prod = "postgresql://u:p@acela.proxy.rlwy.net:47306/railway"
    local = "postgresql://u:p@localhost:5432/bc_tenders"
    with patch("db.db_safety.load_app_env"):
        with patch("db.db_safety.resolve_script_database_url", side_effect=[local, prod]):
            with patch("db.db_safety.apply_script_database_url", return_value=prod):
                with patch("sys.stdin", io.StringIO("wrong phrase\n")):
                    with patch("sys.stdin.isatty", return_value=True):
                        with pytest.raises(SystemExit):
                            guard_destructive_db(
                                script_name="merge.py",
                                allow_production=True,
                            )


def test_allow_production_accepts_confirmation(capsys, tmp_path) -> None:
    prod = "postgresql://u:p@acela.proxy.rlwy.net:47306/railway"
    local = "postgresql://u:p@localhost:5432/bc_tenders"
    log_path = tmp_path / "destructive_operations.log"
    with patch("db.db_safety.DESTRUCTIVE_LOG_PATH", log_path):
        with patch("db.db_safety.load_app_env"):
            with patch("db.db_safety.resolve_script_database_url", side_effect=[local, prod]):
                with patch("db.db_safety.apply_script_database_url", return_value=prod):
                    stdin = io.StringIO(PRODUCTION_CONFIRMATION + "\n")
                    with patch("sys.stdin", stdin):
                        with patch("sys.stdin.isatty", return_value=True):
                            result = guard_destructive_db(
                                script_name="merge.py",
                                allow_production=True,
                            )
    assert result == prod
    assert log_path.read_text(encoding="utf-8").startswith("")
    assert "merge.py" in log_path.read_text(encoding="utf-8")
