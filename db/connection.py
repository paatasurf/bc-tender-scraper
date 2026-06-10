from __future__ import annotations

import os
from functools import lru_cache

from sqlalchemy import create_engine, text
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


COMMERCIAL_TEXT_COLUMNS = (
    "title",
    "company",
    "value",
    "deadline",
    "status",
    "category",
    "url",
    "tender_id",
    "source",
    "ai_budget_estimate",
)


def _ensure_ai_columns(engine) -> None:
    statements = (
        "ALTER TABLE arch_tenders ADD COLUMN IF NOT EXISTS ai_score INTEGER",
        "ALTER TABLE arch_tenders ADD COLUMN IF NOT EXISTS ai_summary TEXT",
        "ALTER TABLE arch_tenders ADD COLUMN IF NOT EXISTS ai_budget_estimate TEXT",
        "ALTER TABLE commercial_tenders ADD COLUMN IF NOT EXISTS ai_score INTEGER",
        "ALTER TABLE commercial_tenders ADD COLUMN IF NOT EXISTS ai_summary TEXT",
        "ALTER TABLE commercial_tenders ADD COLUMN IF NOT EXISTS ai_budget_estimate TEXT",
        "ALTER TABLE tenders ADD COLUMN IF NOT EXISTS ai_score INTEGER",
        "ALTER TABLE tenders ADD COLUMN IF NOT EXISTS ai_summary TEXT",
        "ALTER TABLE tenders ADD COLUMN IF NOT EXISTS ai_budget_estimate TEXT",
        "ALTER TABLE permits ADD COLUMN IF NOT EXISTS architect VARCHAR(300) DEFAULT ''",
        "ALTER TABLE arch_companies ADD COLUMN IF NOT EXISTS google_place_id VARCHAR(200)",
        "ALTER TABLE arch_companies ADD COLUMN IF NOT EXISTS website VARCHAR(500) DEFAULT ''",
        "ALTER TABLE arch_companies ADD COLUMN IF NOT EXISTS lat FLOAT",
        "ALTER TABLE arch_companies ADD COLUMN IF NOT EXISTS lng FLOAT",
        "ALTER TABLE arch_companies ADD COLUMN IF NOT EXISTS houzz_projects_count INTEGER",
        "ALTER TABLE arch_companies ADD COLUMN IF NOT EXISTS houzz_project_types VARCHAR[] DEFAULT '{}'",
        "ALTER TABLE arch_companies ADD COLUMN IF NOT EXISTS houzz_service_areas VARCHAR[] DEFAULT '{}'",
        "ALTER TABLE arch_companies ADD COLUMN IF NOT EXISTS houzz_reviews_count INTEGER",
        "ALTER TABLE arch_companies ADD COLUMN IF NOT EXISTS houzz_rating FLOAT",
        "ALTER TABLE arch_companies ADD COLUMN IF NOT EXISTS houzz_profile_url VARCHAR(500) DEFAULT ''",
        "ALTER TABLE arch_companies ADD COLUMN IF NOT EXISTS aibc_status VARCHAR(50) DEFAULT ''",
        "ALTER TABLE arch_companies ADD COLUMN IF NOT EXISTS website_projects_count INTEGER",
        "ALTER TABLE arch_companies ADD COLUMN IF NOT EXISTS website_specializations VARCHAR[] DEFAULT '{}'",
        "ALTER TABLE arch_companies ADD COLUMN IF NOT EXISTS website_service_areas VARCHAR[] DEFAULT '{}'",
        "ALTER TABLE arch_companies ADD COLUMN IF NOT EXISTS website_notable_projects VARCHAR[] DEFAULT '{}'",
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_arch_companies_google_place_id "
        "ON arch_companies (google_place_id)",
    )
    with engine.begin() as conn:
        for statement in statements:
            conn.execute(text(statement))


def _widen_commercial_text_columns(engine) -> None:
    with engine.begin() as conn:
        for column in COMMERCIAL_TEXT_COLUMNS:
            conn.execute(
                text(
                    f"ALTER TABLE commercial_tenders "
                    f"ALTER COLUMN {column} TYPE TEXT USING {column}::TEXT"
                )
            )


def init_db() -> None:
    engine = get_engine()
    Base.metadata.create_all(bind=engine)
    _ensure_ai_columns(engine)
    _widen_commercial_text_columns(engine)
