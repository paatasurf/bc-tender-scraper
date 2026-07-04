"""Tests for test-suite production DB refusal."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from tests.db_test_safety import require_local_test_database


def test_require_local_test_database_skips_when_unset():
    with patch("tests.db_test_safety.load_app_env"):
        with patch("tests.db_test_safety.get_env", return_value=None):
            with pytest.raises(pytest.skip.Exception):
                require_local_test_database()


def test_require_local_test_database_fails_on_production_url():
    prod_url = "postgresql://u:p@containers-us-west-123.railway.app:5432/railway"
    with patch("tests.db_test_safety.load_app_env"):
        with patch("tests.db_test_safety.get_env", return_value=prod_url):
            with patch("tests.db_test_safety.is_production_database_url", return_value=True):
                with pytest.raises(pytest.fail.Exception, match="Refusing test DB write"):
                    require_local_test_database()


def test_require_local_test_database_allows_localhost():
    local_url = "postgresql://u:p@localhost:5432/bc_tenders"
    with patch("tests.db_test_safety.load_app_env"):
        with patch("tests.db_test_safety.get_env", return_value=local_url):
            with patch("tests.db_test_safety.is_production_database_url", return_value=False):
                assert require_local_test_database() == local_url
