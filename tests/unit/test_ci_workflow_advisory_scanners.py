"""Structural regression test for .github/workflows/quality-gate.yml's
advisory-vs-required scanner split.

mypy, Semgrep, and Schemathesis are advisory: they must always run, always
publish findings/artifacts and a job-summary warning, but a finding must
never fail the job (via `continue-on-error: true` on the scan step), never
fail the overall workflow run, and never block a deploy. The required
Quality Gate stays exactly {ruff, black, pip-audit, pytest, opencode-review,
enrichment-worker-tests}, and Deploy must depend on nothing but Quality Gate.

Parses the actual YAML (not a copy) so this test breaks the moment someone
edits the workflow in a way that regresses any of these properties --
same "exercise the real thing, not a duplicate" principle as
tests/unit/test_migration_028_public_id_backfill.py's BACKFILL_SQL
extraction.
"""

from __future__ import annotations

from pathlib import Path

import yaml

WORKFLOW_PATH = (
    Path(__file__).resolve().parents[2] / ".github" / "workflows" / "quality-gate.yml"
)

REQUIRED_QUALITY_GATE_JOBS = [
    "ruff",
    "black",
    "pip-audit",
    "pytest",
    "opencode-review",
    "enrichment-worker-tests",
]
ADVISORY_JOBS = ["mypy", "semgrep", "schemathesis"]


def _load_workflow() -> dict:
    with WORKFLOW_PATH.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def _steps_with_continue_on_error(job: dict) -> set[str]:
    return {
        step["id"]
        for step in job["steps"]
        if step.get("id") and step.get("continue-on-error") is True
    }


def test_workflow_file_exists_and_parses():
    assert WORKFLOW_PATH.is_file()
    workflow = _load_workflow()
    assert "jobs" in workflow


def test_quality_gate_needs_is_exactly_the_required_five():
    workflow = _load_workflow()
    assert workflow["jobs"]["quality-gate"]["needs"] == REQUIRED_QUALITY_GATE_JOBS


def test_advisory_jobs_are_not_in_quality_gate_needs():
    workflow = _load_workflow()
    needs = workflow["jobs"]["quality-gate"]["needs"]
    for job in ADVISORY_JOBS:
        assert job not in needs, f"{job} must stay out of quality-gate's needs"


def test_deploy_depends_only_on_quality_gate():
    workflow = _load_workflow()
    assert workflow["jobs"]["deploy"]["needs"] == ["quality-gate"]


def test_deploy_condition_only_references_quality_gate_result():
    """Guards against a future edit adding e.g.
    `&& needs.mypy.result == 'success'` to deploy's `if:` condition, which
    would let an advisory finding block deploys even without being added
    to `needs:`."""
    workflow = _load_workflow()
    condition = workflow["jobs"]["deploy"]["if"]
    for job in ADVISORY_JOBS:
        assert f"needs.{job}." not in condition


def test_mypy_scan_step_is_continue_on_error():
    workflow = _load_workflow()
    ids = _steps_with_continue_on_error(workflow["jobs"]["mypy"])
    assert "typecheck" in ids


def test_semgrep_scan_step_is_continue_on_error():
    workflow = _load_workflow()
    ids = _steps_with_continue_on_error(workflow["jobs"]["semgrep"])
    assert "semgrepscan" in ids


def test_schemathesis_scan_step_is_continue_on_error():
    workflow = _load_workflow()
    ids = _steps_with_continue_on_error(workflow["jobs"]["schemathesis"])
    assert "schemathesis_run" in ids


def test_semgrep_scan_step_captures_stderr_for_summary():
    """Semgrep's human-readable "Findings: N" summary goes to stderr, not
    stdout -- confirmed by reports/semgrep.txt (fed only by stdout via
    `tee`) being empty in CI despite the console log showing findings.
    The scan step's run script must merge stderr into the piped stream
    (`2>&1` before the pipe) so the Advisory summary step's grep against
    reports/semgrep.txt can actually find the finding count instead of
    falling back to a generic "Semgrep reported findings" message."""
    workflow = _load_workflow()
    steps = workflow["jobs"]["semgrep"]["steps"]
    scan_step = next(s for s in steps if s.get("id") == "semgrepscan")
    run_text = scan_step["run"]
    assert "2>&1" in run_text
    # Must appear before the pipe to tee, not as some unrelated redirect.
    pipe_index = run_text.index("| tee")
    redirect_index = run_text.index("2>&1")
    assert redirect_index < pipe_index


def test_gitleaks_is_not_advisory():
    """Gitleaks is the one existing new-in-Phase-1 scanner that is a real,
    hard failure today (no continue-on-error) -- confirms this test isn't
    accidentally asserting continue-on-error universally."""
    workflow = _load_workflow()
    gitleaks_steps = workflow["jobs"]["gitleaks"]["steps"]
    assert not any(step.get("continue-on-error") is True for step in gitleaks_steps)


def test_advisory_jobs_always_upload_their_report_artifact():
    """Each advisory job's report upload must run regardless of the scan
    step's outcome (if: always()) -- otherwise a failing scan would also
    lose its own findings artifact."""
    workflow = _load_workflow()
    for job_name in ADVISORY_JOBS:
        steps = workflow["jobs"][job_name]["steps"]
        upload_steps = [
            step
            for step in steps
            if step.get("uses", "").startswith("actions/upload-artifact")
        ]
        assert upload_steps, f"{job_name} has no upload-artifact step"
        for step in upload_steps:
            assert step.get("if") == "always()", (
                f"{job_name}'s {step.get('name')} step must run "
                "unconditionally (if: always()) so a scan failure doesn't "
                "also lose the findings artifact"
            )


def test_advisory_jobs_have_a_summary_step_that_always_runs():
    """Each advisory job must have its own "Advisory summary" step, running
    unconditionally, that writes to $GITHUB_STEP_SUMMARY and emits a
    ::warning:: annotation on failure -- the actual "show a warning in
    summary" requirement, not just "don't fail the job"."""
    workflow = _load_workflow()
    for job_name in ADVISORY_JOBS:
        steps = workflow["jobs"][job_name]["steps"]
        summary_steps = [s for s in steps if s.get("name") == "Advisory summary"]
        assert summary_steps, f"{job_name} has no 'Advisory summary' step"
        summary_step = summary_steps[0]
        assert summary_step.get("if") == "always()"
        run_text = summary_step.get("run", "")
        assert "GITHUB_STEP_SUMMARY" in run_text
        assert "::warning::" in run_text
