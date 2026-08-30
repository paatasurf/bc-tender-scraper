"""Shared helper for local-Postgres integration tests that need to model
data predating a unique constraint that was added on top of it later --
the exact scenario the "ambiguous match" code paths in the Surrey
identity-import pipeline defend against (db/surrey_permit_import.py,
pipeline/surrey_identity_import_canary.py). Two Permit rows sharing a
(source, official_source_id) or (source, external_id) value can no longer
be constructed via a normal INSERT once
db/permit_official_source_id_migration.py's
``ux_permits_source_official_source_id`` or the pre-existing
``ix_permits_source_external_id`` partial unique index is in place.

``temporarily_drop_unique_index`` drops the named index for the rest of
the CALLER's own already-open, never-committed transaction (or, under a
SAVEPOINT-based session -- see tests/db_transactional_fixture.py -- the
current savepoint) only. PostgreSQL DDL is fully transactional: nothing
outside that transaction/savepoint ever observes the index as missing,
and it is restored exactly as before the moment that transaction rolls
back at fixture teardown -- the real schema is never actually, durably
weakened. This does not touch db_safety's production-authorization guard
at all; it only lets one test's own private transaction temporarily
recreate a pre-constraint data shape to exercise still-live defensive
code, then discards that shape along with everything else the test did.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import text
from sqlalchemy.orm import Session


@contextmanager
def temporarily_drop_unique_index(session: Session, index_name: str) -> Iterator[None]:
    session.execute(text(f"DROP INDEX {index_name}"))
    yield
