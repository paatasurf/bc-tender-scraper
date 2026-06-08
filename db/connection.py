from __future__ import annotations

import os
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from db.models import Base


def _database_url() -> str:
    url = os.getenv("DATABASE_URL", "")
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. Add a PostgreSQL database on Railway or set "
            "DATABASE_URL locally, e.g. postgresql://user:pass@localhost:5432/bc_tenders"
        )
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    return url


@lru_cache
def get_engine() -> Engine:
    return create_engine(_database_url(), pool_pre_ping=True)


def get_session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), autoflush=False, autocommit=False)


def get_session() -> Session:
    return get_session_factory()()


def init_db() -> None:
    Base.metadata.create_all(bind=get_engine())
