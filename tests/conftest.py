"""Session-wide pytest fixtures for the whole tests/ tree."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _reset_db_safety_guard_context():
    """``db.db_safety._guard_local`` is ``threading.local()`` shared for the
    whole pytest process; nothing in db_safety.py itself ever resets it
    between calls, so a test that starts (but doesn't complete) a Class-D
    guard authorization leaves that context behind for every later test in
    the same process.

    Confirmed root cause of test_stage2_partial_import.py /
    test_tender_presence.py erroring with "DATABASE_URL_PRODUCTION is not
    set" whenever they ran after
    test_claims_gateway.py::test_acquire_write_capability_refuses_production_without_real_tty:
    that test's own (expected, caught) SystemExit -- raised by the
    interactive-TTY confirmation gate -- fires before db_safety ever marks
    its guard context authorized, so the half-set context leaks forward.
    See tests/unit/test_db_safety_guard_context_isolation.py for the
    regression test.

    Clears db_safety's own private guard-context attribute directly
    (test-only isolation; does not change db_safety.py or weaken any
    authorization check it enforces) before and after every test, so each
    test's guard state stays private to itself -- matching how a real,
    single-purpose CLI script invocation would only ever set this once.
    """
    import db.db_safety as db_safety

    db_safety._guard_local.ctx = None
    yield
    db_safety._guard_local.ctx = None
