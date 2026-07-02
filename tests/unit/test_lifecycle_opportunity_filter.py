"""P2-07 Step 2 — lifecycle filtering in opportunity pipelines."""

from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.dialects import postgresql

from db.lifecycle_constants import LIFECYCLE_STATUS_CLOSING_SOON
from db.models import CommercialTender, Tender
from pipeline.market_normalizer import (
    _open_tender_query,
    deadline_is_open,
    tender_lifecycle_eligible,
)
from pipeline.opportunity_discovery import (
    _load_tender_candidates,
    _scan_construction_rule_tenders_from_rows,
    discover_opportunities,
)
from pipeline.unified_opportunities import get_unified_opportunities


def _row(*, is_open: bool, deadline: str = "", closing_date: str = ""):
    return SimpleNamespace(
        id=1,
        is_open=is_open,
        lifecycle_status=LIFECYCLE_STATUS_CLOSING_SOON if is_open else "closed",
        deadline=deadline,
        closing_date=closing_date or deadline,
        title="Sample tender",
        organization="City of Vancouver",
        company="City of Vancouver",
        category="Construction",
        estimated_value="1000000",
        value="1000000",
        location="Vancouver",
        url="",
    )


class TestDeadlineIsOpen:
    def test_empty_deadline_treated_as_open(self):
        assert deadline_is_open("") is True

    def test_future_deadline_open(self):
        future = (date.today() + timedelta(days=14)).isoformat()
        assert deadline_is_open(future) is True

    def test_past_deadline_closed(self):
        past = (date.today() - timedelta(days=1)).isoformat()
        assert deadline_is_open(past) is False


class TestTenderLifecycleEligible:
    def test_closed_lifecycle_excluded_by_default(self):
        row = _row(is_open=False, deadline="")
        assert tender_lifecycle_eligible(row, "", include_closed=False) is False

    def test_include_closed_allows_lifecycle_closed_with_open_deadline(self):
        row = _row(is_open=False, deadline="")
        assert tender_lifecycle_eligible(row, "", include_closed=True) is True

    def test_closing_soon_included_when_open(self):
        row = _row(is_open=True, deadline=(date.today() + timedelta(days=3)).isoformat())
        assert row.lifecycle_status == LIFECYCLE_STATUS_CLOSING_SOON
        assert tender_lifecycle_eligible(row, row.deadline, include_closed=False) is True

    def test_both_checks_required_past_deadline_excluded(self):
        past = (date.today() - timedelta(days=2)).isoformat()
        row = _row(is_open=True, deadline=past)
        assert tender_lifecycle_eligible(row, past, include_closed=False) is False


class TestOpenTenderQuery:
    def test_default_sql_includes_is_open_filter(self):
        stmt = _open_tender_query(Tender, 100, include_closed=False)
        sql = str(stmt.compile(dialect=postgresql.dialect())).lower()
        assert "is_open" in sql and ("= true" in sql or "is true" in sql)

    def test_include_closed_sql_omits_is_open_filter(self):
        stmt = _open_tender_query(Tender, 100, include_closed=True)
        assert stmt.whereclause is None


class TestLoadTenderCandidates:
    @staticmethod
    def _scalars_result(items: list):
        result = MagicMock()
        result.all.return_value = items
        return result

    def test_construction_default_applies_is_open_on_both_tables(self):
        session = MagicMock()
        open_row = _row(is_open=True, deadline=(date.today() + timedelta(days=7)).isoformat())
        session.scalars.side_effect = [
            self._scalars_result([open_row]),
            self._scalars_result([]),
        ]

        rows = _load_tender_candidates(session, "construction", 50, include_closed=False)

        assert len(rows) == 1
        federal_stmt = session.scalars.call_args_list[0].args[0]
        commercial_stmt = session.scalars.call_args_list[1].args[0]
        federal_sql = str(federal_stmt.compile(dialect=postgresql.dialect())).lower()
        commercial_sql = str(commercial_stmt.compile(dialect=postgresql.dialect())).lower()
        assert "is_open" in federal_sql and ("= true" in federal_sql or "is true" in federal_sql)
        assert "is_open" in commercial_sql and ("= true" in commercial_sql or "is true" in commercial_sql)

    def test_include_closed_skips_is_open_sql_filter(self):
        session = MagicMock()
        closed_row = _row(is_open=False, deadline="")
        session.scalars.side_effect = [
            self._scalars_result([closed_row]),
            self._scalars_result([]),
        ]

        rows = _load_tender_candidates(session, "construction", 50, include_closed=True)

        assert len(rows) == 1
        federal_stmt = session.scalars.call_args_list[0].args[0]
        assert federal_stmt.whereclause is None


class TestRuleScanBeltAndSuspenders:
    def test_scan_excludes_lifecycle_closed_even_if_loaded(self):
        signals = MagicMock()
        open_row = _row(is_open=True, deadline=(date.today() + timedelta(days=5)).isoformat())
        closed_row = _row(is_open=False, deadline="")
        tender_rows = [(open_row, "federal"), (closed_row, "commercial")]

        with patch(
            "pipeline.opportunity_discovery._score_construction_tender_rules",
            return_value=(80, ["fit"]),
        ), patch(
            "pipeline.opportunity_discovery._tender_payload",
            side_effect=lambda row, source: {"id": row.id, "title": row.title, "company": "X", "value": 1, "deadline": row.deadline, "category": "Construction"},
        ):
            results = _scan_construction_rule_tenders_from_rows(
                tender_rows, signals, include_closed=False
            )

        assert len(results) == 1
        assert results[0].tender_id == open_row.id


@patch("pipeline.unified_opportunities.recommend_bd_intelligence")
@patch("pipeline.unified_opportunities.discover_opportunities")
def test_unified_forwards_include_closed(mock_discover, mock_bd):
    mock_discover.return_value = {"matches": [], "ranking_model": "test"}
    mock_bd.return_value = {"active_opportunities": {"items": []}, "engine_version": "v3"}

    get_unified_opportunities(
        MagicMock(),
        company_id=8638,
        include_closed=True,
    )

    mock_discover.assert_called_once_with(
        company_id=8638,
        kind="construction",
        min_score=0,
        limit=50,
        include_closed=True,
    )
    mock_bd.assert_called_once()
    assert mock_bd.call_args.kwargs["include_closed"] is True


@patch("pipeline.opportunity_discovery._discover_construction_opportunities")
def test_discover_opportunities_threads_include_closed(mock_discover):
    mock_discover.return_value = {"matches": []}
    discover_opportunities(company_id=8638, kind="construction", include_closed=True)
    mock_discover.assert_called_once()
    assert mock_discover.call_args.kwargs["include_closed"] is True
