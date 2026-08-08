"""Tests for pipeline/run.py's M3D-A ai_scoring ops_job_run telemetry
wiring -- the scheduled path only (pipeline.run.run_pipeline()). Manual/
n8n endpoints (/internal/ai-scoring, /api/pipeline/run-ai-scoring) are out
of scope for M3D-A and are not touched by this module.

Every test here mocks run_tender_data_pipeline/run_company_intelligence/
run_arch_company_intelligence so only the ai_scoring block under test
actually executes real code.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import pipeline.run as run_module


def _patch_other_steps():
    """Context-manager-free helper: patches everything in run_pipeline()
    other than the ai_scoring block, so each test only exercises that
    block."""
    return (
        patch(
            "pipeline.run.run_tender_data_pipeline",
            return_value={"status": "success"},
        ),
        patch("pipeline.run.run_company_intelligence", return_value={}),
        patch("pipeline.run.run_arch_company_intelligence", return_value={}),
        patch("pipeline.run.init_db"),
    )


class _Ctx:
    """Applies a list of already-constructed patch() context managers."""

    def __init__(self, patches):
        self._patches = patches

    def __enter__(self):
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *exc_info):
        for p in reversed(self._patches):
            p.stop()


def _run_pipeline_with(session, **more_patches):
    patches = list(_patch_other_steps())
    patches.append(patch("pipeline.run.get_session", return_value=session))
    for target, value in more_patches.items():
        patches.append(patch(target, value))
    with _Ctx(patches):
        return run_module.run_pipeline()


# ---------------------------------------------------------------------
# flag=false -- byte-equivalent call path, no telemetry writer touched
# ---------------------------------------------------------------------


def test_flag_false_calls_score_unscored_tenders_with_no_on_phase_kwarg(monkeypatch):
    monkeypatch.delenv(run_module.AI_SCORING_JOB_RUN_TELEMETRY_FLAG, raising=False)
    session = MagicMock()
    captured_kwargs = {}

    def fake_score(_session, **kwargs):
        captured_kwargs.update(kwargs)
        return {"total_tenders_scored": 0, "total_tenders_budgeted": 0}

    exit_code = _run_pipeline_with(
        session, **{"pipeline.run.score_unscored_tenders": fake_score}
    )

    assert exit_code == 0
    assert captured_kwargs == {}  # no on_phase kwarg passed at all


def test_flag_false_calls_no_telemetry_writer_function_at_all(monkeypatch):
    monkeypatch.delenv(run_module.AI_SCORING_JOB_RUN_TELEMETRY_FLAG, raising=False)
    session = MagicMock()
    start_mock = MagicMock()
    finish_mock = MagicMock()

    patches = list(_patch_other_steps())
    patches.append(patch("pipeline.run.get_session", return_value=session))
    patches.append(patch("pipeline.run._ai_scoring_telemetry_start", start_mock))
    patches.append(patch("pipeline.run._ai_scoring_telemetry_finish", finish_mock))
    patches.append(
        patch(
            "pipeline.run.score_unscored_tenders",
            return_value={"total_tenders_scored": 5, "total_tenders_budgeted": 0},
        )
    )
    with _Ctx(patches):
        run_module.run_pipeline()

    start_mock.assert_not_called()
    finish_mock.assert_not_called()


def test_flag_explicitly_false_also_calls_no_writer(monkeypatch):
    monkeypatch.setenv(run_module.AI_SCORING_JOB_RUN_TELEMETRY_FLAG, "false")
    session = MagicMock()
    start_mock = MagicMock()

    patches = list(_patch_other_steps())
    patches.append(patch("pipeline.run.get_session", return_value=session))
    patches.append(patch("pipeline.run._ai_scoring_telemetry_start", start_mock))
    patches.append(
        patch(
            "pipeline.run.score_unscored_tenders",
            return_value={"total_tenders_scored": 0, "total_tenders_budgeted": 0},
        )
    )
    with _Ctx(patches):
        run_module.run_pipeline()

    start_mock.assert_not_called()


def test_pipeline_skip_ai_scoring_flag_takes_priority_never_touches_telemetry(
    monkeypatch,
):
    """PIPELINE_SKIP_AI_SCORING=true must skip AI scoring (and its
    telemetry) entirely, regardless of ENABLE_AI_SCORING_JOB_RUN_TELEMETRY."""
    monkeypatch.setenv("PIPELINE_SKIP_AI_SCORING", "true")
    monkeypatch.setenv(run_module.AI_SCORING_JOB_RUN_TELEMETRY_FLAG, "true")
    session = MagicMock()
    start_mock = MagicMock()
    score_mock = MagicMock()

    patches = list(_patch_other_steps())
    patches.append(patch("pipeline.run.get_session", return_value=session))
    patches.append(patch("pipeline.run._ai_scoring_telemetry_start", start_mock))
    patches.append(patch("pipeline.run.score_unscored_tenders", score_mock))
    with _Ctx(patches):
        run_module.run_pipeline()

    start_mock.assert_not_called()
    score_mock.assert_not_called()


# ---------------------------------------------------------------------
# flag=true -- success: one start, 8 step events/heartbeats, one finish
# ---------------------------------------------------------------------


def test_flag_true_success_records_start_8_phases_and_finish_success(monkeypatch):
    monkeypatch.setenv(run_module.AI_SCORING_JOB_RUN_TELEMETRY_FLAG, "true")
    session = MagicMock()

    phases_recorded: list[str] = []

    def fake_phase(run_id, phase):
        assert run_id == "run-123"
        phases_recorded.append(phase)

    def fake_score(_session, *, on_phase=None):
        assert on_phase is not None
        for phase in (
            "score_federal_gov",
            "score_merx",
            "score_arch",
            "score_bidcentral",
            "budget_federal_gov",
            "budget_merx",
            "budget_arch",
            "budget_bidcentral",
        ):
            on_phase(phase)
        return {
            "total_tenders_scored": 3,
            "total_tenders_budgeted": 1,
            "federal_gov_tenders_scored": 1,
            "backlog": {"federal_gov": 2, "merx_provincial": 0},
        }

    finish_mock = MagicMock()
    patches = list(_patch_other_steps())
    patches.append(patch("pipeline.run.get_session", return_value=session))
    patches.append(
        patch("pipeline.run._ai_scoring_telemetry_start", return_value="run-123")
    )
    patches.append(patch("pipeline.run._ai_scoring_telemetry_phase", fake_phase))
    patches.append(patch("pipeline.run._ai_scoring_telemetry_finish", finish_mock))
    patches.append(patch("pipeline.run.score_unscored_tenders", fake_score))
    with _Ctx(patches):
        exit_code = run_module.run_pipeline()

    assert exit_code == 0
    assert phases_recorded == [
        "score_federal_gov",
        "score_merx",
        "score_arch",
        "score_bidcentral",
        "budget_federal_gov",
        "budget_merx",
        "budget_arch",
        "budget_bidcentral",
    ]
    finish_mock.assert_called_once_with(
        "run-123",
        status="success",
        counts={
            "total_tenders_scored": 3,
            "total_tenders_budgeted": 1,
            "federal_gov_tenders_scored": 1,
            "backlog_federal_gov": 2,
            "backlog_merx_provincial": 0,
        },
    )


# ---------------------------------------------------------------------
# flag=true -- failed: finish failed, original exception unchanged
# ---------------------------------------------------------------------


def test_flag_true_exception_from_scoring_maps_to_failed_and_still_propagates(
    monkeypatch,
):
    monkeypatch.setenv(run_module.AI_SCORING_JOB_RUN_TELEMETRY_FLAG, "true")
    session = MagicMock()

    def raising_score(_session, *, on_phase=None):
        raise RuntimeError("boom: sk_live_should_never_leak")

    finish_mock = MagicMock()
    patches = list(_patch_other_steps())
    patches.append(patch("pipeline.run.get_session", return_value=session))
    patches.append(
        patch("pipeline.run._ai_scoring_telemetry_start", return_value="run-456")
    )
    patches.append(patch("pipeline.run._ai_scoring_telemetry_phase", MagicMock()))
    patches.append(patch("pipeline.run._ai_scoring_telemetry_finish", finish_mock))
    patches.append(patch("pipeline.run.score_unscored_tenders", raising_score))
    with _Ctx(patches):
        with pytest.raises(RuntimeError, match="boom: sk_live_should_never_leak"):
            run_module.run_pipeline()

    finish_mock.assert_called_once_with(
        "run-456", status="failed", raw_error="boom: sk_live_should_never_leak"
    )
    # session.close() still happens even though the exception propagated.
    session.close.assert_called_once()


def test_flag_true_but_start_failed_still_calls_score_unscored_tenders_without_on_phase(
    monkeypatch,
):
    """Fail-open: if _ai_scoring_telemetry_start() itself returns None
    (its own get_session()/start_job_run() failed), the real work must
    still run, with the exact pre-M3D-A call signature (no on_phase),
    and no finish call is attempted (there is no run_id to finish)."""
    monkeypatch.setenv(run_module.AI_SCORING_JOB_RUN_TELEMETRY_FLAG, "true")
    session = MagicMock()
    captured_kwargs = {}

    def fake_score(_session, **kwargs):
        captured_kwargs.update(kwargs)
        return {"total_tenders_scored": 0, "total_tenders_budgeted": 0}

    finish_mock = MagicMock()
    patches = list(_patch_other_steps())
    patches.append(patch("pipeline.run.get_session", return_value=session))
    patches.append(patch("pipeline.run._ai_scoring_telemetry_start", return_value=None))
    patches.append(patch("pipeline.run._ai_scoring_telemetry_finish", finish_mock))
    patches.append(patch("pipeline.run.score_unscored_tenders", fake_score))
    with _Ctx(patches):
        exit_code = run_module.run_pipeline()

    assert exit_code == 0
    assert captured_kwargs == {}
    finish_mock.assert_not_called()


# ---------------------------------------------------------------------
# fail-open at every telemetry boundary, including get_session() itself
# ---------------------------------------------------------------------


def test_start_get_session_failure_still_runs_scoring_exactly_once(monkeypatch, caplog):
    monkeypatch.setenv(run_module.AI_SCORING_JOB_RUN_TELEMETRY_FLAG, "true")
    session = MagicMock()
    score_calls = []

    def fake_score(_session, **kwargs):
        score_calls.append(kwargs)
        return {"total_tenders_scored": 0, "total_tenders_budgeted": 0}

    patches = list(_patch_other_steps())
    patches.append(patch("pipeline.run.get_session", return_value=session))
    # The REAL _ai_scoring_telemetry_start runs, but its own internal
    # get_session() (a separate telemetry session, via
    # pipeline.job_run_telemetry.call_with_telemetry_session) raises.
    patches.append(
        patch(
            "db.connection.get_session", MagicMock(side_effect=RuntimeError("db down"))
        )
    )
    patches.append(patch("pipeline.run.score_unscored_tenders", fake_score))
    with _Ctx(patches):
        with caplog.at_level("WARNING"):
            exit_code = run_module.run_pipeline()

    assert exit_code == 0
    assert len(score_calls) == 1
    assert (
        score_calls[0] == {}
    )  # no on_phase -- start failed, so telemetry_run_id is None
    assert "db down" not in caplog.text
    assert "AI scoring telemetry: failed to start job run tracking" in caplog.text


def test_phase_get_session_failure_still_runs_scoring_once_and_finishes_success(
    monkeypatch, caplog
):
    monkeypatch.setenv(run_module.AI_SCORING_JOB_RUN_TELEMETRY_FLAG, "true")
    session = MagicMock()

    call_count = {"n": 0}
    real_get_session = MagicMock()

    def flaky_get_session():
        call_count["n"] += 1
        # First call: _ai_scoring_telemetry_start's own session -- succeeds.
        if call_count["n"] == 1:
            return real_get_session
        # Every phase call's own session -- raises.
        raise RuntimeError("db down")

    def fake_score(_session, *, on_phase=None):
        on_phase("score_federal_gov")
        on_phase("score_merx")
        return {"total_tenders_scored": 1, "total_tenders_budgeted": 0}

    patches = list(_patch_other_steps())
    patches.append(patch("pipeline.run.get_session", return_value=session))
    patches.append(patch("pipeline.run.start_job_run", return_value="run-789"))
    finish_mock = MagicMock()
    patches.append(patch("pipeline.run.finish_job_run", finish_mock))
    patches.append(patch("db.connection.get_session", flaky_get_session))
    patches.append(patch("pipeline.run.score_unscored_tenders", fake_score))
    with _Ctx(patches):
        with caplog.at_level("WARNING"):
            exit_code = run_module.run_pipeline()

    assert exit_code == 0
    assert "db down" not in caplog.text
    assert (
        "AI scoring telemetry: failed to record phase=score_federal_gov" in caplog.text
    )
    assert "AI scoring telemetry: failed to record phase=score_merx" in caplog.text
    # finish still ran (its own get_session call is the 4th overall: start,
    # phase x2, finish) -- but since flaky_get_session always raises after
    # call 1, finish's own get_session also raises, so finish_job_run
    # itself is never actually called either -- just proving the whole
    # chain stayed fail-open and scoring still completed successfully.
    finish_mock.assert_not_called()


def test_finish_get_session_failure_still_runs_scoring_once(monkeypatch, caplog):
    monkeypatch.setenv(run_module.AI_SCORING_JOB_RUN_TELEMETRY_FLAG, "true")
    session = MagicMock()

    def fake_score(_session, *, on_phase=None):
        return {"total_tenders_scored": 1, "total_tenders_budgeted": 0}

    patches = list(_patch_other_steps())
    patches.append(patch("pipeline.run.get_session", return_value=session))
    patches.append(
        patch("pipeline.run._ai_scoring_telemetry_start", return_value="run-999")
    )
    patches.append(
        patch(
            "db.connection.get_session", MagicMock(side_effect=RuntimeError("db down"))
        )
    )
    patches.append(patch("pipeline.run.score_unscored_tenders", fake_score))
    with _Ctx(patches):
        with caplog.at_level("WARNING"):
            exit_code = run_module.run_pipeline()

    assert exit_code == 0
    assert "db down" not in caplog.text
    assert "AI scoring telemetry: failed to finish job run tracking" in caplog.text
