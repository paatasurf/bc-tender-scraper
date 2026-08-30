"""Regression test: db.db_safety's thread-local guard context (see
tests/conftest.py's autouse reset fixture) must never leak from one test
file into a later, unrelated one within the same pytest process.

Runs test_claims_gateway.py's test that deliberately triggers-and-catches a
SystemExit mid Class-D authorization
(test_acquire_write_capability_refuses_production_without_real_tty)
immediately followed by a test that calls the real init_db() -- exactly the
sequence that, before tests/conftest.py's autouse reset fixture existed,
produced "RuntimeError: DATABASE_URL_PRODUCTION is not set" in CI (and a
refused-production-write banner locally, where DATABASE_URL_PRODUCTION
happens to be set).

Runs as a genuine pytest subprocess -- not in-process pytest.main() reuse
-- so this reflects how CI actually invokes pytest, and a passing result
here cannot be explained by import-order accidents specific to a single
process instance.
"""

from __future__ import annotations

import subprocess
import sys

from tests.db_test_safety import require_local_test_database


def test_claims_gateway_leak_does_not_break_a_later_init_db_call():
    require_local_test_database()  # skips (not fails) if no local Postgres

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "tests/unit/test_claims_gateway.py::test_acquire_write_capability_refuses_production_without_real_tty",
            "tests/unit/test_stage2_partial_import.py::test_no_skip_imports_everything_as_before",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert (
        result.returncode == 0
    ), f"leaked guard context broke the later test:\n{result.stdout[-4000:]}\n{result.stderr[-2000:]}"
