"""Daily pipeline orchestration.

Scheduled path (production, no manual intervention):
  APScheduler (api.main lifespan)
    → pipeline.scheduler._scheduled_pipeline_run()
    → pipeline.executor.start_pipeline_subprocess()
    → run_pipeline.py (file lock)
    → pipeline.run.run_pipeline()
       1. pipeline.tender_data_pipeline.run_tender_data_pipeline()
          — tender scrapers → CSV verify → import → DB verify
       2. pipeline.ai_scoring.score_unscored_tenders() (optional)
       3. pipeline.company_intelligence.run_company_intelligence()
       4. pipeline.arch_company_intelligence.run_arch_company_intelligence()

(M3D-A) Step 2 optionally persists run history to ops_job_runs /
ops_job_run_events (migration 033, pipeline/job_run.py), gated by
ENABLE_AI_SCORING_JOB_RUN_TELEMETRY (default false). With the flag off,
score_unscored_tenders(session) is called with the exact same signature
as before this change -- no on_phase kwarg, no extra get_session() call,
no ops_job_run* write of any kind. This does not (yet) apply to steps 3-4,
or to the manual/n8n endpoints (/internal/ai-scoring,
/api/pipeline/run-ai-scoring) -- see the M3D audit for the planned
follow-up phases.
"""

from config.env import env_flag
from db.connection import get_session, init_db
from pipeline.ai_scoring import score_unscored_tenders
from pipeline.arch_company_intelligence import run_arch_company_intelligence
from pipeline.company_intelligence import run_company_intelligence
from pipeline.job_run import (
    finish_job_run,
    heartbeat_job_run,
    record_job_step,
    start_job_run,
)
from pipeline.job_run_telemetry import call_with_telemetry_session
from pipeline.tender_data_pipeline import run_tender_data_pipeline

AI_SCORING_JOB_RUN_TELEMETRY_FLAG = "ENABLE_AI_SCORING_JOB_RUN_TELEMETRY"
AI_SCORING_JOB_RUN_JOB_TYPE = "ai_scoring"
_AI_SCORING_TELEMETRY_LOG_LABEL = "AI scoring telemetry"

# Flat int fields already present, unchanged, in score_unscored_tenders()'s
# own return dict -- see pipeline/ai_scoring.py. Deliberately an explicit
# allowlist, not a blanket pass-through: this is the boundary that keeps
# ops_job_runs.counts safe even if that dict ever grows an unexpected
# field in the future.
_AI_SCORING_COUNT_KEYS = (
    "tenders_scored",
    "federal_gov_tenders_scored",
    "merx_tenders_scored",
    "arch_tenders_scored",
    "bidcentral_tenders_scored",
    "commercial_tenders_scored",
    "tenders_budgeted",
    "federal_gov_tenders_budgeted",
    "merx_tenders_budgeted",
    "arch_tenders_budgeted",
    "commercial_tenders_budgeted",
    "bidcentral_tenders_budgeted",
    "total_tenders_scored",
    "total_tenders_budgeted",
    "batch_limit_per_table",
)
# The one nested field in that dict -- "backlog": {federal_gov: int,
# merx_provincial: int, commercial_tenders: int, arch_tenders: int} --
# flattened to backlog_<key> below rather than dropped (still all flat
# ints) or passed through as-is (ops_job_runs.counts must be a flat JSON
# object -- see pipeline/job_run.py::validate_counts()).
_AI_SCORING_BACKLOG_KEYS = (
    "federal_gov",
    "merx_provincial",
    "commercial_tenders",
    "arch_tenders",
)


def ai_scoring_job_run_telemetry_enabled() -> bool:
    """Read-only feature-flag check, same "1"/"true"/"yes" convention as
    every other flag in this repo. False by default."""
    return env_flag(AI_SCORING_JOB_RUN_TELEMETRY_FLAG, default=False)


def _safe_ai_scoring_counts(result: dict) -> dict[str, int]:
    """Allowlisted, flattened counts only -- never the nested `backlog`
    object itself, never a prompt, an AI response, or a tender/company
    record. None of those exist in score_unscored_tenders()'s return
    value in the first place; this function only narrows further."""
    counts: dict[str, int] = {
        key: result[key] for key in _AI_SCORING_COUNT_KEYS if key in result
    }
    backlog = result.get("backlog") or {}
    for key in _AI_SCORING_BACKLOG_KEYS:
        if key in backlog:
            counts[f"backlog_{key}"] = backlog[key]
    return counts


def _ai_scoring_telemetry_start() -> str | None:
    def _do(session: object) -> str:
        return start_job_run(
            session,
            job_type=AI_SCORING_JOB_RUN_JOB_TYPE,
            trigger="scheduler",
            source=None,
        )

    return call_with_telemetry_session(
        _do,
        log_label=_AI_SCORING_TELEMETRY_LOG_LABEL,
        failure_message="failed to start job run tracking",
    )


def _ai_scoring_telemetry_phase(run_id: str, phase: str) -> None:
    def _do(session: object) -> None:
        record_job_step(session, run_id, event_type="step_completed", step=phase)
        heartbeat_job_run(session, run_id)

    call_with_telemetry_session(
        _do,
        log_label=_AI_SCORING_TELEMETRY_LOG_LABEL,
        failure_message=f"failed to record phase={phase}",
    )


def _ai_scoring_telemetry_finish(
    run_id: str,
    *,
    status: str,
    counts: dict[str, int] | None = None,
    raw_error: str | None = None,
) -> None:
    def _do(session: object) -> None:
        finish_job_run(
            session, run_id, status=status, counts=counts, raw_error=raw_error
        )

    call_with_telemetry_session(
        _do,
        log_label=_AI_SCORING_TELEMETRY_LOG_LABEL,
        failure_message="failed to finish job run tracking",
    )


def run_pipeline() -> int:
    print("[Pipeline] Starting deterministic tender data pipeline...")
    try:
        summary = run_tender_data_pipeline()
    except Exception as exc:
        print(f"[Pipeline] Tender data pipeline failed: {exc}")
        return 1

    print(f"[Pipeline] Tender data pipeline summary: {summary.get('status')}")

    print("[Pipeline] Post-import enrichment...")
    init_db()
    session = get_session()
    try:
        if env_flag("PIPELINE_SKIP_AI_SCORING"):
            print("[Pipeline] Skipping AI scoring (PIPELINE_SKIP_AI_SCORING=true)")
        elif ai_scoring_job_run_telemetry_enabled():
            telemetry_run_id = _ai_scoring_telemetry_start()
            try:
                if telemetry_run_id is not None:

                    def on_phase(phase: str) -> None:
                        _ai_scoring_telemetry_phase(telemetry_run_id, phase)

                    result = score_unscored_tenders(session, on_phase=on_phase)
                else:
                    # Telemetry start failed (fail-open): call with the
                    # exact pre-M3D-A signature -- no on_phase kwarg at
                    # all -- so this path is byte-for-byte the same call
                    # as when the flag is off.
                    result = score_unscored_tenders(session)
            except Exception as exc:
                if telemetry_run_id is not None:
                    _ai_scoring_telemetry_finish(
                        telemetry_run_id, status="failed", raw_error=str(exc)
                    )
                raise
            else:
                if telemetry_run_id is not None:
                    _ai_scoring_telemetry_finish(
                        telemetry_run_id,
                        status="success",
                        counts=_safe_ai_scoring_counts(result),
                    )
        else:
            # Flag off: byte-for-byte the same call as before M3D-A -- no
            # on_phase kwarg, no extra get_session() call, no ops_job_run*
            # write of any kind.
            score_unscored_tenders(session)
    finally:
        session.close()

    print("[Pipeline] Running company intelligence...")
    session = get_session()
    try:
        run_company_intelligence(session)
    except Exception as exc:
        print(f"[Pipeline] Company intelligence failed: {exc}")
    finally:
        session.close()

    print("[Pipeline] Running architecture company intelligence...")
    session = get_session()
    try:
        run_arch_company_intelligence(session)
    except Exception as exc:
        print(f"[Pipeline] Architecture company intelligence failed: {exc}")
    finally:
        session.close()

    print("[Pipeline] Complete")
    return 0
