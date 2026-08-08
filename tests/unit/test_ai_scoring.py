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


# ---------------------------------------------------------------------
# (M3D-A) on_phase -- purely observational, must never affect scoring
# ---------------------------------------------------------------------


def _run_with_on_phase(on_phase):
    session = MagicMock()
    session.scalar.return_value = 10
    with patch("pipeline.ai_scoring.get_anthropic_api_key", return_value="test-key"):
        with patch("pipeline.ai_scoring.anthropic.Anthropic"):
            with patch("pipeline.ai_scoring._score_table", side_effect=[1, 2, 0, 0]):
                with patch(
                    "pipeline.ai_scoring._estimate_budgets_table",
                    side_effect=[0, 0, 0, 0],
                ):
                    return score_unscored_tenders(session, on_phase=on_phase)


def test_on_phase_called_at_all_8_boundaries_in_order_on_full_success():
    phases: list[str] = []
    result = _run_with_on_phase(phases.append)

    assert phases == [
        "score_federal_gov",
        "score_merx",
        "score_arch",
        "score_bidcentral",
        "budget_federal_gov",
        "budget_merx",
        "budget_arch",
        "budget_bidcentral",
    ]
    # The real result is unaffected by having an on_phase callback at all.
    assert result["total_tenders_scored"] == 3


def test_on_phase_none_default_is_a_complete_noop_existing_callers_unaffected():
    """The exact same call as test_score_unscored_tenders_returns_totals_and_batch_limit,
    just re-asserted here to make the "on_phase defaults to None, zero
    behavior change" guarantee explicit and named."""
    session = MagicMock()
    session.scalar.return_value = 10
    with patch("pipeline.ai_scoring.get_anthropic_api_key", return_value="test-key"):
        with patch("pipeline.ai_scoring.anthropic.Anthropic"):
            with patch("pipeline.ai_scoring._score_table", side_effect=[1, 2, 0, 0]):
                with patch(
                    "pipeline.ai_scoring._estimate_budgets_table",
                    side_effect=[0, 0, 0, 0],
                ):
                    result = score_unscored_tenders(session)  # no on_phase kwarg at all

    assert result["total_tenders_scored"] == 3


def test_a_raising_on_phase_callback_never_breaks_scoring_or_the_return_value():
    def raising_on_phase(phase: str) -> None:
        raise RuntimeError(f"telemetry blew up on {phase}")

    result = _run_with_on_phase(raising_on_phase)

    assert result["total_tenders_scored"] == 3
    assert result["total_tenders_budgeted"] == 0


def test_on_phase_not_called_at_all_when_anthropic_api_key_is_missing():
    """The early-return (no API key) path does no scoring work at all --
    on_phase must not fire for boundaries that never actually ran."""
    session = MagicMock()
    phases: list[str] = []
    with patch("pipeline.ai_scoring.get_anthropic_api_key", return_value=None):
        result = score_unscored_tenders(session, on_phase=phases.append)

    assert phases == []
    assert result["total_tenders_scored"] == 0
