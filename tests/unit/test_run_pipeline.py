"""Tests for pipeline/run.py's M3D-A ai_scoring, M3D-B
company_intelligence, and M3D-C arch_company_intelligence ops_job_run
telemetry wiring -- the scheduled path only (pipeline.run.run_pipeline()).
Manual/n8n endpoints (/internal/ai-scoring, /api/pipeline/run-ai-scoring,
/internal/arch-company-intelligence) are out of scope for all three and
are not touched by this module.

Every test here mocks run_tender_data_pipeline/run_company_intelligence/
run_arch_company_intelligence (or score_unscored_tenders, for the M3D-B
and M3D-C sections) so only the block under test actually executes real
code. The M3D-C section additionally tests pipeline/run.py's own
partial_failure-detection logic (the "did any phase end in _failed"
tracking) via a stubbed run_arch_company_intelligence that calls its
on_phase argument directly -- run_arch_company_intelligence()'s own
step-by-step on_phase behavior is covered separately in
tests/unit/test_arch_company_intelligence.py.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import pipeline.run as run_module
from pipeline.company_intelligence import run_company_intelligence


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


# =======================================================================
# M3D-B: company_intelligence ops_job_run telemetry
# =======================================================================


def _patch_other_steps_for_company_intelligence():
    """Mirrors _patch_other_steps(), but leaves run_company_intelligence
    unpatched by default (each test below patches/exercises it directly)
    and instead patches score_unscored_tenders, since AI scoring's own
    flag defaults off and would otherwise attempt real work."""
    return (
        patch(
            "pipeline.run.run_tender_data_pipeline",
            return_value={"status": "success"},
        ),
        patch("pipeline.run.score_unscored_tenders", return_value={}),
        patch("pipeline.run.run_arch_company_intelligence", return_value={}),
        patch("pipeline.run.init_db"),
    )


# ---------------------------------------------------------------------
# flag=false -- byte-equivalent call path, no telemetry writer touched
# ---------------------------------------------------------------------


def test_company_intelligence_flag_false_calls_with_no_on_phase_kwarg(monkeypatch):
    monkeypatch.delenv(
        run_module.COMPANY_INTELLIGENCE_JOB_RUN_TELEMETRY_FLAG, raising=False
    )
    session = MagicMock()
    captured_kwargs = {}

    def fake_run_company_intelligence(_session, **kwargs):
        captured_kwargs.update(kwargs)
        return {"companies_populated": 0}

    patches = list(_patch_other_steps_for_company_intelligence())
    patches.append(patch("pipeline.run.get_session", return_value=session))
    patches.append(
        patch("pipeline.run.run_company_intelligence", fake_run_company_intelligence)
    )
    with _Ctx(patches):
        exit_code = run_module.run_pipeline()

    assert exit_code == 0
    assert captured_kwargs == {}  # no on_phase kwarg passed at all


def test_company_intelligence_flag_false_calls_no_telemetry_writer_function_at_all(
    monkeypatch,
):
    monkeypatch.delenv(
        run_module.COMPANY_INTELLIGENCE_JOB_RUN_TELEMETRY_FLAG, raising=False
    )
    session = MagicMock()
    start_mock = MagicMock()
    finish_mock = MagicMock()

    patches = list(_patch_other_steps_for_company_intelligence())
    patches.append(patch("pipeline.run.get_session", return_value=session))
    patches.append(
        patch("pipeline.run._company_intelligence_telemetry_start", start_mock)
    )
    patches.append(
        patch("pipeline.run._company_intelligence_telemetry_finish", finish_mock)
    )
    patches.append(
        patch(
            "pipeline.run.run_company_intelligence",
            return_value={"companies_populated": 3},
        )
    )
    with _Ctx(patches):
        run_module.run_pipeline()

    start_mock.assert_not_called()
    finish_mock.assert_not_called()


# ---------------------------------------------------------------------
# flag=true -- success: one start, 5 step events/heartbeats, one finish
# ---------------------------------------------------------------------


def test_company_intelligence_flag_true_success_records_start_5_phases_and_finish_success(
    monkeypatch,
):
    monkeypatch.setenv(run_module.COMPANY_INTELLIGENCE_JOB_RUN_TELEMETRY_FLAG, "true")
    session = MagicMock()

    phases_recorded: list[str] = []

    def fake_phase(run_id, phase):
        assert run_id == "run-ci-123"
        phases_recorded.append(phase)

    def fake_run_company_intelligence(_session, *, on_phase=None):
        assert on_phase is not None
        for phase in (
            "populate",
            "google_enrich",
            "ai_analyze",
            "classify",
            "construction_tiers",
        ):
            on_phase(phase)
        return {
            "companies_populated": 10,
            "companies_google_enriched": 4,
            "companies_ai_analyzed": 4,
            "companies_classified": 10,
            "construction_tiers_updated": 10,
        }

    finish_mock = MagicMock()
    patches = list(_patch_other_steps_for_company_intelligence())
    patches.append(patch("pipeline.run.get_session", return_value=session))
    patches.append(
        patch(
            "pipeline.run._company_intelligence_telemetry_start",
            return_value="run-ci-123",
        )
    )
    patches.append(
        patch("pipeline.run._company_intelligence_telemetry_phase", fake_phase)
    )
    patches.append(
        patch("pipeline.run._company_intelligence_telemetry_finish", finish_mock)
    )
    patches.append(
        patch("pipeline.run.run_company_intelligence", fake_run_company_intelligence)
    )
    with _Ctx(patches):
        exit_code = run_module.run_pipeline()

    assert exit_code == 0
    assert phases_recorded == [
        "populate",
        "google_enrich",
        "ai_analyze",
        "classify",
        "construction_tiers",
    ]
    finish_mock.assert_called_once_with(
        "run-ci-123",
        status="success",
        counts={
            "companies_populated": 10,
            "companies_google_enriched": 4,
            "companies_ai_analyzed": 4,
            "companies_classified": 10,
            "construction_tiers_updated": 10,
        },
    )


def test_company_intelligence_counts_are_safe_flat_ints_no_raw_data(monkeypatch):
    """A return dict with extra/unexpected fields (which
    run_company_intelligence() does not currently produce, but might in
    the future) must never reach finish_job_run's counts -- only the
    five allowlisted keys."""
    monkeypatch.setenv(run_module.COMPANY_INTELLIGENCE_JOB_RUN_TELEMETRY_FLAG, "true")
    session = MagicMock()

    def fake_run_company_intelligence(_session, *, on_phase=None):
        return {
            "companies_populated": 1,
            "companies_google_enriched": 1,
            "companies_ai_analyzed": 1,
            "companies_classified": 1,
            "construction_tiers_updated": 1,
            "tier_counts": {"A": 1},  # nested -- must never leak into counts
            "reference_date": "2026-08-11",  # not in the allowlist
        }

    finish_mock = MagicMock()
    patches = list(_patch_other_steps_for_company_intelligence())
    patches.append(patch("pipeline.run.get_session", return_value=session))
    patches.append(
        patch(
            "pipeline.run._company_intelligence_telemetry_start",
            return_value="run-ci-safe",
        )
    )
    patches.append(
        patch("pipeline.run._company_intelligence_telemetry_phase", MagicMock())
    )
    patches.append(
        patch("pipeline.run._company_intelligence_telemetry_finish", finish_mock)
    )
    patches.append(
        patch("pipeline.run.run_company_intelligence", fake_run_company_intelligence)
    )
    with _Ctx(patches):
        run_module.run_pipeline()

    finish_mock.assert_called_once_with(
        "run-ci-safe",
        status="success",
        counts={
            "companies_populated": 1,
            "companies_google_enriched": 1,
            "companies_ai_analyzed": 1,
            "companies_classified": 1,
            "construction_tiers_updated": 1,
        },
    )


# ---------------------------------------------------------------------
# flag=true -- failed: finish failed, but (unlike M3D-A) the exception is
# swallowed and the pipeline continues to the next step -- company
# intelligence's pre-existing fail-and-continue behavior is unchanged.
# ---------------------------------------------------------------------


def test_company_intelligence_flag_true_exception_maps_to_failed_and_is_swallowed(
    monkeypatch,
):
    monkeypatch.setenv(run_module.COMPANY_INTELLIGENCE_JOB_RUN_TELEMETRY_FLAG, "true")
    session = MagicMock()

    def raising_run_company_intelligence(_session, *, on_phase=None):
        raise RuntimeError("boom: sk_live_should_never_leak")

    finish_mock = MagicMock()
    arch_mock = MagicMock(return_value={})
    patches = [
        patch(
            "pipeline.run.run_tender_data_pipeline",
            return_value={"status": "success"},
        ),
        patch("pipeline.run.score_unscored_tenders", return_value={}),
        patch("pipeline.run.run_arch_company_intelligence", arch_mock),
        patch("pipeline.run.init_db"),
        patch("pipeline.run.get_session", return_value=session),
        patch(
            "pipeline.run._company_intelligence_telemetry_start",
            return_value="run-ci-456",
        ),
        patch("pipeline.run._company_intelligence_telemetry_phase", MagicMock()),
        patch("pipeline.run._company_intelligence_telemetry_finish", finish_mock),
        patch(
            "pipeline.run.run_company_intelligence", raising_run_company_intelligence
        ),
    ]
    with _Ctx(patches):
        exit_code = run_module.run_pipeline()  # must NOT raise

    assert exit_code == 0
    finish_mock.assert_called_once_with(
        "run-ci-456", status="failed", raw_error="boom: sk_live_should_never_leak"
    )
    # Pre-existing fail-and-continue behavior preserved: arch company
    # intelligence still ran after company intelligence failed (unlike
    # M3D-A's ai_scoring, whose exception re-raises and aborts run_pipeline()
    # before reaching this step at all).
    arch_mock.assert_called_once()
    # get_session() is patched to return the same session for all three
    # post-import steps (ai_scoring, company_intelligence, arch) here, so
    # session.close() legitimately fires once per step -- 3 total --
    # rather than once, since (unlike the M3D-A propagating-exception
    # test) execution reaches all three.
    assert session.close.call_count == 3


def test_company_intelligence_flag_true_but_start_failed_still_calls_without_on_phase(
    monkeypatch,
):
    """Fail-open: if _company_intelligence_telemetry_start() itself
    returns None, the real work must still run, with the exact
    pre-M3D-B call signature (no on_phase), and no finish call is
    attempted (there is no run_id to finish)."""
    monkeypatch.setenv(run_module.COMPANY_INTELLIGENCE_JOB_RUN_TELEMETRY_FLAG, "true")
    session = MagicMock()
    captured_kwargs = {}

    def fake_run_company_intelligence(_session, **kwargs):
        captured_kwargs.update(kwargs)
        return {"companies_populated": 0}

    finish_mock = MagicMock()
    patches = list(_patch_other_steps_for_company_intelligence())
    patches.append(patch("pipeline.run.get_session", return_value=session))
    patches.append(
        patch("pipeline.run._company_intelligence_telemetry_start", return_value=None)
    )
    patches.append(
        patch("pipeline.run._company_intelligence_telemetry_finish", finish_mock)
    )
    patches.append(
        patch("pipeline.run.run_company_intelligence", fake_run_company_intelligence)
    )
    with _Ctx(patches):
        exit_code = run_module.run_pipeline()

    assert exit_code == 0
    assert captured_kwargs == {}
    finish_mock.assert_not_called()


# ---------------------------------------------------------------------
# fail-open at every telemetry boundary, including get_session() itself
# ---------------------------------------------------------------------


def test_company_intelligence_phase_get_session_failure_still_completes_once(
    monkeypatch, caplog
):
    monkeypatch.setenv(run_module.COMPANY_INTELLIGENCE_JOB_RUN_TELEMETRY_FLAG, "true")
    session = MagicMock()

    call_count = {"n": 0}
    real_get_session = MagicMock()

    def flaky_get_session():
        call_count["n"] += 1
        # First call: _company_intelligence_telemetry_start's own session -- succeeds.
        if call_count["n"] == 1:
            return real_get_session
        # Every phase call's own session -- raises.
        raise RuntimeError("db down")

    def fake_run_company_intelligence(_session, *, on_phase=None):
        on_phase("populate")
        on_phase("google_enrich")
        return {"companies_populated": 1}

    patches = list(_patch_other_steps_for_company_intelligence())
    patches.append(patch("pipeline.run.get_session", return_value=session))
    patches.append(patch("pipeline.run.start_job_run", return_value="run-ci-789"))
    finish_mock = MagicMock()
    patches.append(patch("pipeline.run.finish_job_run", finish_mock))
    patches.append(patch("db.connection.get_session", flaky_get_session))
    patches.append(
        patch("pipeline.run.run_company_intelligence", fake_run_company_intelligence)
    )
    with _Ctx(patches):
        with caplog.at_level("WARNING"):
            exit_code = run_module.run_pipeline()

    assert exit_code == 0
    assert "db down" not in caplog.text
    assert (
        "Company intelligence telemetry: failed to record phase=populate" in caplog.text
    )
    assert (
        "Company intelligence telemetry: failed to record phase=google_enrich"
        in caplog.text
    )
    finish_mock.assert_not_called()


def test_company_intelligence_finish_get_session_failure_still_completes_once(
    monkeypatch, caplog
):
    monkeypatch.setenv(run_module.COMPANY_INTELLIGENCE_JOB_RUN_TELEMETRY_FLAG, "true")
    session = MagicMock()

    def fake_run_company_intelligence(_session, *, on_phase=None):
        return {"companies_populated": 1}

    patches = list(_patch_other_steps_for_company_intelligence())
    patches.append(patch("pipeline.run.get_session", return_value=session))
    patches.append(
        patch(
            "pipeline.run._company_intelligence_telemetry_start",
            return_value="run-ci-999",
        )
    )
    patches.append(
        patch(
            "db.connection.get_session", MagicMock(side_effect=RuntimeError("db down"))
        )
    )
    patches.append(
        patch("pipeline.run.run_company_intelligence", fake_run_company_intelligence)
    )
    with _Ctx(patches):
        with caplog.at_level("WARNING"):
            exit_code = run_module.run_pipeline()

    assert exit_code == 0
    assert "db down" not in caplog.text
    assert (
        "Company intelligence telemetry: failed to finish job run tracking"
        in caplog.text
    )


# ---------------------------------------------------------------------
# on_phase fail-open INSIDE run_company_intelligence() itself (not just
# the telemetry wiring in pipeline/run.py) -- a raising callback must
# never stop or change the 5 business steps, their order, or the
# returned counts.
# ---------------------------------------------------------------------


def test_run_company_intelligence_on_phase_exception_does_not_stop_steps(caplog):
    call_order: list[str] = []

    def raising_on_phase(phase: str) -> None:
        call_order.append(phase)
        raise RuntimeError("callback exploded: sk_live_should_never_leak")

    patches = (
        patch(
            "pipeline.company_intelligence.populate_companies_from_permits",
            return_value=1,
        ),
        patch("pipeline.company_intelligence.enrich_companies_google", return_value=2),
        patch("pipeline.company_intelligence.analyze_companies_ai", return_value=3),
        patch("pipeline.company_intelligence.classify_companies", return_value=4),
        patch(
            "pipeline.company_intelligence.compute_construction_tiers",
            return_value={"companies_updated": 5},
        ),
    )
    with _Ctx(list(patches)):
        with caplog.at_level("WARNING"):
            result = run_company_intelligence(MagicMock(), on_phase=raising_on_phase)

    assert result == {
        "companies_populated": 1,
        "companies_google_enriched": 2,
        "companies_ai_analyzed": 3,
        "companies_classified": 4,
        "construction_tiers_updated": 5,
    }
    assert call_order == [
        "populate",
        "google_enrich",
        "ai_analyze",
        "classify",
        "construction_tiers",
    ]
    assert "callback exploded" not in caplog.text
    assert "sk_live_should_never_leak" not in caplog.text
    for phase in call_order:
        assert (
            f"[Company Intelligence] on_phase callback failed for phase={phase}"
            in caplog.text
        )


def test_run_company_intelligence_flag_off_byte_equivalent_default_none(monkeypatch):
    """Calling run_company_intelligence(session) with no on_phase at all
    (the flag-off call shape from pipeline/run.py) must behave exactly
    as before this change -- no _safe_call_on_phase invocation."""
    patches = (
        patch(
            "pipeline.company_intelligence.populate_companies_from_permits",
            return_value=1,
        ),
        patch("pipeline.company_intelligence.enrich_companies_google", return_value=2),
        patch("pipeline.company_intelligence.analyze_companies_ai", return_value=3),
        patch("pipeline.company_intelligence.classify_companies", return_value=4),
        patch(
            "pipeline.company_intelligence.compute_construction_tiers",
            return_value={"companies_updated": 5},
        ),
    )
    with _Ctx(list(patches)):
        result = run_company_intelligence(MagicMock())

    assert result == {
        "companies_populated": 1,
        "companies_google_enriched": 2,
        "companies_ai_analyzed": 3,
        "companies_classified": 4,
        "construction_tiers_updated": 5,
    }


# =======================================================================
# M3D-C: arch_company_intelligence ops_job_run telemetry
# =======================================================================


_ARCH_COMPANY_INTELLIGENCE_SUCCESS_RESULT = {
    "arch_companies_populated": 10,
    "arch_companies_houzz_scraped": 4,
    "arch_companies_aibc_verified": 3,
    "arch_companies_websites_researched": 2,
    "arch_companies_scores_backfilled": 10,
    "arch_companies_ai_analyzed": 8,
    "arch_companies_scores_refreshed": 6,
}

_ARCH_COMPANY_INTELLIGENCE_ALL_PHASES = (
    "populate",
    "scores_backfilled",
    "ai_analyzed",
    "houzz_scraped",
    "aibc_verified",
    "scores_refreshed",
    "websites_researched",
    "scores_refreshed_post_website",
    "scores_backfilled_final",
)


def _patch_other_steps_for_arch_company_intelligence():
    """Mirrors _patch_other_steps_for_company_intelligence(), but leaves
    run_arch_company_intelligence unpatched by default (each test below
    patches/exercises it directly) and patches both AI scoring and
    company intelligence to no-ops, since both default off and would
    otherwise attempt real work."""
    return (
        patch(
            "pipeline.run.run_tender_data_pipeline",
            return_value={"status": "success"},
        ),
        patch("pipeline.run.score_unscored_tenders", return_value={}),
        patch("pipeline.run.run_company_intelligence", return_value={}),
        patch("pipeline.run.init_db"),
    )


# ---------------------------------------------------------------------
# flag=false -- byte-equivalent call path, no telemetry writer touched
# ---------------------------------------------------------------------


def test_arch_company_intelligence_flag_false_calls_with_no_on_phase_kwarg(
    monkeypatch,
):
    monkeypatch.delenv(
        run_module.ARCH_COMPANY_INTELLIGENCE_JOB_RUN_TELEMETRY_FLAG, raising=False
    )
    session = MagicMock()
    captured_kwargs = {}

    def fake_run_arch_company_intelligence(_session, **kwargs):
        captured_kwargs.update(kwargs)
        return dict(_ARCH_COMPANY_INTELLIGENCE_SUCCESS_RESULT)

    patches = list(_patch_other_steps_for_arch_company_intelligence())
    patches.append(patch("pipeline.run.get_session", return_value=session))
    patches.append(
        patch(
            "pipeline.run.run_arch_company_intelligence",
            fake_run_arch_company_intelligence,
        )
    )
    with _Ctx(patches):
        exit_code = run_module.run_pipeline()

    assert exit_code == 0
    assert captured_kwargs == {}  # no on_phase kwarg passed at all


def test_arch_company_intelligence_flag_false_calls_no_telemetry_writer_function_at_all(
    monkeypatch,
):
    monkeypatch.delenv(
        run_module.ARCH_COMPANY_INTELLIGENCE_JOB_RUN_TELEMETRY_FLAG, raising=False
    )
    session = MagicMock()
    start_mock = MagicMock()
    finish_mock = MagicMock()

    patches = list(_patch_other_steps_for_arch_company_intelligence())
    patches.append(patch("pipeline.run.get_session", return_value=session))
    patches.append(
        patch("pipeline.run._arch_company_intelligence_telemetry_start", start_mock)
    )
    patches.append(
        patch("pipeline.run._arch_company_intelligence_telemetry_finish", finish_mock)
    )
    patches.append(
        patch(
            "pipeline.run.run_arch_company_intelligence",
            return_value=dict(_ARCH_COMPANY_INTELLIGENCE_SUCCESS_RESULT),
        )
    )
    with _Ctx(patches):
        run_module.run_pipeline()

    start_mock.assert_not_called()
    finish_mock.assert_not_called()


# ---------------------------------------------------------------------
# flag=true -- success: one start, 9 step events/heartbeats, one finish
# ---------------------------------------------------------------------


def test_arch_company_intelligence_flag_true_success_records_start_9_phases_and_finish_success(
    monkeypatch,
):
    monkeypatch.setenv(
        run_module.ARCH_COMPANY_INTELLIGENCE_JOB_RUN_TELEMETRY_FLAG, "true"
    )
    session = MagicMock()

    phases_recorded: list[str] = []

    def fake_phase(run_id, phase):
        assert run_id == "run-arch-123"
        phases_recorded.append(phase)

    def fake_run_arch_company_intelligence(_session, *, on_phase=None):
        assert on_phase is not None
        for phase in _ARCH_COMPANY_INTELLIGENCE_ALL_PHASES:
            on_phase(phase)
        return dict(_ARCH_COMPANY_INTELLIGENCE_SUCCESS_RESULT)

    finish_mock = MagicMock()
    patches = list(_patch_other_steps_for_arch_company_intelligence())
    patches.append(patch("pipeline.run.get_session", return_value=session))
    patches.append(
        patch(
            "pipeline.run._arch_company_intelligence_telemetry_start",
            return_value="run-arch-123",
        )
    )
    patches.append(
        patch("pipeline.run._arch_company_intelligence_telemetry_phase", fake_phase)
    )
    patches.append(
        patch("pipeline.run._arch_company_intelligence_telemetry_finish", finish_mock)
    )
    patches.append(
        patch(
            "pipeline.run.run_arch_company_intelligence",
            fake_run_arch_company_intelligence,
        )
    )
    with _Ctx(patches):
        exit_code = run_module.run_pipeline()

    assert exit_code == 0
    assert phases_recorded == list(_ARCH_COMPANY_INTELLIGENCE_ALL_PHASES)
    finish_mock.assert_called_once_with(
        "run-arch-123",
        status="success",
        counts=_ARCH_COMPANY_INTELLIGENCE_SUCCESS_RESULT,
    )


def test_arch_company_intelligence_counts_are_safe_flat_ints_allowlist_only(
    monkeypatch,
):
    """A return dict with extra/unexpected fields (which
    run_arch_company_intelligence() does not currently produce, but might
    in the future) must never reach finish_job_run's counts -- only the
    seven allowlisted keys."""
    monkeypatch.setenv(
        run_module.ARCH_COMPANY_INTELLIGENCE_JOB_RUN_TELEMETRY_FLAG, "true"
    )
    session = MagicMock()

    def fake_run_arch_company_intelligence(_session, *, on_phase=None):
        result = dict(_ARCH_COMPANY_INTELLIGENCE_SUCCESS_RESULT)
        result["unexpected_future_field"] = "not-an-allowlisted-count"
        return result

    finish_mock = MagicMock()
    patches = list(_patch_other_steps_for_arch_company_intelligence())
    patches.append(patch("pipeline.run.get_session", return_value=session))
    patches.append(
        patch(
            "pipeline.run._arch_company_intelligence_telemetry_start",
            return_value="run-arch-safe",
        )
    )
    patches.append(
        patch("pipeline.run._arch_company_intelligence_telemetry_phase", MagicMock())
    )
    patches.append(
        patch("pipeline.run._arch_company_intelligence_telemetry_finish", finish_mock)
    )
    patches.append(
        patch(
            "pipeline.run.run_arch_company_intelligence",
            fake_run_arch_company_intelligence,
        )
    )
    with _Ctx(patches):
        run_module.run_pipeline()

    finish_mock.assert_called_once_with(
        "run-arch-safe",
        status="success",
        counts=_ARCH_COMPANY_INTELLIGENCE_SUCCESS_RESULT,
    )


# ---------------------------------------------------------------------
# flag=true -- partial_failure: one or more of steps 2-9 report a
# *_failed phase, but run_arch_company_intelligence() still returns
# normally (its own pre-existing fail-and-continue behavior, unchanged)
# ---------------------------------------------------------------------


def test_arch_company_intelligence_one_step_failed_maps_to_partial_failure(
    monkeypatch,
):
    monkeypatch.setenv(
        run_module.ARCH_COMPANY_INTELLIGENCE_JOB_RUN_TELEMETRY_FLAG, "true"
    )
    session = MagicMock()

    phases_recorded: list[str] = []

    def fake_phase(run_id, phase):
        assert run_id == "run-arch-partial"
        phases_recorded.append(phase)

    def fake_run_arch_company_intelligence(_session, *, on_phase=None):
        assert on_phase is not None
        on_phase("populate")
        on_phase("scores_backfilled")
        on_phase("ai_analyzed")
        on_phase("houzz_scraped_failed")  # step 4 failed internally
        on_phase("aibc_verified")
        on_phase("scores_refreshed")
        on_phase("websites_researched")
        on_phase("scores_refreshed_post_website")
        on_phase("scores_backfilled_final")
        result = dict(_ARCH_COMPANY_INTELLIGENCE_SUCCESS_RESULT)
        result["arch_companies_houzz_scraped"] = 0  # pre-existing fallback value
        return result

    finish_mock = MagicMock()
    patches = list(_patch_other_steps_for_arch_company_intelligence())
    patches.append(patch("pipeline.run.get_session", return_value=session))
    patches.append(
        patch(
            "pipeline.run._arch_company_intelligence_telemetry_start",
            return_value="run-arch-partial",
        )
    )
    patches.append(
        patch("pipeline.run._arch_company_intelligence_telemetry_phase", fake_phase)
    )
    patches.append(
        patch("pipeline.run._arch_company_intelligence_telemetry_finish", finish_mock)
    )
    patches.append(
        patch(
            "pipeline.run.run_arch_company_intelligence",
            fake_run_arch_company_intelligence,
        )
    )
    with _Ctx(patches):
        exit_code = run_module.run_pipeline()

    assert exit_code == 0
    assert "houzz_scraped_failed" in phases_recorded
    finish_call = finish_mock.call_args
    assert finish_call.args == ("run-arch-partial",)
    assert finish_call.kwargs["status"] == "partial_failure"
    assert finish_call.kwargs["counts"]["arch_companies_houzz_scraped"] == 0


def test_arch_company_intelligence_multiple_steps_failed_still_one_partial_failure(
    monkeypatch,
):
    """Several *_failed phases in one run must still resolve to exactly
    one partial_failure finish call, not multiple finishes or a
    'more failed than others' distinction."""
    monkeypatch.setenv(
        run_module.ARCH_COMPANY_INTELLIGENCE_JOB_RUN_TELEMETRY_FLAG, "true"
    )
    session = MagicMock()

    def fake_run_arch_company_intelligence(_session, *, on_phase=None):
        on_phase("populate")
        on_phase("scores_backfilled_failed")
        on_phase("ai_analyzed_failed")
        on_phase("houzz_scraped")
        on_phase("aibc_verified_failed")
        on_phase("scores_refreshed")
        on_phase("websites_researched")
        on_phase("scores_refreshed_post_website")
        on_phase("scores_backfilled_final")
        return dict(_ARCH_COMPANY_INTELLIGENCE_SUCCESS_RESULT)

    finish_mock = MagicMock()
    patches = list(_patch_other_steps_for_arch_company_intelligence())
    patches.append(patch("pipeline.run.get_session", return_value=session))
    patches.append(
        patch(
            "pipeline.run._arch_company_intelligence_telemetry_start",
            return_value="run-arch-multi",
        )
    )
    patches.append(
        patch("pipeline.run._arch_company_intelligence_telemetry_phase", MagicMock())
    )
    patches.append(
        patch("pipeline.run._arch_company_intelligence_telemetry_finish", finish_mock)
    )
    patches.append(
        patch(
            "pipeline.run.run_arch_company_intelligence",
            fake_run_arch_company_intelligence,
        )
    )
    with _Ctx(patches):
        run_module.run_pipeline()

    finish_mock.assert_called_once()
    assert finish_mock.call_args.kwargs["status"] == "partial_failure"


# ---------------------------------------------------------------------
# flag=true -- failed: step 1's unwrapped exception (or anything outside
# the named steps) still maps to status="failed", exception swallowed,
# pipeline finishes (arch company intelligence is the last step)
# ---------------------------------------------------------------------


def test_arch_company_intelligence_flag_true_exception_maps_to_failed_and_is_swallowed(
    monkeypatch,
):
    monkeypatch.setenv(
        run_module.ARCH_COMPANY_INTELLIGENCE_JOB_RUN_TELEMETRY_FLAG, "true"
    )
    session = MagicMock()

    def raising_run_arch_company_intelligence(_session, *, on_phase=None):
        raise RuntimeError("boom: sk_live_should_never_leak")

    finish_mock = MagicMock()
    patches = list(_patch_other_steps_for_arch_company_intelligence())
    patches.append(patch("pipeline.run.get_session", return_value=session))
    patches.append(
        patch(
            "pipeline.run._arch_company_intelligence_telemetry_start",
            return_value="run-arch-456",
        )
    )
    patches.append(
        patch("pipeline.run._arch_company_intelligence_telemetry_phase", MagicMock())
    )
    patches.append(
        patch("pipeline.run._arch_company_intelligence_telemetry_finish", finish_mock)
    )
    patches.append(
        patch(
            "pipeline.run.run_arch_company_intelligence",
            raising_run_arch_company_intelligence,
        )
    )
    with _Ctx(patches):
        exit_code = run_module.run_pipeline()  # must NOT raise

    assert exit_code == 0
    finish_mock.assert_called_once_with(
        "run-arch-456", status="failed", raw_error="boom: sk_live_should_never_leak"
    )


def test_arch_company_intelligence_flag_true_but_start_failed_still_calls_without_on_phase(
    monkeypatch,
):
    """Fail-open: if _arch_company_intelligence_telemetry_start() itself
    returns None, the real work must still run, with the exact
    pre-M3D-C call signature (no on_phase), and no finish call is
    attempted (there is no run_id to finish)."""
    monkeypatch.setenv(
        run_module.ARCH_COMPANY_INTELLIGENCE_JOB_RUN_TELEMETRY_FLAG, "true"
    )
    session = MagicMock()
    captured_kwargs = {}

    def fake_run_arch_company_intelligence(_session, **kwargs):
        captured_kwargs.update(kwargs)
        return dict(_ARCH_COMPANY_INTELLIGENCE_SUCCESS_RESULT)

    finish_mock = MagicMock()
    patches = list(_patch_other_steps_for_arch_company_intelligence())
    patches.append(patch("pipeline.run.get_session", return_value=session))
    patches.append(
        patch(
            "pipeline.run._arch_company_intelligence_telemetry_start",
            return_value=None,
        )
    )
    patches.append(
        patch("pipeline.run._arch_company_intelligence_telemetry_finish", finish_mock)
    )
    patches.append(
        patch(
            "pipeline.run.run_arch_company_intelligence",
            fake_run_arch_company_intelligence,
        )
    )
    with _Ctx(patches):
        exit_code = run_module.run_pipeline()

    assert exit_code == 0
    assert captured_kwargs == {}
    finish_mock.assert_not_called()


# ---------------------------------------------------------------------
# fail-open at every telemetry boundary, including get_session() itself
# ---------------------------------------------------------------------


def test_arch_company_intelligence_phase_get_session_failure_still_completes_once(
    monkeypatch, caplog
):
    monkeypatch.setenv(
        run_module.ARCH_COMPANY_INTELLIGENCE_JOB_RUN_TELEMETRY_FLAG, "true"
    )
    session = MagicMock()

    call_count = {"n": 0}
    real_get_session = MagicMock()

    def flaky_get_session():
        call_count["n"] += 1
        # First call: _arch_company_intelligence_telemetry_start's own
        # session -- succeeds.
        if call_count["n"] == 1:
            return real_get_session
        # Every phase call's own session -- raises.
        raise RuntimeError("db down")

    def fake_run_arch_company_intelligence(_session, *, on_phase=None):
        on_phase("populate")
        on_phase("scores_backfilled")
        return dict(_ARCH_COMPANY_INTELLIGENCE_SUCCESS_RESULT)

    patches = list(_patch_other_steps_for_arch_company_intelligence())
    patches.append(patch("pipeline.run.get_session", return_value=session))
    patches.append(patch("pipeline.run.start_job_run", return_value="run-arch-789"))
    finish_mock = MagicMock()
    patches.append(patch("pipeline.run.finish_job_run", finish_mock))
    patches.append(patch("db.connection.get_session", flaky_get_session))
    patches.append(
        patch(
            "pipeline.run.run_arch_company_intelligence",
            fake_run_arch_company_intelligence,
        )
    )
    with _Ctx(patches):
        with caplog.at_level("WARNING"):
            exit_code = run_module.run_pipeline()

    assert exit_code == 0
    assert "db down" not in caplog.text
    assert (
        "Arch company intelligence telemetry: failed to record phase=populate"
        in caplog.text
    )
    assert (
        "Arch company intelligence telemetry: failed to record phase=scores_backfilled"
        in caplog.text
    )
    finish_mock.assert_not_called()


def test_arch_company_intelligence_finish_get_session_failure_still_completes_once(
    monkeypatch, caplog
):
    monkeypatch.setenv(
        run_module.ARCH_COMPANY_INTELLIGENCE_JOB_RUN_TELEMETRY_FLAG, "true"
    )
    session = MagicMock()

    def fake_run_arch_company_intelligence(_session, *, on_phase=None):
        return dict(_ARCH_COMPANY_INTELLIGENCE_SUCCESS_RESULT)

    patches = list(_patch_other_steps_for_arch_company_intelligence())
    patches.append(patch("pipeline.run.get_session", return_value=session))
    patches.append(
        patch(
            "pipeline.run._arch_company_intelligence_telemetry_start",
            return_value="run-arch-999",
        )
    )
    patches.append(
        patch(
            "db.connection.get_session", MagicMock(side_effect=RuntimeError("db down"))
        )
    )
    patches.append(
        patch(
            "pipeline.run.run_arch_company_intelligence",
            fake_run_arch_company_intelligence,
        )
    )
    with _Ctx(patches):
        with caplog.at_level("WARNING"):
            exit_code = run_module.run_pipeline()

    assert exit_code == 0
    assert "db down" not in caplog.text
    assert (
        "Arch company intelligence telemetry: failed to finish job run tracking"
        in caplog.text
    )


def test_arch_company_intelligence_start_get_session_failure_still_runs_once(
    monkeypatch, caplog
):
    monkeypatch.setenv(
        run_module.ARCH_COMPANY_INTELLIGENCE_JOB_RUN_TELEMETRY_FLAG, "true"
    )
    session = MagicMock()
    call_kwargs_list: list[dict] = []

    def fake_run_arch_company_intelligence(_session, **kwargs):
        call_kwargs_list.append(kwargs)
        return dict(_ARCH_COMPANY_INTELLIGENCE_SUCCESS_RESULT)

    patches = list(_patch_other_steps_for_arch_company_intelligence())
    patches.append(patch("pipeline.run.get_session", return_value=session))
    patches.append(
        patch(
            "db.connection.get_session", MagicMock(side_effect=RuntimeError("db down"))
        )
    )
    patches.append(
        patch(
            "pipeline.run.run_arch_company_intelligence",
            fake_run_arch_company_intelligence,
        )
    )
    with _Ctx(patches):
        with caplog.at_level("WARNING"):
            exit_code = run_module.run_pipeline()

    assert exit_code == 0
    assert len(call_kwargs_list) == 1
    assert call_kwargs_list[0] == {}  # no on_phase -- start failed
    assert "db down" not in caplog.text
    assert (
        "Arch company intelligence telemetry: failed to start job run tracking"
        in caplog.text
    )


# ---------------------------------------------------------------------
# source is always None for arch company intelligence (see pipeline/run.py
# module docstring -- honest choice since most steps are not permit-derived)
# ---------------------------------------------------------------------


def test_arch_company_intelligence_telemetry_start_uses_source_none(monkeypatch):
    monkeypatch.setenv(
        run_module.ARCH_COMPANY_INTELLIGENCE_JOB_RUN_TELEMETRY_FLAG, "true"
    )
    start_job_run_mock = MagicMock(return_value="run-arch-source")
    session = MagicMock()
    # _arch_company_intelligence_telemetry_start()'s OWN session (acquired
    # via call_with_telemetry_session -> db.connection.get_session()) is
    # distinct from run_pipeline()'s own session (pipeline.run.get_session,
    # patched above) -- must also be patched to a working mock, or
    # call_with_telemetry_session's real get_session() raises and
    # start_job_run is never reached (fail-open, proven separately by the
    # test_arch_company_intelligence_start_get_session_failure_still_runs_once
    # test above).
    telemetry_session = MagicMock()

    patches = list(_patch_other_steps_for_arch_company_intelligence())
    patches.append(patch("pipeline.run.get_session", return_value=session))
    patches.append(patch("db.connection.get_session", return_value=telemetry_session))
    patches.append(patch("pipeline.run.start_job_run", start_job_run_mock))
    patches.append(
        patch(
            "pipeline.run.run_arch_company_intelligence",
            return_value=dict(_ARCH_COMPANY_INTELLIGENCE_SUCCESS_RESULT),
        )
    )
    with _Ctx(patches):
        run_module.run_pipeline()

    start_job_run_mock.assert_called_once_with(
        telemetry_session,
        job_type="arch_company_intelligence",
        trigger="scheduler",
        source=None,
    )
