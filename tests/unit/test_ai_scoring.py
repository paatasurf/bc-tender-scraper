"""Unit tests for AI scoring batch limits and return payload."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from pipeline.ai_scoring import (
    DEFAULT_AI_BATCH_LIMIT,
    _ScoringRunBudget,
    _needs_ai_scoring_filter,
    _score_table,
    score_unscored_tenders,
)
from db.models import CommercialTender, Tender


def test_score_unscored_tenders_returns_totals_and_batch_limit():
    session = MagicMock()
    session.scalar.return_value = 10

    with patch("pipeline.ai_scoring.get_anthropic_api_key", return_value="test-key"):
        with patch("pipeline.ai_scoring.anthropic.Anthropic"):
            with patch(
                "pipeline.ai_scoring._score_table",
                side_effect=[1, 2, 0, 0],
            ):
                with patch(
                    "pipeline.ai_scoring._estimate_budgets_table",
                    side_effect=[0, 0, 0, 0],
                ):
                    result = score_unscored_tenders(session)

    assert result["federal_gov_tenders_scored"] == 1
    assert result["merx_tenders_scored"] == 2
    assert result["commercial_tenders_scored"] == 2
    assert result["total_tenders_scored"] == 3
    assert result["batch_limit_per_table"] == DEFAULT_AI_BATCH_LIMIT
    assert "backlog" in result


def test_score_unscored_tenders_configures_anthropic_timeout():
    session = MagicMock()
    session.scalar.return_value = 0

    with patch.dict("os.environ", {"ANTHROPIC_TIMEOUT_SECONDS": "12"}, clear=False):
        with patch("pipeline.ai_scoring.get_anthropic_api_key", return_value="test-key"):
            with patch("pipeline.ai_scoring.anthropic.Anthropic") as client_factory:
                with patch("pipeline.ai_scoring._score_table", return_value=0):
                    with patch("pipeline.ai_scoring._estimate_budgets_table", return_value=0):
                        result = score_unscored_tenders(session, time_budget_seconds=120)

    client_factory.assert_called_once_with(api_key="test-key", timeout=12.0)
    assert result["time_budget_seconds"] == 120
    assert result["time_budget_exhausted"] is False


def test_score_table_stops_before_request_when_time_budget_is_too_low():
    session = MagicMock()
    session.scalar.return_value = 1
    tender = SimpleNamespace(title="Time budget test tender")
    rows = MagicMock()
    rows.all.return_value = [tender]
    session.scalars.return_value = rows
    budget = _ScoringRunBudget(limit_seconds=10.0, started_at=0.0)

    with patch("pipeline.ai_scoring.time.monotonic", return_value=9.0):
        with patch("pipeline.ai_scoring._score_tender") as score_tender:
            scored = _score_table(
                session,
                MagicMock(),
                Tender,
                budget=budget,
                request_reserve_seconds=2.0,
            )

    assert scored == 0
    assert budget.exhausted is True
    score_tender.assert_not_called()
    session.commit.assert_not_called()


def test_needs_ai_scoring_filter_includes_null_zero_and_empty_summary():
    clause = _needs_ai_scoring_filter(Tender)
    compiled = str(clause.compile(compile_kwargs={"literal_binds": True}))
    assert "ai_score IS NULL" in compiled or "ai_score IS" in compiled
    assert "ai_summary" in compiled

    commercial_clause = _needs_ai_scoring_filter(CommercialTender)
    assert commercial_clause is not None
