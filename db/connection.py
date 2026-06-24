from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any, Literal, TypeVar
from urllib.parse import quote_plus

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.orm import Session, sessionmaker

from config.env import get_env, load_app_env
from db.models import Base

T = TypeVar("T")

DbInitStatus = Literal["pending", "running", "complete", "failed"]

_db_init_lock = threading.Lock()
_db_init_status: DbInitStatus = "pending"
_db_init_error: str | None = None
_db_init_started_at: datetime | None = None
_db_init_completed_at: datetime | None = None
_last_init_db_error: str | None = None
_MAX_DB_INIT_ERROR_LEN = 500

# PostgreSQL recovery / startup / capacity errors that clear after a short wait.
TRANSIENT_DB_ERROR_MARKERS = (
    "recovery mode",
    "in recovery",
    "the database system is starting up",
    "not yet accepting connections",
    "cannot_connect_now",
    "57p03",
    "server closed the connection unexpectedly",
    "connection refused",
    "connection reset",
    "too many connections",
    "timeout expired",
    "could not connect to server",
    "broken pipe",
    "ssl syscall error",
)


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


def _db_connect_timeout_seconds() -> int:
    raw = get_env("DB_CONNECT_TIMEOUT", "10")
    try:
        return max(1, int(raw))
    except ValueError:
        return 10


def _truncate_init_error(message: str) -> str:
    if len(message) <= _MAX_DB_INIT_ERROR_LEN:
        return message
    return message[: _MAX_DB_INIT_ERROR_LEN - 3] + "..."


def _engine_connect_args(url: str) -> dict[str, Any]:
    args: dict[str, Any] = {"connect_timeout": _db_connect_timeout_seconds()}
    try:
        host = (make_url(url).host or "").lower()
    except Exception:
        return args
    if _is_railway_host(host):
        args["sslmode"] = "require"
    return args


def get_db_init_status() -> dict[str, str | None]:
    with _db_init_lock:
        return {
            "status": _db_init_status,
            "error": _db_init_error,
            "started_at": _db_init_started_at.isoformat() if _db_init_started_at else None,
            "completed_at": _db_init_completed_at.isoformat() if _db_init_completed_at else None,
        }


def _run_init_db_background() -> None:
    global _db_init_status, _db_init_error, _db_init_completed_at, _last_init_db_error

    ok = False
    error_msg: str | None = None
    try:
        ok = init_db(raise_on_failure=False)
        if not ok:
            error_msg = _last_init_db_error or "init_db failed after retries"
    except Exception as exc:
        error_msg = str(exc)
        ok = False

    with _db_init_lock:
        _db_init_completed_at = datetime.now(timezone.utc)
        if ok:
            _db_init_status = "complete"
            _db_init_error = None
        else:
            _db_init_status = "failed"
            _db_init_error = _truncate_init_error(error_msg or "init_db failed")
            print(f"[DB] background init_db failed: {_db_init_error}")


def start_init_db_background() -> None:
    global _db_init_status, _db_init_started_at

    with _db_init_lock:
        if _db_init_status in ("running", "complete"):
            return
        _db_init_status = "running"
        _db_init_started_at = datetime.now(timezone.utc)
        thread = threading.Thread(
            target=_run_init_db_background,
            name="db-init",
            daemon=True,
        )
        thread.start()


def is_transient_db_error(exc: Exception) -> bool:
    if not isinstance(exc, (OperationalError, DBAPIError)):
        return False
    message = str(exc).lower()
    if getattr(exc, "connection_invalidated", False):
        return True
    return any(marker in message for marker in TRANSIENT_DB_ERROR_MARKERS)


def _parse_positive_float(name: str, default: float) -> float:
    raw = get_env(name, str(default))
    try:
        return max(0.5, float(raw))
    except ValueError:
        return default


def _parse_positive_int(name: str, default: int) -> int:
    raw = get_env(name, str(default))
    try:
        return max(1, int(raw))
    except ValueError:
        return default


def db_init_retry_settings() -> tuple[int, float, float]:
    """Startup/init retries — keep total wait under ~30s so the FastAPI lifespan
    yields quickly and the healthcheck endpoint becomes reachable."""
    return (
        _parse_positive_int("DB_INIT_RETRIES", 5),
        _parse_positive_float("DB_INIT_RETRY_DELAY", 2.0),
        _parse_positive_float("DB_INIT_RETRY_MAX_DELAY", 5.0),
    )


def db_request_retry_settings() -> tuple[int, float, float]:
    """Runtime request retries for API handlers and background jobs."""
    return (
        _parse_positive_int("DB_REQUEST_RETRIES", 5),
        _parse_positive_float("DB_REQUEST_RETRY_DELAY", 1.0),
        _parse_positive_float("DB_REQUEST_RETRY_MAX_DELAY", 10.0),
    )


def _backoff_delay(attempt: int, base_delay: float, max_delay: float) -> float:
    return min(max_delay, base_delay * (2 ** (attempt - 1)))


def run_with_db_retry(
    operation: Callable[[], T],
    *,
    context: str,
    retries: int,
    base_delay: float,
    max_delay: float,
    retry_transient_only: bool = True,
) -> T:
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            return operation()
        except Exception as exc:
            last_error = exc
            if retry_transient_only and not is_transient_db_error(exc):
                raise
            if attempt >= retries:
                break
            wait = _backoff_delay(attempt, base_delay, max_delay)
            print(
                f"[DB] {context} attempt {attempt}/{retries} failed "
                f"(retrying in {wait:.1f}s): {exc}"
            )
            time.sleep(wait)
    if last_error:
        raise last_error
    raise RuntimeError(f"{context} failed without an error")


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


def _ping_session(session: Session) -> None:
    session.execute(text("SELECT 1"))


def _opportunities_debug_enabled() -> bool:
    return os.getenv("OPPORTUNITIES_DEBUG", "").lower() in {"1", "true", "yes"}


@contextmanager
def session_scope() -> Iterator[Session]:
    """Yield a short-lived session; always returns the connection to the pool."""
    checkout_started = time.perf_counter()
    session = get_session_factory()()
    if _opportunities_debug_enabled():
        checkout_ms = (time.perf_counter() - checkout_started) * 1000
        pool = get_engine().pool
        print(
            f"[DB:pool] checkout_ms={checkout_ms:.1f} "
            f"checked_out={pool.checkedout()} overflow={pool.overflow()} size={pool.size()}"
        )
    try:
        yield session
    finally:
        session.close()


def get_session() -> Session:
    """Return a DB session, retrying transient errors such as recovery mode."""
    retries, base_delay, max_delay = db_request_retry_settings()
    session = get_session_factory()()

    def _connect() -> Session:
        _ping_session(session)
        return session

    try:
        return run_with_db_retry(
            _connect,
            context="get_session",
            retries=retries,
            base_delay=base_delay,
            max_delay=max_delay,
        )
    except Exception:
        session.close()
        raise


def check_db_connection() -> bool:
    try:
        run_with_db_retry(
            lambda: _verify_connection(get_engine()),
            context="check_db_connection",
            retries=_parse_positive_int("DB_HEALTH_RETRIES", 2),
            base_delay=_parse_positive_float("DB_HEALTH_RETRY_DELAY", 1.0),
            max_delay=_parse_positive_float("DB_HEALTH_RETRY_MAX_DELAY", 3.0),
        )
        return True
    except Exception as exc:
        print(f"[DB] Health check failed: {exc}")
        return False


def _verify_connection(engine: Engine, *, log_failures: bool = True) -> None:
    retries, base_delay, max_delay = db_init_retry_settings()

    def _ping() -> None:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))

    run_with_db_retry(
        _ping,
        context="init_db connection",
        retries=retries,
        base_delay=base_delay,
        max_delay=max_delay,
    )


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
        "ALTER TABLE permits ADD COLUMN IF NOT EXISTS source VARCHAR(50) DEFAULT 'vancouver'",
        "ALTER TABLE permits ADD COLUMN IF NOT EXISTS city VARCHAR(100) DEFAULT 'Vancouver'",
        "ALTER TABLE permits ADD COLUMN IF NOT EXISTS external_id VARCHAR(100) DEFAULT ''",
        "CREATE INDEX IF NOT EXISTS ix_permits_source ON permits (source)",
        "CREATE INDEX IF NOT EXISTS ix_permits_city ON permits (city)",
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_permits_source_external_id "
        "ON permits (source, external_id) WHERE external_id <> ''",
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


def _ensure_permit_early_signal_columns(engine) -> None:
    statements = (
        "ALTER TABLE permits ADD COLUMN IF NOT EXISTS application_date VARCHAR(20) DEFAULT ''",
        "ALTER TABLE permits ADD COLUMN IF NOT EXISTS contractor VARCHAR(300) DEFAULT ''",
        "ALTER TABLE permits ADD COLUMN IF NOT EXISTS local_area VARCHAR(100) DEFAULT ''",
        "CREATE INDEX IF NOT EXISTS ix_permits_application_date "
        "ON permits (application_date) WHERE application_date <> ''",
        "CREATE INDEX IF NOT EXISTS ix_permits_local_area "
        "ON permits (local_area) WHERE local_area <> ''",
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


def _ensure_tender_matches_table(engine) -> None:
    statements = (
        """
        CREATE TABLE IF NOT EXISTS tender_matches (
            id SERIAL PRIMARY KEY,
            company_id INTEGER NOT NULL,
            tender_id INTEGER NOT NULL,
            score INTEGER NOT NULL DEFAULT 0,
            reasoning TEXT DEFAULT '',
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
        """,
        "CREATE INDEX IF NOT EXISTS ix_tender_matches_company_id ON tender_matches (company_id)",
        "CREATE INDEX IF NOT EXISTS ix_tender_matches_tender_id ON tender_matches (tender_id)",
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_tender_matches_company_tender "
        "ON tender_matches (company_id, tender_id)",
    )
    with engine.begin() as conn:
        for statement in statements:
            conn.execute(text(statement))


def _migrate_tender_matches_breakdown_json(engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "ALTER TABLE tender_matches "
                "ADD COLUMN IF NOT EXISTS breakdown_json JSONB"
            )
        )


def _migrate_tender_matches_company_kind(engine) -> None:
    statements = (
        "ALTER TABLE tender_matches "
        "ADD COLUMN IF NOT EXISTS company_kind VARCHAR(20) NOT NULL DEFAULT 'architecture'",
        "ALTER TABLE tender_matches "
        "ADD COLUMN IF NOT EXISTS tender_source VARCHAR(20) NOT NULL DEFAULT 'arch'",
        "CREATE INDEX IF NOT EXISTS ix_tender_matches_company_kind "
        "ON tender_matches (company_kind)",
        "CREATE INDEX IF NOT EXISTS ix_tender_matches_tender_source "
        "ON tender_matches (tender_source)",
        "DROP INDEX IF EXISTS ix_tender_matches_company_tender",
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_tender_matches_company_kind_tender "
        "ON tender_matches (company_kind, company_id, tender_source, tender_id)",
        "CREATE INDEX IF NOT EXISTS ix_tender_matches_company_created "
        "ON tender_matches (company_kind, company_id, created_at DESC)",
    )
    with engine.begin() as conn:
        for statement in statements:
            conn.execute(text(statement))


def _ensure_company_intelligence_columns(engine) -> None:
    statements = (
        "ALTER TABLE companies ADD COLUMN IF NOT EXISTS company_type VARCHAR(50) DEFAULT ''",
        "ALTER TABLE companies ADD COLUMN IF NOT EXISTS confidence_score FLOAT",
        "ALTER TABLE companies ADD COLUMN IF NOT EXISTS company_lifecycle VARCHAR(20) DEFAULT ''",
        "ALTER TABLE companies ADD COLUMN IF NOT EXISTS company_tier VARCHAR(20) DEFAULT ''",
        "ALTER TABLE companies ADD COLUMN IF NOT EXISTS enrichment_status VARCHAR(20) DEFAULT 'pending'",
        "ALTER TABLE companies ADD COLUMN IF NOT EXISTS last_enriched_at TIMESTAMPTZ",
        "CREATE INDEX IF NOT EXISTS ix_companies_company_type ON companies (company_type)",
        "CREATE INDEX IF NOT EXISTS ix_companies_company_lifecycle ON companies (company_lifecycle)",
        "CREATE INDEX IF NOT EXISTS ix_companies_enrichment_status ON companies (enrichment_status)",
    )
    with engine.begin() as conn:
        for statement in statements:
            conn.execute(text(statement))


def _ensure_company_award_columns(engine) -> None:
    statements = (
        "ALTER TABLE companies ADD COLUMN IF NOT EXISTS award_count INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE companies ADD COLUMN IF NOT EXISTS total_award_value FLOAT NOT NULL DEFAULT 0.0",
        "ALTER TABLE companies ADD COLUMN IF NOT EXISTS avg_award_value FLOAT NOT NULL DEFAULT 0.0",
        "ALTER TABLE companies ADD COLUMN IF NOT EXISTS award_categories VARCHAR[] DEFAULT '{}'",
        "ALTER TABLE companies ADD COLUMN IF NOT EXISTS award_clients VARCHAR[] DEFAULT '{}'",
        "ALTER TABLE companies ADD COLUMN IF NOT EXISTS buyer_levels VARCHAR[] DEFAULT '{}'",
        "ALTER TABLE companies ADD COLUMN IF NOT EXISTS award_sources VARCHAR[] DEFAULT '{}'",
        "ALTER TABLE companies ADD COLUMN IF NOT EXISTS first_award_date VARCHAR(20) DEFAULT ''",
        "ALTER TABLE companies ADD COLUMN IF NOT EXISTS last_award_date VARCHAR(20) DEFAULT ''",
        "ALTER TABLE companies ADD COLUMN IF NOT EXISTS primary_address VARCHAR(500) DEFAULT ''",
        "ALTER TABLE companies ADD COLUMN IF NOT EXISTS primary_city VARCHAR(100) DEFAULT ''",
        "ALTER TABLE companies ADD COLUMN IF NOT EXISTS primary_province VARCHAR(50) DEFAULT ''",
        "ALTER TABLE companies ADD COLUMN IF NOT EXISTS data_sources VARCHAR[] DEFAULT '{}'",
        "ALTER TABLE companies ADD COLUMN IF NOT EXISTS canonical_vendor_name VARCHAR(300) DEFAULT ''",
        "CREATE INDEX IF NOT EXISTS ix_companies_award_count ON companies (award_count)",
        "CREATE INDEX IF NOT EXISTS ix_companies_total_award_value ON companies (total_award_value)",
        "CREATE INDEX IF NOT EXISTS ix_companies_last_award_date ON companies (last_award_date)",
        "CREATE INDEX IF NOT EXISTS ix_companies_data_sources ON companies USING GIN (data_sources)",
    )
    with engine.begin() as conn:
        for statement in statements:
            conn.execute(text(statement))


def _ensure_bd_intelligence_columns(engine) -> None:
    statements = (
        "ALTER TABLE companies ADD COLUMN IF NOT EXISTS primary_trade VARCHAR(50) DEFAULT ''",
        "ALTER TABLE companies ADD COLUMN IF NOT EXISTS trade_tags VARCHAR[] DEFAULT '{}'",
        "ALTER TABLE companies ADD COLUMN IF NOT EXISTS capability_profile_json JSONB",
        "ALTER TABLE companies ADD COLUMN IF NOT EXISTS capability_profile_at TIMESTAMPTZ",
        "CREATE INDEX IF NOT EXISTS ix_companies_primary_trade ON companies (primary_trade)",
        "ALTER TABLE arch_companies ADD COLUMN IF NOT EXISTS primary_trade VARCHAR(50) DEFAULT ''",
        "ALTER TABLE arch_companies ADD COLUMN IF NOT EXISTS trade_tags VARCHAR[] DEFAULT '{}'",
        "ALTER TABLE arch_companies ADD COLUMN IF NOT EXISTS capability_profile_json JSONB",
        "ALTER TABLE arch_companies ADD COLUMN IF NOT EXISTS capability_profile_at TIMESTAMPTZ",
        "CREATE INDEX IF NOT EXISTS ix_arch_companies_primary_trade ON arch_companies (primary_trade)",
        "ALTER TABLE tenders ADD COLUMN IF NOT EXISTS estimated_value_numeric FLOAT",
        "ALTER TABLE tenders ADD COLUMN IF NOT EXISTS buyer_level VARCHAR(20) DEFAULT ''",
        "ALTER TABLE commercial_tenders ADD COLUMN IF NOT EXISTS buyer_level VARCHAR(20) DEFAULT ''",
        "ALTER TABLE commercial_tenders ADD COLUMN IF NOT EXISTS estimated_value_numeric FLOAT",
    )
    with engine.begin() as conn:
        for statement in statements:
            conn.execute(text(statement))


def _ensure_company_wiki_table(engine) -> None:
    statements = (
        """
        CREATE TABLE IF NOT EXISTS company_wiki (
            id                  SERIAL PRIMARY KEY,
            company_id          INTEGER NOT NULL,
            company_kind        VARCHAR(20) NOT NULL DEFAULT 'construction',
            company_name        VARCHAR(300) NOT NULL DEFAULT '',
            wiki_markdown       TEXT NOT NULL DEFAULT '',
            summary             TEXT NOT NULL DEFAULT '',
            specializations     TEXT NOT NULL DEFAULT '',
            market_position     TEXT NOT NULL DEFAULT '',
            geographic_focus    TEXT NOT NULL DEFAULT '',
            competitive_profile TEXT NOT NULL DEFAULT '',
            data_snapshot       JSONB,
            model_used          VARCHAR(80) NOT NULL DEFAULT '',
            generated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_company_wiki_company_kind "
        "ON company_wiki (company_id, company_kind)",
        "CREATE INDEX IF NOT EXISTS ix_company_wiki_generated_at "
        "ON company_wiki (generated_at DESC)",
        "CREATE INDEX IF NOT EXISTS ix_company_wiki_kind "
        "ON company_wiki (company_kind)",
    )
    with engine.begin() as conn:
        for statement in statements:
            conn.execute(text(statement))


def _ensure_cip_columns(engine) -> None:
    statements = (
        "ALTER TABLE companies ADD COLUMN IF NOT EXISTS cip_json JSONB",
        "ALTER TABLE companies ADD COLUMN IF NOT EXISTS cip_at TIMESTAMPTZ",
        "ALTER TABLE companies ADD COLUMN IF NOT EXISTS cip_version INTEGER",
        "ALTER TABLE companies ADD COLUMN IF NOT EXISTS dominant_sector VARCHAR(30) DEFAULT ''",
        "ALTER TABLE companies ADD COLUMN IF NOT EXISTS work_orientation VARCHAR(20) DEFAULT ''",
        "ALTER TABLE companies ADD COLUMN IF NOT EXISTS specialization_confidence FLOAT",
        "ALTER TABLE companies ADD COLUMN IF NOT EXISTS geographic_reach VARCHAR(20) DEFAULT ''",
        "ALTER TABLE companies ADD COLUMN IF NOT EXISTS value_p25 FLOAT",
        "ALTER TABLE companies ADD COLUMN IF NOT EXISTS value_p75 FLOAT",
        "CREATE INDEX IF NOT EXISTS ix_companies_dominant_sector ON companies (dominant_sector)",
        "ALTER TABLE arch_companies ADD COLUMN IF NOT EXISTS cip_json JSONB",
        "ALTER TABLE arch_companies ADD COLUMN IF NOT EXISTS cip_at TIMESTAMPTZ",
        "ALTER TABLE arch_companies ADD COLUMN IF NOT EXISTS cip_version INTEGER",
        "ALTER TABLE arch_companies ADD COLUMN IF NOT EXISTS dominant_sector VARCHAR(30) DEFAULT ''",
        "ALTER TABLE arch_companies ADD COLUMN IF NOT EXISTS work_orientation VARCHAR(20) DEFAULT ''",
        "ALTER TABLE arch_companies ADD COLUMN IF NOT EXISTS specialization_confidence FLOAT",
        "ALTER TABLE arch_companies ADD COLUMN IF NOT EXISTS geographic_reach VARCHAR(20) DEFAULT ''",
        "ALTER TABLE arch_companies ADD COLUMN IF NOT EXISTS value_p25 FLOAT",
        "ALTER TABLE arch_companies ADD COLUMN IF NOT EXISTS value_p75 FLOAT",
        "CREATE INDEX IF NOT EXISTS ix_arch_companies_dominant_sector ON arch_companies (dominant_sector)",
    )
    with engine.begin() as conn:
        for statement in statements:
            conn.execute(text(statement))


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


def _ensure_wiki_batch_progress_table(engine) -> None:
    """Checkpoint table for resumable batch wiki generation."""
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS wiki_batch_progress (
                id           SERIAL PRIMARY KEY,
                batch_id     VARCHAR(36)  NOT NULL,
                company_id   INTEGER      NOT NULL,
                company_kind VARCHAR(20)  NOT NULL DEFAULT 'construction',
                status       VARCHAR(20)  NOT NULL DEFAULT 'pending',
                error        TEXT         NOT NULL DEFAULT '',
                created_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
                updated_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW()
            )
        """))
    # Indexes in separate connections so a single failure doesn't roll back the table.
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_wiki_batch_progress_batch_id "
            "ON wiki_batch_progress (batch_id)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_wiki_batch_progress_status "
            "ON wiki_batch_progress (status)"
        ))
        conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_wiki_batch_progress_entry "
            "ON wiki_batch_progress (batch_id, company_id, company_kind)"
        ))


def _ensure_client_profiles_table(engine) -> None:
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS client_profiles (
                id                  SERIAL PRIMARY KEY,
                clerk_user_id       VARCHAR(100) NOT NULL DEFAULT '',
                company_id          INTEGER NOT NULL,
                company_name        VARCHAR(300) NOT NULL DEFAULT '',
                email               VARCHAR(320) NOT NULL,
                regions             VARCHAR[] DEFAULT '{}',
                specializations     VARCHAR[] DEFAULT '{}',
                min_project_value   FLOAT,
                max_project_value   FLOAT,
                alerts_enabled      BOOLEAN NOT NULL DEFAULT TRUE,
                created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_client_profiles_clerk_user_id "
            "ON client_profiles (clerk_user_id)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_client_profiles_company_id "
            "ON client_profiles (company_id)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_client_profiles_email "
            "ON client_profiles (email)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_client_profiles_alerts_enabled "
            "ON client_profiles (alerts_enabled)"
        ))
        conn.execute(text("""
            INSERT INTO client_profiles (company_id, company_name, email, regions, alerts_enabled)
            SELECT
                1,
                COALESCE(c.name, 'Test Company'),
                'test@tenderscope.ca',
                ARRAY['Vancouver', 'Burnaby', 'Surrey'],
                TRUE
            FROM companies c
            WHERE c.id = 1
              AND NOT EXISTS (
                  SELECT 1 FROM client_profiles WHERE email = 'test@tenderscope.ca'
              )
        """))


def _run_migrations(engine: Engine) -> None:
    Base.metadata.create_all(bind=engine)
    _ensure_tender_matches_table(engine)
    _migrate_tender_matches_company_kind(engine)
    _migrate_tender_matches_breakdown_json(engine)
    _ensure_pipeline_runs_table(engine)
    _ensure_ai_columns(engine)
    _ensure_permit_early_signal_columns(engine)
    _ensure_company_intelligence_columns(engine)
    _ensure_company_award_columns(engine)
    _ensure_bd_intelligence_columns(engine)
    _ensure_cip_columns(engine)
    _ensure_company_wiki_table(engine)
    _ensure_wiki_batch_progress_table(engine)
    _ensure_client_profiles_table(engine)
    _widen_commercial_text_columns(engine)


def init_db(*, raise_on_failure: bool = False) -> bool:
    """Initialize schema. Retries through Postgres recovery; app may start degraded."""
    global _last_init_db_error

    import db.models  # noqa: F401  # register all ORM models before create_all

    engine = get_engine()
    retries, base_delay, max_delay = db_init_retry_settings()

    try:
        run_with_db_retry(
            lambda: _run_migrations(engine),
            context="init_db",
            retries=retries,
            base_delay=base_delay,
            max_delay=max_delay,
        )
        _last_init_db_error = None
        print("[DB] init_db complete")
        return True
    except Exception as exc:
        _last_init_db_error = str(exc)
        print(f"[DB] init_db failed after retries: {exc}")
        if raise_on_failure:
            raise
        return False
