"""Unit tests for Google enrichment schema (migration 013)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect

from db.google_enrichment_ddl import (
    COMPANY_GOOGLE_COLUMN_NAMES,
    google_enrichment_migration_sql,
    google_enrichment_migration_statements,
)
from db.models import Company, GoogleEnrichmentLog, GoogleEnrichmentReview


def test_migration_sql_file_exists():
    path = Path(__file__).resolve().parents[2] / "db" / "migrations" / "013_google_enrichment.sql"
    assert path.is_file()
    assert "google_enrichment_logs" in path.read_text(encoding="utf-8")
    assert "google_enrichment_reviews" in path.read_text(encoding="utf-8")


def test_migration_statements_are_non_empty():
    statements = google_enrichment_migration_statements()
    assert len(statements) >= 5
    joined = "\n".join(statements)
    assert "google_place_id" in joined
    assert "CREATE TABLE IF NOT EXISTS google_enrichment_logs" in joined


def test_company_google_column_names_match_migration():
    sql = google_enrichment_migration_sql()
    for column in COMPANY_GOOGLE_COLUMN_NAMES:
        assert column in sql


def test_company_model_includes_google_enrichment_columns():
    column_names = {column.name for column in Company.__table__.columns}
    expected = set(COMPANY_GOOGLE_COLUMN_NAMES) | {
        "google_rating",
        "google_reviews_count",
        "google_address",
        "google_phone",
    }
    assert expected.issubset(column_names)


def test_log_and_review_models_map_to_tables():
    assert GoogleEnrichmentLog.__tablename__ == "google_enrichment_logs"
    assert GoogleEnrichmentReview.__tablename__ == "google_enrichment_reviews"
    log_columns = {column.name for column in GoogleEnrichmentLog.__table__.columns}
    assert {"company_id", "run_id", "status", "provider", "latency_ms"}.issubset(log_columns)


@pytest.fixture(scope="module")
def migrated_engine():
    from db.connection import init_db
    from tests.db_test_safety import require_local_test_database

    database_url = require_local_test_database()
    init_db()
    return create_engine(database_url)


def test_google_enrichment_columns_exist_after_init_db(migrated_engine):
    inspector = inspect(migrated_engine)
    company_columns = {col["name"] for col in inspector.get_columns("companies")}
    for column in COMPANY_GOOGLE_COLUMN_NAMES:
        assert column in company_columns, f"companies missing {column}"

    assert inspector.has_table("google_enrichment_logs")
    assert inspector.has_table("google_enrichment_reviews")

    log_columns = {col["name"] for col in inspector.get_columns("google_enrichment_logs")}
    assert "match_confidence" in log_columns
    assert "latency_ms" in log_columns
