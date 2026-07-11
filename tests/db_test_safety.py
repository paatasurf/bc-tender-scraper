"""Test-suite database safety — unit/integration tests must never write to production."""

from __future__ import annotations

import os

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from config.env import get_env, load_app_env
from db.db_safety import is_production_database_url

_REFUSAL_MESSAGE = (
    "Refusing test DB write: DATABASE_URL resolves to production. "
    "Point DATABASE_URL at local Postgres (.env.local). "
    "Production writes require explicit Class C/D scripts with --allow-production."
)


def _ci_skips_db_integration() -> bool:
    """GitHub Actions sets CI=true; Phase 1 CI has no Postgres service."""
    if os.environ.get("CI", "").strip().lower() not in {"1", "true", "yes"}:
        return False
    # Opt-in local/CI Postgres for future jobs.
    return not bool(os.environ.get("CI_DATABASE_URL", "").strip())


def require_local_test_database() -> str:
    """Return DATABASE_URL after refusing production hosts."""
    if _ci_skips_db_integration():
        pytest.skip("DB integration tests skipped on CI (set CI_DATABASE_URL to enable)")
    load_app_env()
    url = get_env("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not configured")
    if is_production_database_url(url):
        pytest.fail(_REFUSAL_MESSAGE)
    return url


def teardown_test_company(session: Session, company_id: int) -> None:
    """Remove rows created by integration tests (registry links, score history, company)."""
    session.execute(
        text("DELETE FROM company_registry_links WHERE company_id = :id"),
        {"id": company_id},
    )
    session.execute(
        text("DELETE FROM company_score_history WHERE company_id = :id"),
        {"id": company_id},
    )
    session.execute(text("DELETE FROM companies WHERE id = :id"), {"id": company_id})
    session.commit()


def teardown_odbus_test_fixture(session: Session, *, odbus_idx: str = "t1-1") -> None:
    """Remove ODB integration-test fixture rows (registry link + reference)."""
    session.execute(
        text(
            "DELETE FROM company_registry_links WHERE source = 'odbus' AND external_id = :idx"
        ),
        {"idx": odbus_idx},
    )
    session.execute(
        text("DELETE FROM odbus_reference WHERE odbus_idx = :idx"),
        {"idx": odbus_idx},
    )
    session.commit()
