"""Shared DDL execution helpers for database migrations."""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import text
from sqlalchemy.engine import Engine


def execute_ddl_statements(engine: Engine, statements: Iterable[str]) -> None:
    """Execute a sequence of DDL statements in a single transaction."""
    with engine.begin() as conn:
        for statement in statements:
            conn.execute(text(statement))
