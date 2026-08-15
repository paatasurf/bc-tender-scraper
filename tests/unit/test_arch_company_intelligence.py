"""Tests for pipeline/arch_company_intelligence.py's M3D-C on_phase
callback -- the module's own step order, fallback values, and
fail-and-continue behavior, independent of pipeline/run.py's telemetry
wiring (see tests/unit/test_run_pipeline.py for that layer).

Every test here patches populate_arch_companies_from_permits /
backfill_arch_reliability_scores / analyze_arch_companies_ai /
scrape_arch_houzz / scrape_arch_aibc / _refresh_arch_reliability_scores /
research_arch_websites so only run_arch_company_intelligence()'s own
orchestration logic actually executes.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from pipeline.arch_company_intelligence import run_arch_company_intelligence


def _patch_all_steps(**overrides):
    """Patches every step run_arch_company_intelligence() calls (including
    the three imported inside the function body) with a success-shaped
    default, letting individual tests override any subset via **overrides
    (values are passed straight to unittest.mock.patch's `new` param, so
    pass a MagicMock(side_effect=...) to make a step raise)."""
    defaults = {
        "pipeline.arch_company_intelligence.populate_arch_companies_from_permits": MagicMock(
            return_value=1
        ),
        "pipeline.arch_company_intelligence.backfill_arch_reliability_scores": MagicMock(
            return_value=2
        ),
        "pipeline.arch_company_intelligence.analyze_arch_companies_ai": MagicMock(
            return_value=3
        ),
        "pipeline.scrape_arch_houzz.scrape_arch_houzz": MagicMock(return_value=4),
        "pipeline.scrape_arch_aibc.scrape_arch_aibc": MagicMock(return_value=5),
        "pipeline.arch_company_intelligence._refresh_arch_reliability_scores": MagicMock(
            return_value=6
        ),
        "pipeline.research_arch_websites.research_arch_websites": MagicMock(
            return_value=7
        ),
    }
    defaults.update(overrides)
    return [patch(target, value) for target, value in defaults.items()]


class _Ctx:
    def __init__(self, patches):
        self._patches = patches

    def __enter__(self):
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *exc_info):
        for p in reversed(self._patches):
            p.stop()


# ---------------------------------------------------------------------
# flag off / no on_phase -- byte-equivalent to before M3D-C
# ---------------------------------------------------------------------


def test_no_on_phase_kwarg_is_a_complete_no_op():
    """Calling run_arch_company_intelligence(session) with no on_phase at
    all (the flag-off call shape from pipeline/run.py, and the exact
    call the manual/n8n run_arch_company_intelligence_step() still uses)
    must behave exactly as before this change."""
    with _Ctx(_patch_all_steps()):
        result = run_arch_company_intelligence(MagicMock())

    assert result == {
        "arch_companies_populated": 1,
        "arch_companies_houzz_scraped": 4,
        "arch_companies_aibc_verified": 5,
        "arch_companies_websites_researched": 7,
        "arch_companies_scores_backfilled": 2,
        "arch_companies_ai_analyzed": 3,
        "arch_companies_scores_refreshed": 12,  # 6 (first refresh) + 6 (post-website)
    }


# ---------------------------------------------------------------------
# on_phase, all steps succeed -- 9 success phases, in order, none *_failed
# ---------------------------------------------------------------------


def test_on_phase_all_success_records_nine_phases_in_order_no_failed():
    phases: list[str] = []

    with _Ctx(_patch_all_steps()):
        result = run_arch_company_intelligence(MagicMock(), on_phase=phases.append)

    assert phases == [
        "populate",
        "scores_backfilled",
        "ai_analyzed",
        "houzz_scraped",
        "aibc_verified",
        "scores_refreshed",
        "websites_researched",
        "scores_refreshed_post_website",
        "scores_backfilled_final",
    ]
    assert not any(p.endswith("_failed") for p in phases)
    assert result == {
        "arch_companies_populated": 1,
        "arch_companies_houzz_scraped": 4,
        "arch_companies_aibc_verified": 5,
        "arch_companies_websites_researched": 7,
        "arch_companies_scores_backfilled": 2,
        "arch_companies_ai_analyzed": 3,
        "arch_companies_scores_refreshed": 12,
    }


# ---------------------------------------------------------------------
# on_phase, one of steps 2-9 raises -- fallback value unchanged, *_failed
# phase reported, remaining steps still run in order
# ---------------------------------------------------------------------


def test_houzz_scrape_failure_reports_failed_phase_and_continues():
    phases: list[str] = []
    overrides = {
        "pipeline.scrape_arch_houzz.scrape_arch_houzz": MagicMock(
            side_effect=RuntimeError("houzz down")
        ),
    }

    with _Ctx(_patch_all_steps(**overrides)):
        result = run_arch_company_intelligence(MagicMock(), on_phase=phases.append)

    assert phases == [
        "populate",
        "scores_backfilled",
        "ai_analyzed",
        "houzz_scraped_failed",
        "aibc_verified",
        "scores_refreshed",
        "websites_researched",
        "scores_refreshed_post_website",
        "scores_backfilled_final",
    ]
    # Pre-existing fallback: houzz_scraped falls back to 0 on failure --
    # unchanged by M3D-C.
    assert result["arch_companies_houzz_scraped"] == 0
    # Every other step still ran with its normal value.
    assert result["arch_companies_populated"] == 1
    assert result["arch_companies_ai_analyzed"] == 3
    assert result["arch_companies_aibc_verified"] == 5
    assert result["arch_companies_websites_researched"] == 7


def test_ai_analysis_failure_falls_back_to_scores_backfilled_unchanged():
    """Pre-existing fallback for step 3: ai_analyzed falls back to
    scores_backfilled's own value on failure -- must be unchanged by
    M3D-C."""
    phases: list[str] = []
    overrides = {
        "pipeline.arch_company_intelligence.analyze_arch_companies_ai": MagicMock(
            side_effect=RuntimeError("claude down")
        ),
    }

    with _Ctx(_patch_all_steps(**overrides)):
        result = run_arch_company_intelligence(MagicMock(), on_phase=phases.append)

    assert "ai_analyzed_failed" in phases
    assert (
        result["arch_companies_ai_analyzed"]
        == result["arch_companies_scores_backfilled"]
        == 2
    )


def test_multiple_step_failures_all_reported_as_failed_phases():
    phases: list[str] = []
    overrides = {
        "pipeline.scrape_arch_houzz.scrape_arch_houzz": MagicMock(
            side_effect=RuntimeError("houzz down")
        ),
        "pipeline.scrape_arch_aibc.scrape_arch_aibc": MagicMock(
            side_effect=RuntimeError("aibc down")
        ),
        "pipeline.research_arch_websites.research_arch_websites": MagicMock(
            side_effect=RuntimeError("website research down")
        ),
    }

    with _Ctx(_patch_all_steps(**overrides)):
        result = run_arch_company_intelligence(MagicMock(), on_phase=phases.append)

    failed_phases = [p for p in phases if p.endswith("_failed")]
    assert failed_phases == [
        "houzz_scraped_failed",
        "aibc_verified_failed",
        "websites_researched_failed",
    ]
    assert result["arch_companies_houzz_scraped"] == 0
    assert result["arch_companies_aibc_verified"] == 0
    assert result["arch_companies_websites_researched"] == 0
    # Score-refresh steps still ran (they don't depend on Houzz/website
    # actually having produced anything -- pre-existing behavior).
    assert "scores_refreshed" in phases
    assert "scores_refreshed_post_website" in phases


def test_final_score_backfill_failure_reports_failed_phase():
    """Step 9 (final backfill_arch_reliability_scores call) has its return
    value discarded either way, but a failure must still surface as
    scores_backfilled_final_failed."""
    phases: list[str] = []
    call_count = {"n": 0}

    def flaky_backfill(_session):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return 2  # first call (step 2) succeeds
        raise RuntimeError("final backfill exploded")  # second call (step 9) fails

    overrides = {
        "pipeline.arch_company_intelligence.backfill_arch_reliability_scores": flaky_backfill,
    }

    with _Ctx(_patch_all_steps(**overrides)):
        result = run_arch_company_intelligence(MagicMock(), on_phase=phases.append)

    assert "scores_backfilled_final_failed" in phases
    assert phases[-1] == "scores_backfilled_final_failed"
    # Everything computed before the final (discarded-return-value) step
    # is unaffected.
    assert result["arch_companies_scores_backfilled"] == 2


# ---------------------------------------------------------------------
# step 1 (populate) has no except-block -- unchanged; its exception still
# propagates out of run_arch_company_intelligence() entirely
# ---------------------------------------------------------------------


def test_populate_failure_propagates_and_no_phase_recorded():
    phases: list[str] = []
    overrides = {
        "pipeline.arch_company_intelligence.populate_arch_companies_from_permits": MagicMock(
            side_effect=RuntimeError("permits query failed")
        ),
    }

    with _Ctx(_patch_all_steps(**overrides)):
        try:
            run_arch_company_intelligence(MagicMock(), on_phase=phases.append)
            raised = False
        except RuntimeError:
            raised = True

    assert raised
    assert phases == []  # not even "populate" -- the exception fired first


# ---------------------------------------------------------------------
# on_phase itself raising must never affect steps, order, fallback
# values, or the returned dict (fail-open, matches
# pipeline.company_intelligence's own on_phase contract)
# ---------------------------------------------------------------------


def test_on_phase_exception_does_not_stop_steps_or_change_result(caplog):
    call_order: list[str] = []

    def raising_on_phase(phase: str) -> None:
        call_order.append(phase)
        raise RuntimeError("callback exploded: sk_live_should_never_leak")

    with _Ctx(_patch_all_steps()):
        with caplog.at_level("WARNING"):
            result = run_arch_company_intelligence(
                MagicMock(), on_phase=raising_on_phase
            )

    assert result == {
        "arch_companies_populated": 1,
        "arch_companies_houzz_scraped": 4,
        "arch_companies_aibc_verified": 5,
        "arch_companies_websites_researched": 7,
        "arch_companies_scores_backfilled": 2,
        "arch_companies_ai_analyzed": 3,
        "arch_companies_scores_refreshed": 12,
    }
    assert call_order == [
        "populate",
        "scores_backfilled",
        "ai_analyzed",
        "houzz_scraped",
        "aibc_verified",
        "scores_refreshed",
        "websites_researched",
        "scores_refreshed_post_website",
        "scores_backfilled_final",
    ]
    assert "callback exploded" not in caplog.text
    assert "sk_live_should_never_leak" not in caplog.text
    for phase in call_order:
        assert (
            f"[ArchCompanies] on_phase callback failed for phase={phase}" in caplog.text
        )
