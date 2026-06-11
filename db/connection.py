from __future__ import annotations

import os
import time
from functools import lru_cache
from urllib.parse import quote_plus

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from config.env import load_app_env
from db.models import Base


def _strip_env(value: str) -> str:
    return value.strip().strip('"').strip("'")


def _is_railway_host(host: str) -> bool:
    host = host.lower()
    return (
        host.endswith(".railway.internal")
        or host.endswith(".rlwy.net")
        or "railway.app" in host
    )


def _normalize_database_url(raw: str) -> str:
    raw = _strip_env(raw)
    if raw.startswith("postgres://"):
        raw = "postgresql://" + raw[len("postgres://") :]

    try:
        url = make_url(raw)
    except Exception as exc:
        raise RuntimeError(f"DATABASE_URL is not a valid SQLAlchemy URL: {exc}") from exc

    host = (url.host or "").lower()
    query = dict(url.query)
    if _is_railway_host(host) and "sslmode" not in query:
        query["sslmode"] = "require"
        url = url.set(query=query)

    return url.render_as_string(hide_password=False)


def _database_url_from_pg_vars() -> str:
    host = _strip_env(os.getenv("PGHOST", ""))
    user = _strip_env(os.getenv("PGUSER", ""))
    password = os.getenv("PGPASSWORD", "")
    database = _strip_env(os.getenv("PGDATABASE", ""))
    port = _strip_env(os.getenv("PGPORT", "5432")) or "5432"

    if not (host and user and database):
        return ""

    auth = quote_plus(user)
    if password:
        auth = f"{auth}:{quote_plus(password)}"
    raw = f"postgresql://{auth}@{host}:{port}/{quote_plus(database)}"
    return _normalize_database_url(raw)


def _database_url() -> str:
    load_app_env()

    for name in ("DATABASE_URL", "DATABASE_PRIVATE_URL", "DATABASE_PUBLIC_URL"):
        value = _strip_env(os.getenv(name, ""))
        if value:
            return _normalize_database_url(value)

    from_pg = _database_url_from_pg_vars()
    if from_pg:
        return from_pg

    raise RuntimeError(
        "DATABASE_URL is not set. On Railway, link a PostgreSQL plugin so DATABASE_URL "
        "is injected, or set PGHOST/PGUSER/PGPASSWORD/PGDATABASE."
    )


def _engine_connect_args(url: str) -> dict[str, str]:
    try:
        host = (make_url(url).host or "").lower()
    except Exception:
        return {}
    if _is_railway_host(host):
        return {"sslmode": "require"}
    return {}


@lru_cache
def get_engine() -> Engine:
    url = _database_url()
    return create_engine(
        url,
        pool_pre_ping=True,
        pool_recycle=1800,
        connect_args=_engine_connect_args(url),
    )


def get_session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), autoflush=False, autocommit=False)


def get_session() -> Session:
    return get_session_factory()()


def _verify_connection(engine: Engine, *, retries: int = 5, delay: float = 2.0) -> None:
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return
        except OperationalError as exc:
            last_error = exc
            if attempt >= retries:
                break
            print(f"[DB] Connection attempt {attempt}/{retries} failed, retrying: {exc}")
            time.sleep(delay)
    if last_error:
        raise last_error


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
        "ALTER TABLE reddit ADD COLUMN IF NOT EXISTS subreddit VARCHAR(100) DEFAULT ''",
    )
    with engine.begin() as conn:
        for statement in statements:
            conn.execute(text(statement))


def _widen_commercial_text_columns(engine) -> None:
    if "commercial_tenders" not in inspect(engine).get_table_names():
        return

    with engine.begin() as conn:
        for column in COMMERCIAL_TEXT_COLUMNS:
            conn.execute(
                text(
                    f"ALTER TABLE commercial_tenders "
                    f"ALTER COLUMN {column} TYPE TEXT USING {column}::TEXT"
                )
            )


def _ensure_pipeline_runs_table(engine) -> None:
    statements = (
        """
        CREATE TABLE IF NOT EXISTS pipeline_runs (
            id SERIAL PRIMARY KEY,
            run_id VARCHAR(36) NOT NULL,
            step VARCHAR(100) NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'running',
            started_at TIMESTAMPTZ DEFAULT NOW(),
            finished_at TIMESTAMPTZ,
            error TEXT DEFAULT '',
            counts_json TEXT DEFAULT '{}'
        )
        """,
        "CREATE INDEX IF NOT EXISTS ix_pipeline_runs_run_id ON pipeline_runs (run_id)",
        "CREATE INDEX IF NOT EXISTS ix_pipeline_runs_step ON pipeline_runs (step)",
    )
    with engine.begin() as conn:
        for statement in statements:
            conn.execute(text(statement))


def init_db() -> None:
    import db.models  # noqa: F401  # register all ORM models before create_all

    engine = get_engine()
    _verify_connection(engine)
    Base.metadata.create_all(bind=engine)
    _ensure_pipeline_runs_table(engine)
    _ensure_ai_columns(engine)
    _widen_commercial_text_columns(engine)
