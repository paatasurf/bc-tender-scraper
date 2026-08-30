"""Regression test: the local-Postgres integration test files whose
db_session fixtures were fixed to use tests.db_transactional_fixture
(SAVEPOINT-based isolation) and delta-based row-count assertions must
pass regardless of which order they run in and regardless of what a
DIFFERENT test file already committed to the shared database.

Runs each ordering as a genuinely separate pytest subprocess (not
pytest.main() in-process) so each ordering gets its own fresh Python
process/import state, matching how CI actually invokes pytest.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from tests.db_test_safety import require_local_test_database

_FIXED_MODULES = [
    "tests/unit/test_run_permit_official_source_id_bridge_full_db.py",
    "tests/unit/test_run_surrey_applicant_recovery_full_db.py",
]

_ORDERINGS = [
    _FIXED_MODULES,
    list(reversed(_FIXED_MODULES)),
]


@pytest.mark.parametrize("modules", _ORDERINGS, ids=["forward_order", "reversed_order"])
def test_fixed_modules_pass_regardless_of_run_order(modules):
    require_local_test_database()  # skips (not fails) if no local Postgres

    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", *modules],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert (
        result.returncode == 0
    ), f"order {modules} failed:\n{result.stdout[-4000:]}\n{result.stderr[-2000:]}"
