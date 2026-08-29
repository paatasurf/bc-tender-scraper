"""Shared helper for local-Postgres integration tests whose code under
test itself calls session.commit()/session.rollback() (the "Class-C
runner owns the transaction" pattern used by
scripts/run_permit_official_source_id_bridge_full.py,
scripts/run_surrey_applicant_recovery_full.py, and similar).

Without this, a plain `conn.begin()` + `Session(bind=conn)` fixture is
NOT safe to reuse across a test's own setup and the code under test: a
session.rollback() issued by the code under test rolls back the entire
connection's transaction, including whatever the test itself inserted
before calling it (session.flush() writes are not commits, so they are
just as undoable). Diagnosed via NoResultFound failures in tests whose
own setup rows vanished after the code under test's own rollback --
see PR history for the exact repro.

Implements SQLAlchemy 2.0's built-in `join_transaction_mode="create_savepoint"`
(the documented "Joining a Session into an External Transaction" recipe):
an outer, never-committed transaction wraps a SAVEPOINT that the Session
automatically restarts whenever the code under test ends it (by calling
session.commit() or session.rollback()), so the code under test can
commit/rollback freely without ever persisting real data or affecting
any other test.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session


@contextmanager
def transactional_session(engine: Engine) -> Iterator[Session]:
    conn = engine.connect()
    outer = conn.begin()
    session = Session(bind=conn, join_transaction_mode="create_savepoint")

    try:
        yield session
    finally:
        session.close()
        if outer.is_active:
            outer.rollback()
        conn.close()
