"""Mock-based control-flow tests for
pipeline.internal_steps.run_company_intelligence_step()'s telemetry
wiring (manual/n8n trigger path) -- verifies:

  - backward compatibility (no run_id, or telemetry flag off): the exact
    pre-instrumentation call shape, no telemetry call at all.
  - the manual path's pre-existing exception-propagation contract is
    preserved even with telemetry involved -- unlike the SCHEDULED path
    (pipeline/run.py), which deliberately swallows and continues, this
    function is used as the `worker` callable inside
    pipeline.runs._execute_tracked_worker and MUST let an exception
    propagate so pipeline_runs.status="failed" is set correctly.
  - telemetry start failing open (returns None) falls back to the exact
    no-on_phase call shape, and finish is never called for a run_id that
    was never actually started.
  - make_company_intelligence_worker's closure threads run_id through.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from pipeline.internal_steps import (
    make_company_intelligence_worker,
    run_company_intelligence_step,
)


def test_no_run_id_never_calls_telemetry_or_changes_the_call_shape():
    """Backward compatible: called exactly as before this instrumentation
    existed (no run_id kwarg at all, e.g. any hypothetical direct
    zero-arg caller) -- no telemetry call, no on_phase kwarg."""
    session = MagicMock()

    with patch("pipeline.internal_steps.get_session", return_value=session):
        with patch(
            "pipeline.internal_steps.run_company_intelligence", return_value={"x": 1}
        ) as run_mock:
            with patch(
                "pipeline.internal_steps.start_company_intelligence_telemetry"
            ) as start_mock:
                result = run_company_intelligence_step()

    assert result == {"x": 1}
    start_mock.assert_not_called()
    run_mock.assert_called_once_with(session)
    session.close.assert_called_once()


def test_run_id_given_but_flag_off_never_calls_telemetry():
    session = MagicMock()

    with patch("pipeline.internal_steps.get_session", return_value=session):
        with patch(
            "pipeline.internal_steps.company_intelligence_job_run_telemetry_enabled",
            return_value=False,
        ):
            with patch(
                "pipeline.internal_steps.run_company_intelligence",
                return_value={"x": 1},
            ) as run_mock:
                with patch(
                    "pipeline.internal_steps.start_company_intelligence_telemetry"
                ) as start_mock:
                    result = run_company_intelligence_step(run_id="some-run-id")

    assert result == {"x": 1}
    start_mock.assert_not_called()
    run_mock.assert_called_once_with(session)


def test_run_id_and_flag_on_success_starts_and_finishes_telemetry_with_correlation():
    session = MagicMock()

    with patch("pipeline.internal_steps.get_session", return_value=session):
        with patch(
            "pipeline.internal_steps.company_intelligence_job_run_telemetry_enabled",
            return_value=True,
        ):
            with patch(
                "pipeline.internal_steps.start_company_intelligence_telemetry",
                return_value="corr-run-id",
            ) as start_mock:
                with patch(
                    "pipeline.internal_steps.run_company_intelligence",
                    return_value={"companies_populated": 3},
                ) as run_mock:
                    with patch(
                        "pipeline.internal_steps.finish_company_intelligence_telemetry"
                    ) as finish_mock:
                        result = run_company_intelligence_step(run_id="corr-run-id")

    assert result == {"companies_populated": 3}
    start_mock.assert_called_once_with(trigger="manual", run_id="corr-run-id")
    # on_phase kwarg WAS passed this time (telemetry active).
    assert run_mock.call_args.args == (session,)
    assert "on_phase" in run_mock.call_args.kwargs
    finish_mock.assert_called_once_with(
        "corr-run-id", status="success", counts={"companies_populated": 3}
    )


def test_exception_still_propagates_with_telemetry_active_and_records_failure():
    """The critical contract test: unlike pipeline/run.py's scheduled
    path, this function must NOT swallow the exception -- it is the
    `worker` callable pipeline.runs._execute_tracked_worker depends on to
    mark pipeline_runs.status="failed"."""
    session = MagicMock()
    boom = RuntimeError("cursor already closed")

    with patch("pipeline.internal_steps.get_session", return_value=session):
        with patch(
            "pipeline.internal_steps.company_intelligence_job_run_telemetry_enabled",
            return_value=True,
        ):
            with patch(
                "pipeline.internal_steps.start_company_intelligence_telemetry",
                return_value="corr-run-id",
            ):
                with patch(
                    "pipeline.internal_steps.run_company_intelligence", side_effect=boom
                ):
                    with patch(
                        "pipeline.internal_steps.finish_company_intelligence_telemetry"
                    ) as finish_mock:
                        with pytest.raises(RuntimeError, match="cursor already closed"):
                            run_company_intelligence_step(run_id="corr-run-id")

    finish_mock.assert_called_once_with(
        "corr-run-id", status="failed", raw_error="cursor already closed"
    )
    session.close.assert_called_once()


def test_telemetry_start_failing_open_falls_back_to_no_on_phase_call():
    """start_company_intelligence_telemetry() returning None (fail-open,
    e.g. a duplicate start or a telemetry-session failure) must fall back
    to the exact no-telemetry call shape -- no on_phase kwarg -- and must
    never call finish for a run_id that was never actually started."""
    session = MagicMock()

    with patch("pipeline.internal_steps.get_session", return_value=session):
        with patch(
            "pipeline.internal_steps.company_intelligence_job_run_telemetry_enabled",
            return_value=True,
        ):
            with patch(
                "pipeline.internal_steps.start_company_intelligence_telemetry",
                return_value=None,
            ):
                with patch(
                    "pipeline.internal_steps.run_company_intelligence",
                    return_value={"x": 1},
                ) as run_mock:
                    with patch(
                        "pipeline.internal_steps.finish_company_intelligence_telemetry"
                    ) as finish_mock:
                        result = run_company_intelligence_step(run_id="corr-run-id")

    assert result == {"x": 1}
    run_mock.assert_called_once_with(session)
    finish_mock.assert_not_called()


def test_telemetry_start_failing_open_on_exception_still_propagates_without_finish():
    session = MagicMock()
    boom = RuntimeError("boom")

    with patch("pipeline.internal_steps.get_session", return_value=session):
        with patch(
            "pipeline.internal_steps.company_intelligence_job_run_telemetry_enabled",
            return_value=True,
        ):
            with patch(
                "pipeline.internal_steps.start_company_intelligence_telemetry",
                return_value=None,
            ):
                with patch(
                    "pipeline.internal_steps.run_company_intelligence", side_effect=boom
                ):
                    with patch(
                        "pipeline.internal_steps.finish_company_intelligence_telemetry"
                    ) as finish_mock:
                        with pytest.raises(RuntimeError, match="boom"):
                            run_company_intelligence_step(run_id="corr-run-id")

    finish_mock.assert_not_called()


def test_make_company_intelligence_worker_threads_run_id_through():
    with patch(
        "pipeline.internal_steps.run_company_intelligence_step",
        return_value={"ok": True},
    ) as step_mock:
        worker = make_company_intelligence_worker("worker-run-id")
        result = worker()

    assert result == {"ok": True}
    step_mock.assert_called_once_with(run_id="worker-run-id")
