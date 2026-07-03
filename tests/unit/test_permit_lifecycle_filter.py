"""P3 — permit lifecycle filtering in consumer pipelines."""

from __future__ import annotations

import inspect
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy import select

from db.models import Permit
from db.permit_lifecycle_constants import apply_active_permit_filter, permit_lifecycle_eligible
from pipeline.competitive_intel import activity as ci_activity
from pipeline.early_signals import _collect_permit_signals
from pipeline.opportunity_discovery import CompanySignals, _load_permit_candidates


def _permit(*, is_active: bool, permit_id: int = 1):
    return SimpleNamespace(
        id=permit_id,
        is_active=is_active,
        lifecycle_status="stale" if not is_active else "active",
        applicant="Example Builder Ltd",
        permit_type="New Building",
        project_value="500000",
        address="1 Main St",
        description="Tower",
        application_date="2026-06-01",
        issue_date="2026-06-15",
        local_area="Downtown",
        city="Vancouver",
        source="vancouver",
        scraped_at=None,
        contractor="",
    )


class TestPermitLifecycleEligible:
    def test_active_included_by_default(self):
        assert permit_lifecycle_eligible(_permit(is_active=True)) is True

    def test_stale_excluded_by_default(self):
        assert permit_lifecycle_eligible(_permit(is_active=False)) is False

    def test_include_inactive_admits_stale(self):
        assert permit_lifecycle_eligible(_permit(is_active=False), include_inactive=True) is True


class TestApplyActivePermitFilter:
    def test_default_sql_includes_is_active(self):
        stmt = apply_active_permit_filter(select(Permit), include_inactive=False)
        sql = str(stmt.compile(dialect=postgresql.dialect())).lower()
        assert "is_active" in sql and ("= true" in sql or "is true" in sql)

    def test_include_inactive_omits_filter(self):
        stmt = apply_active_permit_filter(select(Permit), include_inactive=True)
        assert stmt.whereclause is None


class TestLoadPermitCandidates:
    @staticmethod
    def _signals() -> CompanySignals:
        return CompanySignals(
            name="Example Builder Ltd",
            normalized_name="example builder ltd",
            project_types=["New Building"],
            neighborhoods=[],
            google_address="",
            primary_city="",
            primary_address="",
            geographic_reach="",
            avg_project_value=500_000,
            avg_award_value=0,
            award_categories=[],
            award_clients=[],
            buyer_levels=[],
            ai_reliability_score=None,
        )

    @staticmethod
    def _scalars_result(items: list):
        result = MagicMock()
        result.all.return_value = items
        return result

    def test_default_applies_is_active_on_market_scan(self):
        session = MagicMock()
        active = _permit(is_active=True, permit_id=10)
        session.scalars.side_effect = [
            self._scalars_result([]),
            self._scalars_result([active]),
        ]

        rows = _load_permit_candidates(session, self._signals(), 5, include_closed=False)

        assert len(rows) == 1
        market_stmt = session.scalars.call_args_list[1].args[0]
        sql = str(market_stmt.compile(dialect=postgresql.dialect())).lower()
        assert "is_active" in sql

    def test_include_closed_omits_is_active_filter(self):
        session = MagicMock()
        stale = _permit(is_active=False, permit_id=11)
        session.scalars.side_effect = [
            self._scalars_result([]),
            self._scalars_result([stale]),
        ]

        rows = _load_permit_candidates(session, self._signals(), 5, include_closed=True)

        assert len(rows) == 1
        market_stmt = session.scalars.call_args_list[1].args[0]
        assert market_stmt.whereclause is None


class TestEarlySignalsPermitQuery:
    def test_collect_permit_signals_applies_is_active(self):
        session = MagicMock()
        session.scalars.return_value.all.return_value = []

        _collect_permit_signals(
            session,
            since="2026-06-01",
            signals_model=None,
            kind="construction",
            market_regions=[],
            market_project_types=[],
            min_value=None,
            max_value=None,
            fetch_limit=10,
        )

        stmt = session.scalars.call_args.args[0]
        sql = str(stmt.compile(dialect=postgresql.dialect())).lower()
        assert "is_active" in sql


class TestListPermitsApi:
    def test_default_filters_active_only(self):
        from fastapi.testclient import TestClient

        from api.main import app

        client = TestClient(app)
        with patch("api.main.get_session") as mock_get_session:
            session = MagicMock()
            mock_get_session.return_value = session
            session.scalar.return_value = 0
            session.scalars.return_value.all.return_value = []

            response = client.get("/api/permits?limit=10")

        assert response.status_code == 200
        count_stmt = session.scalar.call_args.args[0]
        sql = str(count_stmt.compile(dialect=postgresql.dialect())).lower()
        assert "is_active" in sql

    def test_include_inactive_skips_is_active_filter(self):
        from fastapi.testclient import TestClient

        from api.main import app

        client = TestClient(app)
        with patch("api.main.get_session") as mock_get_session:
            session = MagicMock()
            mock_get_session.return_value = session
            session.scalar.return_value = 111773
            session.scalars.return_value.all.return_value = []

            response = client.get("/api/permits?limit=10&include_inactive=true")

        assert response.status_code == 200
        count_stmt = session.scalar.call_args.args[0]
        assert count_stmt.whereclause is None


class TestCompetitiveIntelUnchanged:
    def test_permit_count_90d_source_has_no_is_active_filter(self):
        source = inspect.getsource(ci_activity.permit_count_90d)
        assert "is_active" not in source

    def test_build_activity_stats_source_has_no_is_active_filter(self):
        source = inspect.getsource(ci_activity.build_activity_stats)
        assert "is_active" not in source
