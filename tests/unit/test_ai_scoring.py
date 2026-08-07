"""Unit tests for AI scoring batch limits and return payload."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from pipeline.ai_scoring import (
    DEFAULT_AI_BATCH_LIMIT,
    _needs_ai_scoring_filter,
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
                side_effect=[(1, 1), (2, 2), (0, 0), (0, 0)],
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
    assert result["total_tenders_attempted"] == 3
    assert result["partial_failure"] is False


def test_score_unscored_tenders_flags_partial_failure_when_all_attempts_fail():
    """The exact regression this PR fixes: a non-empty backlog existed
    and rows were fetched (attempted > 0) but every _score_table call
    scored zero -- pipeline_runs must not report this as plain success."""
    session = MagicMock()
    session.scalar.return_value = 10

    with patch("pipeline.ai_scoring.get_anthropic_api_key", return_value="test-key"):
        with patch("pipeline.ai_scoring.anthropic.Anthropic"):
            with patch(
                "pipeline.ai_scoring._score_table",
                side_effect=[(0, 1), (0, 10), (0, 0), (0, 0)],
            ):
                with patch(
                    "pipeline.ai_scoring._estimate_budgets_table",
                    side_effect=[0, 0, 0, 0],
                ):
                    result = score_unscored_tenders(session)

    assert result["total_tenders_scored"] == 0
    assert result["total_tenders_attempted"] == 11
    assert result["partial_failure"] is True


def test_score_unscored_tenders_no_partial_failure_when_nothing_to_do():
    """An empty backlog (attempted == 0 everywhere) is genuinely nothing
    to score -- must not be flagged as a failure."""
    session = MagicMock()
    session.scalar.return_value = 0

    with patch("pipeline.ai_scoring.get_anthropic_api_key", return_value="test-key"):
        with patch("pipeline.ai_scoring.anthropic.Anthropic"):
            with patch(
                "pipeline.ai_scoring._score_table",
                side_effect=[(0, 0), (0, 0), (0, 0), (0, 0)],
            ):
                with patch(
                    "pipeline.ai_scoring._estimate_budgets_table",
                    side_effect=[0, 0, 0, 0],
                ):
                    result = score_unscored_tenders(session)

    assert result["total_tenders_attempted"] == 0
    assert result["partial_failure"] is False


def test_score_unscored_tenders_missing_api_key_reports_no_partial_failure():
    session = MagicMock()
    with patch("pipeline.ai_scoring.get_anthropic_api_key", return_value=""):
        result = score_unscored_tenders(session)

    assert result["total_tenders_attempted"] == 0
    assert result["partial_failure"] is False
    assert result["backlog"] == {}


def test_needs_ai_scoring_filter_includes_null_zero_and_empty_summary():
    clause = _needs_ai_scoring_filter(Tender)
    compiled = str(clause.compile(compile_kwargs={"literal_binds": True}))
    assert "ai_score IS NULL" in compiled or "ai_score IS" in compiled
    assert "ai_summary" in compiled

    commercial_clause = _needs_ai_scoring_filter(CommercialTender)
    assert commercial_clause is not None
