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


def test_needs_ai_scoring_filter_includes_null_zero_and_empty_summary():
    clause = _needs_ai_scoring_filter(Tender)
    compiled = str(clause.compile(compile_kwargs={"literal_binds": True}))
    assert "ai_score IS NULL" in compiled or "ai_score IS" in compiled
    assert "ai_summary" in compiled

    commercial_clause = _needs_ai_scoring_filter(CommercialTender)
    assert commercial_clause is not None
