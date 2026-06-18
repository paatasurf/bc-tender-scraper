"""Unit tests for AI scoring batch limits and return payload."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from pipeline.ai_scoring import DEFAULT_AI_BATCH_LIMIT, score_unscored_tenders


def test_score_unscored_tenders_returns_totals_and_batch_limit():
    session = MagicMock()
    session.scalar.return_value = 10

    with patch("pipeline.ai_scoring.get_anthropic_api_key", return_value="test-key"):
        with patch("pipeline.ai_scoring.anthropic.Anthropic"):
            with patch("pipeline.ai_scoring._score_table", side_effect=[2, 1, 0]):
                with patch("pipeline.ai_scoring._estimate_budgets_table", side_effect=[1, 0, 0]):
                    result = score_unscored_tenders(session)

    assert result["total_tenders_scored"] == 3
    assert result["total_tenders_budgeted"] == 1
    assert result["batch_limit_per_table"] == DEFAULT_AI_BATCH_LIMIT
